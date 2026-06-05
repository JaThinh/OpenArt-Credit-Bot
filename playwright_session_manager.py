"""Async Playwright session lifecycle helpers for worker pools."""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from functools import partial
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)


@dataclass
class AutomationTask:
    index: int
    target_url: str
    network_payload: Optional[dict[str, Any]] = None
    record_data: Optional[dict[str, Any]] = None


@dataclass
class WorkerSandboxSession:
    current_dir: str
    context: BrowserContext
    page: Page
    browser: Optional[Browser] = None


@dataclass
class BrowserSessionResult:
    success: bool
    worker_id: int
    task_index: int
    step: str
    title: str = ""
    error: Optional[str] = None


SessionHandler = Callable[[WorkerSandboxSession, AutomationTask, dict[str, Any], int], Awaitable[Any]]
SyncFunction = Callable[..., Any]

async def execute_sync_module_safe(sync_function: SyncFunction, *args: Any, **kwargs: Any) -> Any:
    """Run a synchronous helper in a worker thread without blocking the event loop.

    Use this only for sync helpers that own their sync Playwright objects or do
    non-Playwright blocking work. Do not pass an async Playwright Page/Context
    into a sync helper; convert that helper to async instead.
    """
    return await asyncio.to_thread(partial(sync_function, *args, **kwargs))


def _browser_launcher(playwright: Any, browser_type: str) -> Any:
    try:
        return getattr(playwright, browser_type)
    except AttributeError as exc:
        raise ValueError(f"Unsupported Playwright browser_type: {browser_type}") from exc


def _apply_timeouts(
    context: BrowserContext,
    page: Page,
    *,
    navigation_timeout_ms: int,
    action_timeout_ms: int,
) -> None:
    context.set_default_timeout(action_timeout_ms)
    context.set_default_navigation_timeout(navigation_timeout_ms)
    page.set_default_timeout(action_timeout_ms)
    page.set_default_navigation_timeout(navigation_timeout_ms)


@asynccontextmanager
async def isolated_worker_session(
    playwright: Any,
    *,
    browser_type: str = "chromium",
    headless: bool = True,
    context_options: Optional[dict[str, Any]] = None,
    launch_options: Optional[dict[str, Any]] = None,
    persistent_context: bool = True,
    navigation_timeout_ms: int = 20_000,
    action_timeout_ms: int = 15_000,
) -> AsyncIterator[WorkerSandboxSession]:
    """Create an isolated async Playwright session and always clean it up."""
    current_dir = tempfile.mkdtemp(prefix="worker_sandbox_")
    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None

    try:
        launcher = _browser_launcher(playwright, browser_type)
        context_kwargs = dict(context_options or {})
        launch_kwargs = dict(launch_options or {})

        if persistent_context:
            context_kwargs.setdefault("headless", headless)
            context_kwargs.setdefault("ignore_https_errors", True)
            context_kwargs.setdefault("viewport", {"width": 1366, "height": 768})
            context = await launcher.launch_persistent_context(
                user_data_dir=current_dir,
                **context_kwargs,
            )
        else:
            launch_kwargs.setdefault("headless", headless)
            browser = await launcher.launch(**launch_kwargs)
            context_kwargs.setdefault("ignore_https_errors", True)
            context_kwargs.setdefault("viewport", {"width": 1366, "height": 768})
            context = await browser.new_context(**context_kwargs)

        page = context.pages[0] if context.pages else await context.new_page()
        _apply_timeouts(
            context,
            page,
            navigation_timeout_ms=navigation_timeout_ms,
            action_timeout_ms=action_timeout_ms,
        )
        yield WorkerSandboxSession(
            current_dir=current_dir,
            context=context,
            page=page,
            browser=browser,
        )

    finally:
        if context is not None:
            with suppress(Exception):
                await context.close()
        if browser is not None:
            with suppress(Exception):
                await browser.close()
        shutil.rmtree(current_dir, ignore_errors=True)


async def run_in_isolated_worker_session(
    playwright: Any,
    handler: Callable[[WorkerSandboxSession], Awaitable[Any]],
    **session_kwargs: Any,
) -> Any:
    """Run a worker handler inside an isolated persistent session."""
    async with isolated_worker_session(playwright, **session_kwargs) as session:
        return await handler(session)


async def run_browser_session(
    playwright: Any,
    worker_id: int,
    task: AutomationTask,
    configs: dict[str, Any],
    session_handler: Optional[SessionHandler] = None,
) -> BrowserSessionResult:
    """Run one browser session with total timeout, per-action timeouts, and cleanup."""
    current_step = "infrastructure_init"
    total_timeout_sec = float(configs.get("total_timeout_sec", 40))
    required_browser_args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--disable-gpu",
    ]

    try:
        async with asyncio.timeout(total_timeout_sec):
            network_payload = task.network_payload or {}
            proxy_config = network_payload.get("proxy")

            context_options = dict(configs.get("context_options") or {})
            launch_options = dict(configs.get("launch_options") or {})
            persistent_context = bool(configs.get("persistent_context", True))
            browser_args = list(configs.get("browser_args") or [])
            for arg in required_browser_args:
                if arg not in browser_args:
                    browser_args.append(arg)
            launch_timeout_ms = 15_000
            navigation_timeout_ms = int(configs.get("navigation_timeout_ms", 20_000))
            action_timeout_ms = int(configs.get("action_timeout_ms", 15_000))

            if browser_args:
                if persistent_context:
                    context_options.setdefault("args", browser_args)
                else:
                    launch_options.setdefault("args", browser_args)

            if proxy_config:
                if persistent_context:
                    context_options["proxy"] = proxy_config
                else:
                    launch_options["proxy"] = proxy_config

            if persistent_context:
                context_options.setdefault("timeout", launch_timeout_ms)
            else:
                launch_options.setdefault("timeout", launch_timeout_ms)

            current_step = "browser_session"
            async with isolated_worker_session(
                playwright,
                browser_type="chromium",
                headless=True,
                context_options=context_options,
                launch_options=launch_options,
                persistent_context=persistent_context,
                navigation_timeout_ms=navigation_timeout_ms,
                action_timeout_ms=action_timeout_ms,
            ) as session:
                if session_handler is not None:
                    current_step = "session_handler"
                    handler_result = await session_handler(session, task, configs, worker_id)
                    title = "" if handler_result is None else str(handler_result)
                    return BrowserSessionResult(True, worker_id, task.index, current_step, title=title)

                current_step = "navigation"
                await session.page.goto(
                    task.target_url,
                    wait_until=str(configs.get("wait_until", "commit")),
                    timeout=navigation_timeout_ms,
                )

                current_step = "execution"
                page_title = await session.page.title()
                return BrowserSessionResult(True, worker_id, task.index, current_step, title=page_title)

    except asyncio.TimeoutError:
        return BrowserSessionResult(
            False,
            worker_id,
            task.index,
            current_step,
            error="total timeout",
        )
    except PlaywrightTimeoutError as exc:
        return BrowserSessionResult(
            False,
            worker_id,
            task.index,
            current_step,
            error=f"playwright timeout: {exc}",
        )
    except PlaywrightError as exc:
        return BrowserSessionResult(
            False,
            worker_id,
            task.index,
            current_step,
            error=f"playwright error: {exc}",
        )
    except Exception as exc:
        return BrowserSessionResult(
            False,
            worker_id,
            task.index,
            current_step,
            error=f"{type(exc).__name__}: {exc}",
        )
