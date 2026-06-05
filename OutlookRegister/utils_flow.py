# OutlookRegister/utils_flow.py
import asyncio
import logging
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError
# Import class mô phỏng người thật bạn đã xây lại ở bước trước
from playwright_input_utils import PlaywrightInputUtils

logger = logging.getLogger(__name__)

async def check_gacha_presence(page: Page, timeout_ms: int = 1500) -> bool:
    """
    Hàm bổ trợ kiểm tra nhanh xem các iframe hoặc khung chứa Gacha (Arkose Labs)
    của Microsoft có xuất hiện trên màn hình hay không.
    """
    gacha_selectors = [
        "iframe[src*='arkoselabs']",
        "iframe[id*='enforcement']",
        "#inline-captcha-container",
        "#identitity_verification_container",
        "div[id*='captcha']"
    ]

    # Kiểm tra đồng thời các selector gacha xem có cái nào hiển thị không
    for selector in gacha_selectors:
        try:
            if await page.locator(selector).is_visible():
                return True
        except Exception:
            continue
    return False

async def run_outlook_signup_flow(page: Page, account_data: dict, worker_id: int = 0) -> tuple[str, str | None]:
    """
    Luồng chạy đăng ký Hotmail tự động hóa thông minh.
    Trả về: (STATUS, EXTRA_INFO)
    Các trạng thái có thể trả về:
    - "SUCCESS": Tạo thành công
    - "CAPTCHA_DETECTED": Dính gacha, cần báo Orchestrator đổi IP
    - "ERROR": Lỗi hệ thống khác
    """
    email = account_data.get("email")
    password = account_data.get("password")

    try:
        # Bước 1: Ghé thăm trang mồi để tích lũy cookie (Nuôi độ Trust ban đầu)
        logger.info(f"[W{worker_id:02d}] Đang đi dạo mồi tạo lịch sử duyệt web...")
        try:
            await page.goto("https://google.com", wait_until="networkidle", timeout=15000)
            await asyncio.sleep(3)
        except Exception:
            pass # Không để việc trang mồi lỗi làm chết luồng chính

        # Bước 2: Truy cập trang đăng ký chính thức của Hotmail
        logger.info(f"[W{worker_id:02d}] Đang truy cập trang đăng ký Hotmail/Outlook...")
        await page.goto("https://live.com", wait_until="networkidle")
        await asyncio.sleep(2)

        # Kiểm tra gacha ngay khi vừa tải trang (Trường hợp IP quá nát bị chặn ngay từ đầu)
        if await check_gacha_presence(page):
            logger.warning(f"[W{worker_id:02d}] ⚠️ IP bị chặn ngay khi vừa tải trang. Phát hiện Gacha!")
            return "CAPTCHA_DETECTED", "Blocked on land"

        # Bước 3: Điền Email bằng phương thức người thật
        logger.info(f"[W{worker_id:02d}] Đang điền email: {email}")
        await PlaywrightInputUtils.type_humanlike(page, "input[name='MemberName']", email)
        await PlaywrightInputUtils.click_humanlike(page, "input[type='submit']")
        await asyncio.sleep(1.5)

        # Kiểm tra gacha sau khi bấm Next bước Email
        if await check_gacha_presence(page):
            logger.warning(f"[W{worker_id:02d}] ⚠️ Microsoft kích hoạt Gacha sau khi nhập Email.")
            return "CAPTCHA_DETECTED", "Gacha after email"

        # Bước 4: Điền Mật khẩu bằng phương thức người thật
        logger.info(f"[W{worker_id:02d}] Đang điền mật khẩu...")
        await PlaywrightInputUtils.type_humanlike(page, "input[name='PasswordInput']", password)
        # Bỏ tích chọn nhận email quảng cáo nếu có để giảm hành vi bot
        try:
            if await page.locator("input[name='iOptinEmail']").is_visible():
                await PlaywrightInputUtils.click_humanlike(page, "input[name='iOptinEmail']")
        except Exception:
            pass

        await PlaywrightInputUtils.click_humanlike(page, "input[type='submit']")

        # Chờ trang xử lý thông tin mật khẩu
        await page.wait_for_load_state("networkidle", timeout=10000)
        await asyncio.sleep(3)

        # Bước 5: Kiểm tra gacha ở chốt chặn quyết định (Sau bước Password)
        if await check_gacha_presence(page):
            logger.warning(f"[W{worker_id:02d}] ⚠️ Microsoft kích hoạt Gacha bắt giải sau khi nhập Password.")
            return "CAPTCHA_DETECTED", "Gacha after password"

        # Bước 6: Điền các thông tin phụ nếu luồng đi tiếp thành công (Họ tên, Ngày sinh...)
        # (Đoạn này bạn giữ nguyên logic điền First Name/Last Name hiện tại của bạn bằng cách chuyển sang `PlaywrightInputUtils`)

        # Bước 7: Xác nhận kết quả thành công cuối cùng
        # Chờ sự xuất hiện của Dashboard hoặc token thành công
        try:
            # Selector đặc trưng khi đăng ký thành công và chuyển hướng vào hộp thư
            await page.wait_for_selector("[data-report-event*='SignupSuccess']", state="attached", timeout=20000)
            logger.success(f"[W{worker_id:02d}] 🎉 Đăng ký tài khoản thành công: {email}")
            return "SUCCESS", None
        except PlaywrightTimeoutError:
            # Check lại lần cuối xem có bị đẩy về màn hình gacha ẩn không
            if await check_gacha_presence(page):
                return "CAPTCHA_DETECTED", "Late gacha trigger"

            # Nếu không thấy gacha mà vẫn timeout, có thể do mạng chậm hoặc giao diện thay đổi
            return "ERROR", "Timeout waiting for success indicator"

    except Exception as e:
        logger.error(f"[W{worker_id:02d}] Lỗi không xác định trong flow: {str(e)}")
        return "ERROR", str(e)
