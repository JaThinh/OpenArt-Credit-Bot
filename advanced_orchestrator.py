"""Core async worker pool for browser automation tasks."""
from __future__ import annotations

import asyncio
import logging
import random
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import async_playwright

from playwright_session_manager import (
    AutomationTask,
    BrowserSessionResult,
    WorkerSandboxSession,
    run_browser_session,
)


logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")


class CoreBrowserOrchestrator:
    """Queue based worker pool that runs Playwright on one asyncio event loop."""

    DEFAULT_CONFIG: dict[str, Any] = {
        "concurrency": 3,
        "max_exception_retries": 3,
        "bot_protection_wait": 5.0,
        "headless": True,
        "browser_type": "chromium",
        "persistent_context": True,
        "launch_timeout_ms": 15_000,
        "action_timeout_ms": 15_000,
        "navigation_timeout_ms": 25_000,
        "selector_timeout_ms": 20_000,
        "total_timeout_sec": 50,
        "failure_circuit_threshold": 5,
        "sandbox_base_dir": "./sandboxes",
        "wait_until": "domcontentloaded",
        "browser_args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--disable-sync",
            "--disable-webrtc",
            "--no-sandbox",
        ],
    }

    def __init__(self, config: Optional[dict[str, Any]] = None, concurrency: Optional[int] = None):
        self.config = {**self.DEFAULT_CONFIG, **dict(config or {})}
        self.concurrency = int(concurrency or self.config.get("concurrency") or 3)
        self.queue: asyncio.Queue[Optional[AutomationTask]] = asyncio.Queue()
        self.workers: list[asyncio.Task[Any]] = []
        self.circuit_open = False
        self.failure_counter = 0
        self.sandbox_base_path = Path(str(self.config.get("sandbox_base_dir", "./sandboxes")))

    def _get_next_device_profile(self, attempt: int) -> dict[str, Any]:
        if attempt == 1:
            return {
                "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
                "viewport": {"width": 393, "height": 852},
                "is_mobile": True,
                "has_touch": True,
                "device_name": "iPhone 15 Pro",
            }
        if attempt == 2:
            return {
                "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel Tablet) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "viewport": {"width": 1280, "height": 800},
                "is_mobile": True,
                "has_touch": True,
                "device_name": "Pixel Tablet",
            }
        return {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
            "viewport": {"width": 1920, "height": 1080},
            "is_mobile": False,
            "has_touch": False,
            "device_name": "Desktop Windows",
        }

    def _get_adaptive_selectors(self, is_mobile: bool = False) -> list[str]:
        base_selectors = [
            "input[id='usernameInput']",
            "input[id='MemberName']",
            "input[name='MemberName']",
            "input[type='email']",
            "input[name='loginfmt']",
            "input[autocomplete='username']",
        ]
        if not is_mobile:
            return base_selectors
        return [
            "input[role='textbox']",
            "[role='textbox'][type='email']",
            *base_selectors,
        ]

    def _normalize_proxy(self, proxy_value: Any) -> Optional[dict[str, str]]:
        if not proxy_value:
            return None
        if isinstance(proxy_value, str):
            raw = proxy_value.strip()
            if not raw:
                return None
            return {"server": raw if "://" in raw else f"http://{raw}"}
        if not isinstance(proxy_value, dict):
            return None

        server = str(proxy_value.get("server") or "").strip()
        if not server:
            return None
        normalized = {"server": server if "://" in server else f"http://{server}"}
        username = proxy_value.get("username")
        password = proxy_value.get("password")
        if username is not None and str(username).strip():
            normalized["username"] = str(username).strip()
        if password is not None and str(password).strip():
            normalized["password"] = str(password).strip()
        return normalized

    def _attempt_config(self, configs: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        context_options = dict(configs.get("context_options") or {})
        context_options.update(
            {
                "user_agent": profile["user_agent"],
                "viewport": profile["viewport"],
                "is_mobile": profile["is_mobile"],
                "has_touch": profile["has_touch"],
            }
        )
        return {
            **configs,
            "context_options": context_options,
            "profile": profile,
        }

    async def _execute_task_flow(
        self,
        session: WorkerSandboxSession,
        task: AutomationTask,
        configs: dict[str, Any],
        worker_id: int,
    ) -> str:
        page = session.page
        await page.goto(task.target_url, wait_until=str(configs.get("wait_until", "domcontentloaded")))

        record_data = task.record_data or {}
        email_to_input = str(record_data.get("email") or "").strip()
        if email_to_input:
            profile = configs.get("profile") or {}
            selectors = self._get_adaptive_selectors(is_mobile=bool(profile.get("is_mobile")))
            combined_query = ", ".join(selectors)
            await page.wait_for_selector(
                combined_query,
                state="visible",
                timeout=int(configs.get("selector_timeout_ms", 20_000)),
            )
            await page.focus(combined_query)
            for char in email_to_input:
                await page.keyboard.type(char, delay=random.randint(70, 150))
            await asyncio.sleep(random.uniform(1.0, 2.5))

        return await page.title()

    async def run_session(
        self,
        worker_id: int,
        task: AutomationTask,
        configs: Optional[dict[str, Any]] = None,
    ) -> bool:
        if self.circuit_open:
            logging.critical("[W%s] Circuit breaker is open; refusing new browser session.", worker_id)
            return False

        active_config = {**self.config, **dict(configs or {})}
        max_attempts = int(active_config.get("max_exception_retries", 1))
        base_payload = dict(task.network_payload or {})
        proxy_pool = base_payload.pop("proxy_pool", None)

        async with async_playwright() as playwright:
            for attempt in range(1, max_attempts + 1):
                profile = self._get_next_device_profile(attempt)
                current_proxy = base_payload.get("proxy")
                if current_proxy is None and proxy_pool:
                    current_proxy = random.choice(proxy_pool)
                normalized_proxy = self._normalize_proxy(current_proxy)

                attempt_payload = dict(base_payload)
                if normalized_proxy:
                    attempt_payload["proxy"] = normalized_proxy
                attempt_task = replace(task, network_payload=attempt_payload or None)
                attempt_config = self._attempt_config(active_config, profile)
                proxy_info = normalized_proxy.get("server") if normalized_proxy else "DIRECT"

                logging.info(
                    "[W%s] Starting task %s attempt %s/%s using %s via %s",
                    worker_id,
                    task.index,
                    attempt,
                    max_attempts,
                    profile["device_name"],
                    proxy_info,
                )

                result: BrowserSessionResult = await run_browser_session(
                    playwright,
                    worker_id,
                    attempt_task,
                    attempt_config,
                    session_handler=self._execute_task_flow,
                )
                if result.success:
                    self.failure_counter = 0
                    logging.info(
                        "[W%s] Task %s completed. Title: %r",
                        worker_id,
                        task.index,
                        result.title,
                    )
                    return True

                self.failure_counter += 1
                logging.warning(
                    "[W%s] Task %s failed at %s: %s",
                    worker_id,
                    task.index,
                    result.step,
                    result.error,
                )
                if self.failure_counter >= int(active_config.get("failure_circuit_threshold", 5)):
                    self.circuit_open = True
                    logging.critical("Circuit breaker opened after repeated browser-session failures.")
                    return False

                if attempt < max_attempts:
                    sleep_duration = float(active_config.get("bot_protection_wait", 5.0)) * attempt
                    sleep_duration += random.uniform(1.0, 3.0)
                    await asyncio.sleep(sleep_duration)

        return False

    async def _worker_loop(self, worker_id: int, configs: dict[str, Any]) -> None:
        logging.info("[W%s] Worker is ready.", worker_id)
        while True:
            task = await self.queue.get()
            if task is None:
                self.queue.task_done()
                break
            try:
                await self.run_session(worker_id, task, configs)
            finally:
                self.queue.task_done()
        logging.info("[W%s] Worker stopped safely.", worker_id)

    async def start(self, task_list: list[AutomationTask], configs: Optional[dict[str, Any]] = None) -> None:
        active_config = {**self.config, **dict(configs or {})}
        self.queue = asyncio.Queue()
        self.workers = [
            asyncio.create_task(self._worker_loop(i + 1, active_config))
            for i in range(self.concurrency)
        ]

        try:
            for task in task_list:
                await self.queue.put(task)
            await self.queue.join()
        finally:
            for _ in range(self.concurrency):
                await self.queue.put(None)
            await asyncio.gather(*self.workers, return_exceptions=True)

    async def execute_robust_session(
        self,
        worker_id: int,
        target_url: str,
        record_data: dict[str, Any],
        proxy_pool: Optional[list[Any]] = None,
    ) -> bool:
        task = AutomationTask(
            index=worker_id,
            target_url=target_url,
            network_payload={"proxy_pool": proxy_pool or []},
            record_data=record_data,
        )
        return await self.run_session(worker_id, task, self.config)

    def reset_circuit_breaker(self) -> None:
        self.circuit_open = False
        self.failure_counter = 0
        logging.info("Circuit breaker has been reset.")

    def cleanup_all_sandboxes(self) -> None:
        try:
            if self.sandbox_base_path.exists():
                shutil.rmtree(self.sandbox_base_path)
                logging.info("Removed sandbox directory: %s", self.sandbox_base_path)
        except Exception as exc:
            logging.warning("Failed to remove sandbox directory %s: %s", self.sandbox_base_path, exc)


class AutonomousLoadOrchestrator(CoreBrowserOrchestrator):
    """Backward-compatible name used by orchestration_bridge.py."""


async def main() -> None:
    config_profile = {
        "headless": True,
        "launch_timeout_ms": 15_000,
        "action_timeout_ms": 15_000,
        "navigation_timeout_ms": 25_000,
        "total_timeout_sec": 50,
    }
    tasks = [
        AutomationTask(index=1, target_url="https://example.com"),
        AutomationTask(index=2, target_url="https://example.org"),
    ]
    orchestrator = CoreBrowserOrchestrator(config_profile, concurrency=2)
    await orchestrator.start(tasks, config_profile)


if __name__ == "__main__":
    asyncio.run(main())
