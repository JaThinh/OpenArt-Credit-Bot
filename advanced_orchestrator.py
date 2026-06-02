import asyncio
import random
import logging
import shutil
import os
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError, Error

# Cấu hình logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s'
)

class AutonomousLoadOrchestrator:
    def __init__(self, config):
        self.config = config
        self.circuit_open = False
        self.failure_counter = 0
        self.sandbox_base_path = Path(self.config.get("sandbox_base_dir", "./sandboxes"))

    def _ensure_sandbox_dir(self):
        """Tạo thư mục sandbox nếu chưa tồn tại"""
        self.sandbox_base_path.mkdir(parents=True, exist_ok=True)

    def _cleanup_old_sandbox(self, sandbox_path):
        """
        Xóa sạch thư mục sandbox cũ để tránh chiếm dụng dung lượng ổ cứng
        được gọi sau khi session kết thúc (thành công hoặc thất bại)
        """
        try:
            if os.path.exists(sandbox_path):
                shutil.rmtree(sandbox_path, ignore_errors=True)
                logging.info(f"Đã xóa sạch sandbox tạm: {sandbox_path}")
        except Exception as e:
            logging.warning(f"Cảnh báo khi xóa sandbox {sandbox_path}: {e}")

    def _get_next_device_profile(self, attempt):
        """
        TẦNG 1: Biến đổi cấu trúc tác nhân (Dynamic Fingerprint Mutation)
        Tự động xoay chuyển cấu hình driver để tìm phân hệ hiển thị có độ rủi ro thấp nhất.
        """
        if attempt == 1:
            # Cấu hình giả lập thiết bị di động hệ điều hành iOS
            return {
                "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
                "viewport": {"width": 393, "height": 852},
                "is_mobile": True,
                "has_touch": True,
                "device_name": "iPhone 15 Pro"
            }
        elif attempt == 2:
            # Cấu hình giả lập thiết bị máy tính bảng Android cao cấp
            return {
                "user_agent": "Mozilla/5.0 (Linux; Android 14; Pixel Tablet) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                "viewport": {"width": 1280, "height": 800},
                "is_mobile": True,
                "has_touch": True,
                "device_name": "Pixel Tablet"
            }
        else:
            # Cấu hình hệ điều hành Desktop tiêu chuẩn (Môi trường QA)
            return {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
                "viewport": {"width": 1920, "height": 1080},
                "is_mobile": False,
                "has_touch": False,
                "device_name": "Desktop Windows"
            }

    def _get_adaptive_selectors(self, is_mobile=False):
        """
        Bộ chọn logic đa dạng - Tương thích với cả giao diện Desktop và Mobile
        Trả về danh sách các selectors theo thứ tự ưu tiên
        """
        base_selectors = [
            "input[id='usernameInput']",
            "input[id='MemberName']",
            "input[name='MemberName']",
            "input[type='email']",
            "input[name='loginfmt']",
            "input[autocomplete='username']"
        ]

        if is_mobile:
            # Thêm các selectors tối ưu cho mobile viewport
            mobile_selectors = [
                "input[role='textbox']",
                "[role='textbox'][type='email']",
            ]
            return mobile_selectors + base_selectors

        return base_selectors

    async def execute_robust_session(self, worker_id, target_url, record_data, proxy_pool=None):
        """
        TẦNG 2: Bộ điều phối tiến trình có khả năng tự phục hồi (Self-Healing Engine)

        Args:
            worker_id: Định danh worker
            target_url: URL mục tiêu để tải
            record_data: Dict chứa {"email": "...", "password": "..."}
            proxy_pool: Danh sách proxy dict [{"server": "...", "username": "...", "password": "..."}]

        Returns:
            bool: True nếu session thành công, False nếu thất bại
        """
        if self.circuit_open:
            logging.critical(f"[W{worker_id}] Mạch điều khiển đang mở. Từ chối nạp tiến trình để bảo vệ tài nguyên.")
            return False

        self._ensure_sandbox_dir()
        async with async_playwright() as playwright_instance:
            max_attempts = self.config.get("max_exception_retries", 3)

            for attempt in range(1, max_attempts + 1):
                # Khởi tạo thông số thiết bị tương ứng với lượt chạy
                profile = self._get_next_device_profile(attempt)
                sandbox_path = str(self.sandbox_base_path / f"w{worker_id}_a{attempt}")

                # Trích xuất proxy ngẫu nhiên từ bể chứa (Proxy Pool) của bạn
                current_proxy = None
                if proxy_pool and len(proxy_pool) > 0:
                    current_proxy = random.choice(proxy_pool)

                launch_kwargs = {}

                if current_proxy:
                    launch_kwargs["proxy"] = {
                        "server": current_proxy.get("server"),
                        "username": current_proxy.get("username"),
                        "password": current_proxy.get("password")
                    }

                browser_context = None
                try:
                    proxy_info = current_proxy.get("server") if current_proxy else "DIRECT"
                    logging.info(f"[W{worker_id}] Khởi tạo cấu hình #Attempt-{attempt} ({profile['device_name']}) qua trạm mạng: {proxy_info}")

                    # Kích hoạt Persistent Context để cô lập dữ liệu bộ nhớ đệm hoàn toàn
                    browser_context = await playwright_instance.chromium.launch_persistent_context(
                        user_data_dir=sandbox_path,
                        headless=self.config.get("headless", False),
                        user_agent=profile["user_agent"],
                        viewport=profile["viewport"],
                        is_mobile=profile["is_mobile"],
                        has_touch=profile["has_touch"],
                        args=[
                            "--disable-blink-features=AutomationControlled",
                            "--disable-webrtc",
                            "--disable-sync"
                        ],
                        **launch_kwargs
                    )

                    page = await browser_context.new_page()
                    # ========================================================
                    # THÊM CẤU HÌNH PHÁ BĂNG KHẨN CẤP (ANTI-FREEZE CONFIG)
                    # ========================================================
                    # Ép tất cả các lệnh tương tác (click, fill) không được đợi quá 30 giây
                    page.set_default_timeout(30000)

                    # Ép lệnh page.goto không được treo luồng quá 35 giây khi kết nối mạng proxy
                    page.set_default_navigation_timeout(35000)
                    # ========================================================

                    # Tăng giới hạn thời gian phản hồi tầng Driver lên 60 giây để chống nghẽn dòng mạng
                    # (đã hạ mức cho tương tác từ 60s xuống 30s cho an toàn)
                    # page.set_default_timeout(60000)

                    # Thực hiện tải trang mục tiêu
                    logging.info(f"[W{worker_id}] Đang đồng bộ cấu trúc DOM tới endpoint...")
                    await page.goto(target_url, wait_until="domcontentloaded")

                    # Bộ chọn logic đa dạng tương ứng với loại thiết bị
                    selectors = self._get_adaptive_selectors(is_mobile=profile["is_mobile"])
                    combined_query = ", ".join(selectors)

                    await page.wait_for_selector(combined_query, state="visible", timeout=20000)
                    await page.focus(combined_query)

                    # TẦNG 3: Đồng bộ tốc độ nhập phím biến thiên liên tục
                    email_to_input = record_data.get("email", "")
                    for char in email_to_input:
                        await page.keyboard.type(char, delay=random.randint(70, 150))

                    # Thêm thời gian chờ ngẫu nhiên trước khi tiếp tục (anti-bot behavior)
                    await asyncio.sleep(random.uniform(1.0, 2.5))

                    logging.info(f"[W{worker_id}] Bản ghi dữ liệu đã được nạp thành công vào Form.")
                    self.failure_counter = 0  # Reset trình đếm lỗi hệ thống

                    # Cleanup thành công - đóng context
                    await browser_context.close()
                    self._cleanup_old_sandbox(sandbox_path)

                    return True

                except (TimeoutError, Error) as connection_exception:
                    logging.warning(f"[W{worker_id}] Cảnh báo lỗi tầng vật lý tại lượt {attempt}: {connection_exception}")
                    self.failure_counter += 1

                    if self.failure_counter >= 5:
                        self.circuit_open = True
                        logging.critical("[Hệ thống] Phát hiện lỗi hệ thống diện rộng liên tiếp. Kích hoạt ngắt mạch khẩn cấp.")

                    # Khoảng trễ tịnh tiến tăng dần (Exponential Backoff với Jitter) để làm sạch card mạng
                    sleep_duration = (self.config.get("bot_protection_wait", 5.0) * attempt) + random.uniform(1.0, 3.0)
                    logging.info(f"[W{worker_id}] Tái cấu hình luồng mạng ngầm, vui lòng chờ {sleep_duration:.2f} giây...")

                    if browser_context:
                        await browser_context.close()

                    # Cleanup sandbox ngay cả khi thất bại
                    self._cleanup_old_sandbox(sandbox_path)

                    await asyncio.sleep(sleep_duration)

                except Exception as critical_error:
                    logging.error(f"[W{worker_id}] Lỗi logic kịch bản: {critical_error}")
                    if browser_context:
                        await browser_context.close()

                    # Cleanup sandbox khi gặp lỗi
                    self._cleanup_old_sandbox(sandbox_path)
                    break

        return False

    def reset_circuit_breaker(self):
        """Đặt lại trạng thái circuit breaker sau khi bảo trì"""
        self.circuit_open = False
        self.failure_counter = 0
        logging.info("[Hệ thống] Circuit breaker đã được đặt lại.")

    def cleanup_all_sandboxes(self):
        """Xóa toàn bộ thư mục sandbox (dùng khi shutdown)"""
        try:
            if self.sandbox_base_path.exists():
                shutil.rmtree(self.sandbox_base_path)
                logging.info("Đã xóa toàn bộ thư mục sandbox.")
        except Exception as e:
            logging.warning(f"Lỗi khi xóa toàn bộ sandbox: {e}")


# ============ USAGE EXAMPLE ============
#
# async def test_orchestrator():
#     config = {
#         "max_exception_retries": 3,
#         "bot_protection_wait": 5.0,
#         "headless": False,
#         "sandbox_base_dir": "./sandboxes"
#     }
#
#     orchestrator = AutonomousLoadOrchestrator(config)
#
#     proxy_pool = [
#         {"server": "http://proxy1.com:8080", "username": "user1", "password": "pass1"},
#         {"server": "http://proxy2.com:8080", "username": "user2", "password": "pass2"}
#     ]
#
#     record = {"email": "test@example.com", "password": "secure_password"}
#
#     success = await orchestrator.execute_robust_session(
#         worker_id=1,
#         target_url="https://outlook.live.com/mail/0/?prompt=create_account",
#         record_data=record,
#         proxy_pool=proxy_pool
#     )
#
#     print(f"Session result: {success}")
#     orchestrator.cleanup_all_sandboxes()
