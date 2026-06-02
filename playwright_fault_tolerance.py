"""Fault-tolerant async Playwright navigation helpers.

This module is meant for legitimate UI test workers that need to survive
transient gateway, adapter, or browser-session failures while navigating.
"""
from __future__ import annotations

import asyncio
import inspect
import random
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from playwright.async_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

import network_service


NETWORK_ERROR_SIGNALS = (
    "timeout",
    "timed out",
    "net::err",
    "err_internet_disconnected",
    "err_network_changed",
    "err_connection_reset",
    "err_connection_closed",
    "err_connection_refused",
    "err_connection_timed_out",
    "err_name_not_resolved",
    "err_proxy_connection_failed",
    "err_tunnel_connection_failed",
    "err_socks_connection_failed",
    "ns_error_net_timeout",
    "ns_error_net_reset",
    "ns_error_net_interrupt",
    "econnreset",
    "econnrefused",
    "econnaborted",
    "connection reset",
    "connection refused",
    "connection aborted",
    "connection closed",
    "target page, context or browser has been closed",
    "browser has been closed",
    "proxy",
    "gateway",
    "tunnel",
    "socket",
    "dns",
    "could not resolve",
)


Logger = Callable[[str, str, int], Any]
SessionFactory = Callable[[Any], Awaitable["BrowserSession"]]
FailoverFunction = Callable[..., Any]


@dataclass
class BrowserSession:
    browser: Browser
    context: BrowserContext
    page: Page


@dataclass
class NavigationFailoverResult:
    session: BrowserSession
    response: Any
    attempts: int
    recovered: bool


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def is_navigation_network_error(error: Exception) -> bool:
    if isinstance(error, PlaywrightTimeoutError):
        return True
    if not isinstance(error, PlaywrightError):
        return False
    message = str(error).lower()
    return any(signal in message for signal in NETWORK_ERROR_SIGNALS)


def _log(logger: Optional[Logger], message: str, level: str, worker_id: int) -> None:
    if logger is None:
        print(f"[W{worker_id:02d}] [{level}] {message}")
        return
    try:
        logger(message, level, worker_id)
    except TypeError:
        logger(message)  # type: ignore[misc]


async def create_default_browser_session(
    playwright: Any,
    *,
    browser_type: str = "firefox",
    headless: bool = True,
    launch_options: Optional[dict[str, Any]] = None,
    context_options: Optional[dict[str, Any]] = None,
) -> BrowserSession:
    """Create a fresh browser/context/page triplet for retry workers."""
    launch_options = dict(launch_options or {})
    context_options = {
        "ignore_https_errors": True,
        "viewport": {"width": 1366, "height": 768},
        **dict(context_options or {}),
    }

    browser_launcher = getattr(playwright, browser_type)
    browser = await browser_launcher.launch(headless=headless, **launch_options)
    context = await browser.new_context(**context_options)
    page = await context.new_page()
    return BrowserSession(browser=browser, context=context, page=page)


async def close_browser_session(session: Optional[BrowserSession]) -> None:
    if session is None:
        return
    try:
        await session.context.close()
    except Exception:
        pass
    try:
        await session.browser.close()
    except Exception:
        pass


async def _call_failover_function(
    failover_func: FailoverFunction,
    *,
    worker_id: int,
    attempt: int,
    reason: str,
) -> Any:
    try:
        signature = inspect.signature(failover_func)
        if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
            return await maybe_await(
                failover_func(worker_id=worker_id, attempt=attempt, reason=reason)
            )

        kwargs = {
            key: value
            for key, value in {
                "worker_id": worker_id,
                "attempt": attempt,
                "reason": reason,
            }.items()
            if key in signature.parameters
        }
        return await maybe_await(failover_func(**kwargs))
    except (TypeError, ValueError):
        return await maybe_await(failover_func())


async def run_network_failover(
    *,
    worker_id: int,
    attempt: int,
    error: Exception,
    logger: Optional[Logger] = None,
    failover_func: Optional[FailoverFunction] = None,
) -> bool:
    """Invoke the external network_service hook without crashing the worker."""
    failover_func = failover_func or network_service.reconfigure_network_adapter
    reason = f"{type(error).__name__}: {error}"

    _log(logger, f"Network failover triggered after navigation error: {reason}", "WARN", worker_id)
    try:
        result = await _call_failover_function(
            failover_func,
            worker_id=worker_id,
            attempt=attempt,
            reason=reason,
        )
        return bool(result)
    except Exception as failover_error:
        _log(
            logger,
            f"network_service failover failed: {type(failover_error).__name__}: {failover_error}",
            "WARN",
            worker_id,
        )
        return False


async def goto_with_browser_failover(
    playwright: Any,
    session: BrowserSession,
    url: str,
    *,
    worker_id: int = 0,
    max_retries: int = 3,
    wait_until: str = "domcontentloaded",
    timeout: int = 30_000,
    min_sleep_seconds: float = 5.0,
    max_sleep_seconds: float = 8.0,
    logger: Optional[Logger] = None,
    failover_func: Optional[FailoverFunction] = None,
    session_factory: Optional[SessionFactory] = None,
) -> NavigationFailoverResult:
    """Navigate with failover, browser replacement, and bounded retries.

    ``max_retries`` is the number of retries after the first attempt, so the
    maximum number of page.goto calls is ``max_retries + 1``.
    """
    if max_retries < 0:
        raise ValueError("max_retries must be >= 0")
    if min_sleep_seconds > max_sleep_seconds:
        raise ValueError("min_sleep_seconds must be <= max_sleep_seconds")

    session_factory = session_factory or create_default_browser_session
    current_session = session
    last_error: Optional[Exception] = None
    total_attempts = max_retries + 1

    for attempt in range(1, total_attempts + 1):
        try:
            _log(
                logger,
                f"Navigating attempt {attempt}/{total_attempts}: {url}",
                "INFO",
                worker_id,
            )
            response = await current_session.page.goto(
                url,
                wait_until=wait_until,
                timeout=timeout,
            )
            return NavigationFailoverResult(
                session=current_session,
                response=response,
                attempts=attempt,
                recovered=attempt > 1,
            )
        except (PlaywrightTimeoutError, PlaywrightError) as error:
            if not is_navigation_network_error(error):
                raise

            last_error = error
            _log(
                logger,
                f"Navigation failed on attempt {attempt}/{total_attempts}: {error}",
                "WARN",
                worker_id,
            )

            await run_network_failover(
                worker_id=worker_id,
                attempt=attempt,
                error=error,
                logger=logger,
                failover_func=failover_func,
            )

            delay = random.uniform(min_sleep_seconds, max_sleep_seconds)
            _log(
                logger,
                f"Waiting {delay:.1f}s for network stack refresh before retry...",
                "INFO",
                worker_id,
            )
            await asyncio.sleep(delay)

            await close_browser_session(current_session)

            if attempt >= total_attempts:
                break

            _log(logger, "Creating a fresh browser instance...", "INFO", worker_id)
            current_session = await session_factory(playwright)

    raise RuntimeError(
        f"Navigation failed after {total_attempts} attempts: {url}"
    ) from last_error
