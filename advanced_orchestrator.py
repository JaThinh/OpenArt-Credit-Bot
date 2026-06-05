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

# Nhúng chặt chẽ các module hệ thống đã tối ưu hóa chống bot ở các bước trước
from vpn_manager import vpn_manager
from playwright_input_utils import PlaywrightInputUtils
from network_service import NetworkService

from playwright_session_manager import (
    AutomationTask,
    BrowserSessionResult,
    WorkerSandboxSession,
    run_browser_session,
)

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class CoreBrowserOrchestrator:
    """Queue based worker pool that runs Playwright on one asyncio event loop."""
    DEFAULT_CONFIG: dict[str, Any] = {
        "concurrency": 2,                  # Tạo Hotmail khuyên duy trì 1-2 worker để tránh làm nát IP
        "max_exception_retries": 3,
        "bot_protection_wait": 5.0,
        "headless": False,                 # BẮT BUỘC ĐỂ FALSE: Bật giao diện giúp nâng điểm Trust lên tối đa
        "browser_type": "chromium",
        "persistent_context": True,
        "launch_timeout_ms": 15_000,
        "action_timeout_ms": 15_000,
        "navigation_timeout_ms": 25_000,
        "selector_timeout_ms": 15_000,
        "total_timeout_sec": 60,
        "failure_circuit_threshold": 4,
        "sandbox_base_dir": "./sandboxes",
        "wait_until": "networkidle",       # Đợi tải xong toàn bộ tài nguyên mạng để tối ưu hành vi người thật
        "browser_args": [
            "--disable-blink-features=AutomationControlled", # Triệt tiêu hoàn toàn cờ nhận diện bot
            "--disable-dev-shm-usage",
            "--window-size=1366,768",
        ],
    }

    def __init__(self, config: Optional[dict[str, Any]] = None, concurrency: Optional[int] = None):
        self.config = {**self.DEFAULT_CONFIG, **dict(config or {})}
        self.concurrency = int(concurrency or self.config.get("concurrency") or 2)
        self.queue: asyncio.Queue[Optional[AutomationTask]] = asyncio.Queue()
        self.workers: list[asyncio.Task[Any]] = []
        self.circuit_open = False
        self.failure_counter = 0
        self.sandbox_base_path = Path(str(self.config.get("sandbox_base_dir", "./sandboxes")))
        self.vpn_lock = asyncio.Lock()    # Khóa chặn an toàn: Ngăn các luồng khác chạy đè khi đang đổi IP
        self.is_changing_ip = False

    def _get_next_device_profile(self, attempt: int) -> dict[str, Any]:
        """
        THIẾT LẬP PROFILE MÁY TÍNH THẬT (DESKTOP WINDOWS/MAC).
        Loại bỏ hoàn toàn các User-Agent thiết bị di động cũ để tránh lỗi lệch thông số (Mismatch).
        """
        profiles = [
            {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "viewport": {"width": 1366, "height": 768},
                "is_mobile": False,
                "has_touch": False,
                "device_name": "Windows PC Chrome 124",
            },
            {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edge/123.0.0.0",
                "viewport": {"width": 1440, "height": 900},
                "is_mobile": False,
                "has_touch": False,
                "device_name": "Windows PC Edge 123",
            },
            {
                "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "viewport": {"width": 1536, "height": 864},
                "is_mobile": False,
                "has_touch": False,
                "device_name": "macOS Chrome 124",
            }
        ]
        return profiles[(attempt - 1) % len(profiles)]

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

    async def _check_gacha_presence(self, page) -> bool:
        """Kiểm tra nhanh xem hệ thống bảo mật Gacha (Arkose Labs) có chặn trên màn hình không"""
        gacha_selectors = [
            "iframe[src*='arkoselabs']",
            "iframe[id*='enforcement']",
            "#inline-captcha-container",
            "div[id*='captcha']"
        ]
        for selector in gacha_selectors:
            try:
                if await page.locator(selector).is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _request_ip_rotation(self, worker_id: int):
        """Hàm phối hợp đổi IP mạng an toàn, tạm dừng các luồng khác xếp hàng chờ dải IP sạch mới"""
        if self.is_changing_ip:
            return
        async with self.vpn_lock:
            if self.is_changing_ip:
                return
            self.is_changing_ip = True
            logger.warning(f"[W{worker_id}] ⚠️ Bị chặn bởi Gacha Hotmail. Tiến hành xoay ExpressVPN lấy IP mới...")
            try:
                success = await vpn_manager.rotate_ip()
                if success:
                    await asyncio.sleep(4)  # Chờ 4 giây cho dải định tuyến Windows nhận mạng ổn định
            except Exception as e:
                logger.error(f"[W{worker_id}] Gặp lỗi khi điều phối đổi IP: {e}")
            finally:
                self.is_changing_ip = False

    async def _execute_task_flow(
        self, session: WorkerSandboxSession, task: AutomationTask, configs: dict[str, Any], worker_id: int,
    ) -> str:
        """Luồng đăng ký Hotmail mô phỏng hành vi sinh học của người thật chi tiết"""
        page = session.page
        navigation_timeout_ms = int(configs.get("navigation_timeout_ms", 25_000))

        # 1. Đi dạo mồi qua Google để nạp lịch sử duyệt web tự nhiên (Nuôi điểm Trust)
        try:
            await page.goto("https://google.com", wait_until="networkidle", timeout=12000)
            await asyncio.sleep(2)
        except Exception:
            pass

        # 2. Điều hướng thẳng vào liên kết đăng ký Hotmail/Outlook thực tế
        await page.goto(
            task.target_url,
            wait_until=str(configs.get("wait_until", "networkidle")),
            timeout=navigation_timeout_ms,
        )
        await asyncio.sleep(1)

        # Kiểm tra nếu dải mạng quá nát bị Microsoft ném Gacha ngay khi vừa tải trang
        if await self._check_gacha_presence(page):
            return "GACHA_TRIGGERED_IMMEDIATELY"

        record_data = task.record_data or {}
        email_to_input = str(record_data.get("email") or "").strip()
        password_to_input = str(record_data.get("password") or "SecurePass123!").strip()

        if email_to_input:
            profile = configs.get("profile") or {}
            selectors = self._get_adaptive_selectors(is_mobile=bool(profile.get("is_mobile")))
            combined_query = ", ".join(selectors)

            await page.wait_for_selector(
                combined_query,
                state="visible",
                timeout=int(configs.get("selector_timeout_ms", 15_000)),
            )

            # 3. Điền Email bằng thuật toán gõ phím sinh học và di chuột cong lệch tâm
            logger.info(f"[W{worker_id}] Tiến hành nhập dữ liệu email bằng cơ chế người thật...")
            await PlaywrightInputUtils.type_humanlike(page, combined_query, email_to_input)
            await PlaywrightInputUtils.click_humanlike(page, "input[type='submit']")
            await asyncio.sleep(2)

            if await self._check_gacha_presence(page):
                return "GACHA_TRIGGERED_AFTER_EMAIL"

            # 4. Điền Mật khẩu bằng cơ chế người thật
            pass_query = "input[name='PasswordInput'], input[type='password']"
            await page.wait_for_selector(pass_query, state="visible", timeout=int(configs.get("selector_timeout_ms", 15_000)))
            logger.info(f"[W{worker_id}] Tiến hành nhập dữ liệu mật khẩu bằng cơ chế người thật...")
            await PlaywrightInputUtils.type_humanlike(page, pass_query, password_to_input)
            try:
                if await page.locator("input[name='iOptinEmail']").is_visible():
                    await PlaywrightInputUtils.click_humanlike(page, "input[name='iOptinEmail']")
            except Exception:
                pass