"""Isolated persistent Playwright sessions for worker pools."""
from __future__ import annotations

import shutil
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from playwright.async_api import BrowserContext, Page


@dataclass
class WorkerSandboxSession:
    current_dir: str
    context: BrowserContext
    page: Page


@asynccontextmanager
async def isolated_worker_session(
    playwright: Any,
    *,
    browser_type: str = "chromium",
    headless: bool = True,
    context_options: Optional[dict[str, Any]] = None,
    navigation_timeout_ms: int = 45_000,
    action_timeout_ms: int = 15_000,
) -> AsyncIterator[WorkerSandboxSession]:
    """Create a temporary persistent context and always clean it up.

    Each call allocates a fresh ``worker_sandbox_*`` user data directory, so
    cache, cookies, local storage, and service-worker state cannot leak between
    worker cycles.
    """
    current_dir = tempfile.mkdtemp(prefix="worker_sandbox_")
    context: Optional[BrowserContext] = None

    try:
        options = {
            "headless": headless,
            "ignore_https_errors": True,
            "viewport": {"width": 1366, "height": 768},
            **dict(context_options or {}),
        }
        browser_launcher = getattr(playwright, browser_type)
        context = await browser_launcher.launch_persistent_context(
            user_data_dir=current_dir,
            **options,
        )
        context.set_default_navigation_timeout(navigation_timeout_ms)
        context.set_default_timeout(action_timeout_ms)

        page = context.pages[0] if context.pages else await context.new_page()
        yield WorkerSandboxSession(current_dir=current_dir, context=context, page=page)

    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        shutil.rmtree(current_dir, ignore_errors=True)


async def run_in_isolated_worker_session(
    playwright: Any,
    handler: Callable[[WorkerSandboxSession], Awaitable[Any]],
    **session_kwargs: Any,
) -> Any:
    """Run a worker handler inside an isolated persistent session."""
    async with isolated_worker_session(playwright, **session_kwargs) as session:
        return await handler(session)
