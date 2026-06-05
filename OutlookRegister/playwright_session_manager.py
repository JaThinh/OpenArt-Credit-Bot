# playwright_session_manager.py
import os
import random
import logging
from .network_service import NetworkService

logger = logging.getLogger(__name__)

class PlaywrightSessionManager:
    def __init__(self, config_path="config.json"):
        self.config_path = config_path
        self.user_agents = self._load_user_agents()

    def _load_user_agents(self):
        """Đọc danh sách User-Agent Desktop từ file useragent.txt tại root"""
        ua_file = "useragent.txt"
        if os.path.exists(ua_file):
            with open(ua_file, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
                if lines:
                    return lines
        # Fallback UA nếu file trống hoặc lỗi
        return ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"]

    async def create_trust_context(self, browser):
        """
        Khởi tạo Context siêu Trust đồng bộ 100% với IP mạng của ExpressVPN.
        Hỗ trợ cả Playwright gốc, Patchright và Camoufox.
        """
        # 1. Lấy thông tin mạng thời gian thực
        ip_meta = await NetworkService.get_ip_metadata()

        # 2. Map nhanh mã quốc gia sang ngôn ngữ của trình duyệt (Locale)
        # Giúp tránh lỗi: IP ở Mỹ nhưng trình duyệt gửi tiếng Việt (vi-VN)
        locale_mapping = {
            "US": "en-US", "VN": "vi-VN", "SG": "en-SG",
            "TH": "th-TH", "KR": "ko-KR", "JP": "ja-JP",
            "DE": "de-DE", "FR": "fr-FR", "UK": "en-GB", "GB": "en-GB"
        }
        current_locale = locale_mapping.get(ip_meta["country_code"], "en-US")

        # 3. Lấy ngẫu nhiên 1 User-Agent Desktop chuẩn Windows
        selected_ua = random.choice(self.user_agents)

        logger.info(f"[Session] Cấu hình Context -> Locale: {current_locale} | Timezone: {ip_meta['timezone']}")

        # 4. Tạo cấu hình Context chuẩn hóa máy tính thật
        context = await browser.new_context(
            user_agent=selected_ua,
            viewport={"width": 1366, "height": 768}, # Độ phân giải PC phổ biến nhất
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
            locale=current_locale,
            timezone_id=ip_meta["timezone"],
            permissions=["geolocation"], # Bật quyền vị trí để tăng độ tin cậy
            ignore_https_errors=True
        )

        # 5. Inject mã Javascript nâng cao vào sâu trong nhân trình duyệt
        # Đoạn script này sẽ chạy trước khi mọi mã quét của Microsoft (Arkose Labs) kịp tải
        await context.add_init_script("""
            // 1. Ẩn hoàn toàn biến navigator.webdriver (Cờ báo hiệu bot tự động)
            if (navigator.webdriver !== undefined) {
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            }

            // 2. Định nghĩa cấu hình phần cứng thật (Tránh rò rỉ thông số máy ảo)
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
            Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

            // 3. Khớp danh sách ngôn ngữ ưu tiên với Locale của hệ thống
            Object.defineProperty(navigator, 'languages', {
                get: () => ['""" + current_locale + """', 'en']
            });

            // 4. Giả lập đối tượng Chrome Runtime để qua mặt bộ quét đặc biệt của Google/Microsoft
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {}
            };
        """)

        return context
