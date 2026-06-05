# vpn_manager.py
import os
import random
import asyncio
import httpx
import logging

try:
    from logger_setup import get_logger
    logger = get_logger()
except ImportError:
    class FallbackLogger:
        def info(self, msg): print(f"[INFO] {msg}")
        def warning(self, msg): print(f"[WARN] {msg}")
        def error(self, msg): print(f"[ERROR] {msg}")
        def success(self, msg): print(f"[SUCCESS] {msg}")
    logger = FallbackLogger()

class Config:
    def __init__(self):
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        data = {}
        if os.path.exists(config_path):
            try:
                import json
                with open(config_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except Exception:
                pass
        self.change_ip_mode = str(data.get("change_ip_mode", "on")).lower()
        self.num_fail_to_change_ip = int(data.get("num_fail_to_change_ip", 3))

class VpnManager:
    """Điều khiển ExpressVPN trực tiếp bằng lệnh CLI bất đồng bộ (Async Subprocess)"""
    def __init__(self):
        # Đường dẫn dẫn tới file thực thi chính của ExpressVPN (Bản Desktop Windows)
        self.vpn_path = r"C:\Program Files\ExpressVPN\expressvpn-client.exe"
        self._lock = asyncio.Lock()
        self._changing = False
        self._config = Config()
        self._fail_count = 0
        self._fail_lock = asyncio.Lock()

    @property
    def is_changing(self):
        return self._changing

    async def increment_fail(self):
        async with self._fail_lock:
            self._fail_count += 1
            if self._fail_count >= self._config.num_fail_to_change_ip:
                self._fail_count = 0
                return True
            return False

    async def reset_fail(self):
        async with self._fail_lock:
            self._fail_count = 0

    async def get_public_ip(self):
        """Sử dụng httpx async để check IP công khai hiện tại không gây nghẽn luồng"""
        endpoints = ["https://api.ipify.org", "https://ifconfig.me/ip"]
        async with httpx.AsyncClient(timeout=8.0) as client:
            for url in endpoints:
                try:
                    response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                    if response.status_code == 200:
                        ip = response.text.strip()
                        if ip:
                            return ip
                except Exception:
                    continue
        return ""

    async def rotate_ip(self):
        """Hàm API chính được gọi bởi advanced_orchestrator.py"""
        if self._changing:
            logger.info("[VPN] Một worker khác đang ra lệnh đổi IP, vui lòng chờ xếp hàng...")
            while self._changing:
                await asyncio.sleep(1.0)
            return True

        async with self._lock:
            if self._changing:
                return True
            self._changing = True
            try:
                return await self._do_change_ip()
            finally:
                self._changing = False

    async def _do_change_ip(self):
        if self._config.change_ip_mode == "off":
            logger.info("[VPN] Chế độ Đổi IP (change_ip_mode) đang tắt trong config.json")
            return False
        return await self._do_change_ip_evpn()

    async def _do_change_ip_evpn(self):
        try:
            if not os.path.exists(self.vpn_path):
                logger.error(f"[VPN] Không tìm thấy ExpressVPN tại: {self.vpn_path}")
                return False

            logger.info("[VPN] Gửi lệnh ngắt kết nối IP cũ (Disconnect)...")

            # Sử dụng async subprocess để chạy ngầm lệnh của Windows không làm đơ giao diện GUI
            process = await asyncio.create_subprocess_exec(
                self.vpn_path, "disconnect",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(process.communicate(), timeout=12.0)
            await asyncio.sleep(2.0)

            # Danh sách các vùng có kết nối nhanh, mượt để tạo Hotmail
            regions = ["singapore", "hongkong", "japan", "thailand", "taiwan"]
            selected_region = random.choice(regions)
            logger.info(f"[VPN] Tiến hành kết nối ExpressVPN tới quốc gia: {selected_region}")

            process = await asyncio.create_subprocess_exec(
                self.vpn_path, "connect", selected_region,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=25.0)

            if process.returncode == 0:
                new_ip = await self.get_public_ip()
                logger.success(f"[VPN] Đổi IP thành công sang vùng [{selected_region}] | IP mới: {new_ip}")
                return True

            logger.warning(f"[VPN] Kết nối vùng {selected_region} thất bại (Code={process.returncode}). Thử cấu hình Smart Location mặc định...")

            # Fallback về Smart Location của ExpressVPN nếu khu vực được chọn bị bận
            process = await asyncio.create_subprocess_exec(
                self.vpn_path, "connect",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=25.0)

            if process.returncode == 0:
                logger.success("[VPN] Kết nối lại thành công bằng Smart Location mặc định!")
                return True

            logger.error(f"[VPN] Lỗi nghiêm trọng: Cả Smart Location cũng không thể kết nối. Stderr: {stderr.decode().strip()}")
            return False

        except asyncio.TimeoutError:
            logger.error("[VPN] Lệnh gọi ExpressVPN CLI bị quá thời gian phản hồi (Timeout).")
            return False
        except Exception as e:
            logger.error(f"[VPN] Lỗi hệ thống khi điều khiển ExpressVPN CLI: {str(e)}")
            return False

# Export một instance toàn cục duy nhất để các file khác import vào dùng chung trạng thái khóa (Lock)
vpn_manager = VpnManager()
