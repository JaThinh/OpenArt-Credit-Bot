# advanced_orchestrator.py
import asyncio
import logging
from .vpn_manager import vpn_manager  # Giả định vpn_manager export một instance chung

logger = logging.getLogger(__name__)

class AdvancedOrchestrator:
    def __init__(self, max_workers=2):
        self.max_workers = max_workers
        self.active_workers = 0
        self.vpn_lock = asyncio.Lock()  # Khóa chặn các luồng khác khi đang đổi IP
        self.is_changing_ip = False

    async def request_ip_rotation(self, worker_id: int):
        """
        Hàm được gọi bởi các worker khi phát hiện dính gacha/captcha
        hoặc lỗi mạng liên tiếp.
        """
        if self.is_changing_ip:
            logger.info(f"[Worker-{worker_id}] Hệ thống đang đổi IP rồi, xếp hàng chờ...")
            return

        async with self.vpn_lock:
            # Check lại một lần nữa để tránh việc nhiều worker cùng đổi IP một lúc
            if self.is_changing_ip:
                return

            self.is_changing_ip = True
            logger.warning(f"⚠️ [Orchestrator] Worker-{worker_id} báo lỗi Gacha/Blacklist. Tiến hành ngắt luồng và đổi ExpressVPN...")

            try:
                # Gọi hàm đổi IP từ vpn_manager của bạn
                # Bạn có thể truyền địa chỉ quốc gia ngẫu nhiên nếu vpn_manager hỗ trợ
                success = await vpn_manager.rotate_ip()

                if success:
                    logger.info("[Orchestrator] Đổi IP thành công! Chờ 5 giây để mạng ổn định...")
                    await asyncio.sleep(5)
                else:
                    logger.error("[Orchestrator] Đổi ExpressVPN thất bại, thử lại sau.")
            except Exception as e:
                logger.error(f"[Orchestrator] Lỗi trong quá trình đổi IP: {e}")
            finally:
                self.is_changing_ip = False

    async def worker_task(self, worker_id: int, account_queue: asyncio.Queue):
        """Logic chạy vòng lặp của một Worker"""
        while not account_queue.empty():
            # Nếu hệ thống đang đổi IP, worker này phải tạm dừng không được load trang mới
            while self.is_changing_ip:
                await asyncio.sleep(2)

            account_data = await account_queue.get()
            logger.info(f"[Worker-{worker_id}] Bắt đầu xử lý tài khoản: {account_data.get('email')}")

            captcha_triggered_count = 0
            max_captcha_tolerance = 2  # Thử thách gacha tối đa cho phép xuất hiện trên 1 IP

            try:
                # -------------------------------------------------------------
                # Đoạn này gọi sang logic chạy Playwright (ví dụ: utils_flow.py)
                # Bạn cần cấu hình hàm chạy trả về trạng thái (status)
                # -------------------------------------------------------------

                # Giả lập luồng chạy thực tế:
                # status, info = await run_outlook_signup_flow(page, account_data)

                # MÔ PHỎNG LOGIC BẮT LỖI GACHA TRONG LUỒNG CHẠY:
                # Trong utils_flow.py, nếu check thấy '#arkoselabs' hoặc chữ 'captcha' hiện ra:
                # -> Trả về status="CAPTCHA_DETECTED"

                status = "SUCCESS"  # Mặc định giả lập thành công

                if status == "CAPTCHA_DETECTED":
                    captcha_triggered_count += 1
                    if captcha_triggered_count >= max_captcha_tolerance:
                        logger.error(f"[Worker-{worker_id}] IP dính Gacha vượt giới hạn cho phép!")
                        # Trả ngược tài khoản lại hàng chờ để chạy sau
                        await account_queue.put(account_data)
                        # Yêu cầu tổng đài Orchestrator đổi IP mạng ngay lập tức
                        await self.request_ip_rotation(worker_id)
                        account_queue.task_done()
                        continue

            except Exception as e:
                logger.error(f"[Worker-{worker_id}] Lỗi hệ thống: {e}")
                await account_queue.put(account_data)
                # Lỗi kết nối mạng cũng tiến hành kích hoạt đổi IP
                await self.request_ip_rotation(worker_id)

            finally:
                account_queue.task_done()

    async def start_orchestrator(self, accounts_list: list):
        """Hàm kích hoạt chạy đa luồng worker phối hợp"""
        queue = asyncio.Queue()
        for acc in accounts_list:
            await queue.put(acc)

        # Khởi tạo ExpressVPN phát đầu tiên trước khi chạy các worker
        logger.info("[Orchestrator] Khởi động IP sạch ban đầu cho ExpressVPN...")
        await vpn_manager.rotate_ip()

        tasks = []
        for i in range(self.max_workers):
            task = asyncio.create_task(self.worker_task(worker_id=i+1, account_queue=queue))
            tasks.append(task)

        await asyncio.gather(*tasks)
        logger.info("[Orchestrator] Đã hoàn thành toàn bộ hàng chờ tài khoản.")
