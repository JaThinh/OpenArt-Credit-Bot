# network_service.py
import httpx
import logging

logger = logging.getLogger(__name__)

class NetworkService:
    @staticmethod
    async def get_ip_metadata():
        """
        Tự động kiểm tra IP hiện tại (ExpressVPN/Proxy)
        để lấy Múi giờ (Timezone) và Mã quốc gia (Country Code).
        """
        url = "http://ip-api.com"
        try:
            # Sử dụng httpx async client trùng khớp với dependencies của project
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") == "success":
                        logger.info(f"[Network] IP Hiện tại: {data.get('query')} | Quốc gia: {data.get('countryCode')} | Múi giờ: {data.get('timezone')}")
                        return {
                            "timezone": data.get("timezone"),
                            "country_code": data.get("countryCode"),
                            "ip": data.get("query")
                        }
        except Exception as e:
            logger.error(f"[Network] Lỗi khi lấy IP Metadata: {str(e)}")

        # Cấu hình dự phòng (Fallback) nếu ExpressVPN đang đổi IP hoặc lỗi mạng
        return {
            "timezone": "America/New_York",
            "country_code": "US",
            "ip": "127.0.0.1"
        }
