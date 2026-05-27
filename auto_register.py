import asyncio
import re
import os
import httpx
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# ============ CẤU HÌNH HỆ THỐNG & SELECTORS ============
# Vui lòng kiểm tra lại F12 trên OpenArt thực tế để điều chỉnh các Selector nếu cần.
CONFIG = {
    "ACCOUNTS_FILE": "accounts.txt",
    "SUCCESS_FILE": "reg_success.txt",
    "MAIL_API_BASE": "https://mail.cskh-group.com",
    
    # URL OpenArt
    "LOGIN_URL": "https://openart.ai/login",
    "SIGNUP_URL": "https://openart.ai/signup",
    
    # Selectors cho form đăng ký
    "SELECTOR_EMAIL_INPUT": 'input[type="email"]', # Ô nhập Email
    "SELECTOR_PASSWORD_INPUT": 'input[type="password"]', # Ô nhập Mật khẩu
    
    # Nút bấm submit đăng ký (Có thể dùng text "Sign Up" / "Continue" / "Register")
    "SELECTOR_SUBMIT_BUTTON": 'button:has-text("Sign Up"), button:has-text("Continue"), button:has-text("Register")',
    
    # Selector cho ô nhập mã OTP (nếu OpenArt yêu cầu nhập mã OTP 6 số)
    # OpenArt thường sử dụng 6 ô nhập mã độc lập hoặc 1 ô input chung.
    "SELECTOR_OTP_INPUT_CONTAINER": 'input[maxlength="1"]', # Trường hợp 6 ô input đơn lẻ
    "SELECTOR_OTP_SINGLE_INPUT": 'input[autocomplete="one-time-code"], input[name*="code" i], input[name*="otp" i]', # Trường hợp 1 ô input chung
    
    # Nút bấm Xác minh OTP
    "SELECTOR_VERIFY_BUTTON": 'button:has-text("Verify"), button:has-text("Continue"), button:has-text("Submit")',
    
    # Chờ nhận Email
    "OTP_MAX_ATTEMPTS": 12,        # Số lần thử check mail tối đa
    "OTP_POLL_INTERVAL": 5,        # Thời gian chờ giữa mỗi lần check mail (giây)
}

# ============ MODULE 1: ĐỌC/GHI FILE TÀI KHOẢN BẤT ĐỒNG BỘ ============
async def read_accounts(file_path: str) -> list[dict]:
    """Đọc danh sách tài khoản từ file accounts.txt."""
    accounts = []
    if not os.path.exists(file_path):
        print(f"[-] Không tìm thấy file nguồn tài khoản: {file_path}")
        return accounts

    try:
        # Sử dụng asyncio.to_thread để tránh blocking khi đọc file lớn
        def read_file_sync():
            acc_list = []
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or "|" not in line or line.startswith("#"):
                        continue
                    parts = line.split("|")
                    if len(parts) >= 2:
                        acc_list.append({
                            "email": parts[0].strip(),
                            "password": parts[1].strip()
                        })
            return acc_list

        accounts = await asyncio.to_thread(read_file_sync)
        print(f"[+] Đã tải thành công {len(accounts)} tài khoản từ file.")
    except Exception as e:
        print(f"[-] Lỗi khi đọc file tài khoản: {e}")
    return accounts

async def save_success_account(email: str, password: str, file_path: str):
    """Ghi nhận tài khoản đăng ký thành công vào file success."""
    try:
        def write_sync():
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(f"{email}|{password}\n")
        await asyncio.to_thread(write_sync)
        print(f"[+] Đã lưu tài khoản thành công: {email}")
    except Exception as e:
        print(f"[-] Không thể ghi file lưu thành công cho {email}: {e}")


# ============ MODULE 2: GỌI API MAIL HỆ THỐNG (HTTPX) ============
async def fetch_activation_data(email: str) -> dict:
    """
    Gọi API check hòm thư để lấy OTP 6 số hoặc Link kích hoạt từ OpenArt.
    Trả về dict dạng: {"otp": "123456", "link": None} hoặc {"otp": None, "link": "https://..."}
    """
    url = f"{CONFIG['MAIL_API_BASE']}/api/inbox/{email}"
    print(f"[*] Đang kiểm tra hộp thư của: {email}...")

    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(1, CONFIG["OTP_MAX_ATTEMPTS"] + 1):
            try:
                response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if response.status_code == 200:
                    data = response.json()
                    messages = data.get("messages", data.get("emails", []))
                    
                    # Tìm mail từ openart.ai
                    for msg in messages:
                        sender = msg.get("from", "").lower()
                        subject = msg.get("subject", "").lower()
                        text_content = msg.get("text", "")
                        html_content = msg.get("html", "")
                        
                        # Kết hợp cả text và html để quét regex
                        full_content = f"{subject} {text_content} {html_content}"
                        
                        if "openart" in sender or "openart" in subject:
                            # 1. Tìm Link kích hoạt chứa openart.ai
                            link_match = re.search(r"https://openart\.ai/[^\s\"'>]+", full_content)
                            if link_match:
                                link = link_match.group(0)
                                print(f"[+] Tìm thấy link kích hoạt: {link}")
                                return {"otp": None, "link": link}

                            # 2. Tìm mã OTP (6 số)
                            otp_match = re.search(r"\b\d{6}\b", full_content)
                            if otp_match:
                                otp = otp_match.group(0)
                                print(f"[+] Tìm thấy mã OTP: {otp}")
                                return {"otp": otp, "link": None}
                
                print(f"[*] (Lần thử {attempt}/{CONFIG['OTP_MAX_ATTEMPTS']}) Chưa nhận được mail xác nhận. Chờ 5s...")
            except Exception as e:
                print(f"[-] (Lần thử {attempt}) Lỗi kết nối API Mail: {e}")
                
            await asyncio.sleep(CONFIG["OTP_POLL_INTERVAL"])
            
    print(f"[-] Hết thời gian chờ! Không nhận được email xác nhận từ OpenArt cho: {email}")
    return {"otp": None, "link": None}


# ============ MODULE 3: BROWSER AUTOMATION (PLAYWRIGHT) ============
async def process_registration(email: str, password: str) -> bool:
    """Thực thi chu trình đăng ký bằng Playwright cho 1 tài khoản."""
    print(f"\n==================================================")
    print(f"[*] Bắt đầu xử lý đăng ký tài khoản: {email}")
    print(f"==================================================")

    # Khởi tạo Stealth
    stealth = Stealth()

    async with async_playwright() as p:
        # Khởi chạy Chromium (headless=False để dễ giám sát)
        browser = await p.chromium.launch(headless=False)
        
        # Thiết lập context và user agent sạch để bypass Cloudflare
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        
        page = await context.new_page()
        # Áp dụng Stealth để che dấu Playwright
        await stealth.apply_stealth_async(page)

        try:
            # 1. Đi tới trang Login/Signup
            print("[*] Đang điều hướng tới OpenArt Login...")
            await page.goto(CONFIG["LOGIN_URL"], wait_until="domcontentloaded", timeout=40000)
            await asyncio.sleep(2.0)

            # Chuyển sang form Đăng ký (nếu trang mặc định đang là Login)
            if "signup" not in page.url:
                try:
                    signup_tab = page.locator('a[href*="signup"], button:has-text("Sign up"), button:has-text("Register")').first
                    if await signup_tab.is_visible(timeout=3000):
                        await signup_tab.click()
                        await asyncio.sleep(1.0)
                except Exception:
                    print("[*] Chuyển hướng trực tiếp tới trang Signup...")
                    await page.goto(CONFIG["SIGNUP_URL"], wait_until="domcontentloaded", timeout=30000)

            # 2. Điền email vào form
            print("[*] Điền thông tin Email...")
            email_input = page.locator(CONFIG["SELECTOR_EMAIL_INPUT"]).first
            await email_input.wait_for(state="visible", timeout=15000)
            await email_input.click()
            await email_input.fill(email)
            await asyncio.sleep(0.5)

            # 3. Điền mật khẩu
            print("[*] Điền thông tin Password...")
            password_input = page.locator(CONFIG["SELECTOR_PASSWORD_INPUT"]).first
            await password_input.wait_for(state="visible", timeout=5000)
            await password_input.click()
            await password_input.fill(password)
            await asyncio.sleep(0.5)

            # 4. Click Submit Đăng ký
            print("[*] Bấm nút Submit Đăng ký...")
            submit_btn = page.locator(CONFIG["SELECTOR_SUBMIT_BUTTON"]).first
            await submit_btn.wait_for(state="visible", timeout=5000)
            await submit_btn.click()
            
            # Chờ một lúc để hệ thống OpenArt gửi email xác nhận đi
            await asyncio.sleep(5.0)

            # 5. Gọi API Mail hệ thống lấy thông tin OTP hoặc Link
            activation_data = await fetch_activation_data(email)
            
            # Trường hợp 1: Nhận được Link kích hoạt
            if activation_data["link"]:
                print("[*] Tiến hành xác thực bằng Link kích hoạt...")
                # Mở tab mới chạy link kích hoạt
                activation_page = await context.new_page()
                await stealth.apply_stealth_async(activation_page)
                await activation_page.goto(activation_data["link"], wait_until="domcontentloaded", timeout=40000)
                await asyncio.sleep(5.0)
                print("[+] Xác thực qua Link hoàn tất.")
                await activation_page.close()
                await browser.close()
                return True

            # Trường hợp 2: Nhận được mã OTP 6 số
            elif activation_data["otp"]:
                otp_code = activation_data["otp"]
                print(f"[*] Tiến hành nhập mã OTP: {otp_code}...")

                # Kiểm tra dạng ô nhập OTP: Nhiều ô đơn lẻ hay 1 ô chung
                otp_inputs = page.locator(CONFIG["SELECTOR_OTP_INPUT_CONTAINER"])
                otp_count = await otp_inputs.count()

                if otp_count >= 6:
                    print("[*] Điền OTP vào 6 ô độc lập...")
                    for idx in range(6):
                        await otp_inputs.nth(idx).fill(otp_code[idx])
                        await asyncio.sleep(0.1)
                else:
                    print("[*] Điền OTP vào ô input chung...")
                    single_otp_input = page.locator(CONFIG["SELECTOR_OTP_SINGLE_INPUT"]).first
                    await single_otp_input.wait_for(state="visible", timeout=5000)
                    await single_otp_input.click()
                    await single_otp_input.fill(otp_code)

                await asyncio.sleep(0.5)

                # Bấm xác nhận OTP
                verify_btn = page.locator(CONFIG["SELECTOR_VERIFY_BUTTON"]).first
                if await verify_btn.is_visible(timeout=3000):
                    await verify_btn.click()
                
                # Đợi hệ thống phản hồi xác nhận thành công
                await asyncio.sleep(6.0)
                print("[+] Xác thực OTP hoàn tất.")
                await browser.close()
                return True

            else:
                print("[-] Đăng ký thất bại do không có thông tin xác thực từ Email.")
                await browser.close()
                return False

        except Exception as e:
            print(f"[-] Gặp lỗi trong quá trình tự động hóa trình duyệt: {e}")
            await browser.close()
            return False


# ============ MODULE 4: HÀM ĐIỀU PHỐI CHÍNH (MAIN PROCESS) ============
async def main():
    print("=" * 60)
    print("      OPENART.AI AUTO REGISTER SYSTEM - PLAYWRIGHT")
    print("=" * 60)

    # 1. Đọc danh sách tài khoản cần đăng ký
    accounts = await read_accounts(CONFIG["ACCOUNTS_FILE"])
    if not accounts:
        print("[-] Không có tài khoản nào được nạp hoặc file trống. Dừng chương trình.")
        return

    success_count = 0
    fail_count = 0

    # 2. Lặp qua từng tài khoản để thực hiện đăng ký tuần tự
    for idx, acc in enumerate(accounts, start=1):
        email = acc["email"]
        password = acc["password"]
        
        print(f"\n[*] Đang xử lý tài khoản {idx}/{len(accounts)}")
        
        try:
            # Thực hiện đăng ký
            success = await process_registration(email, password)
            
            if success:
                # Ghi nhận thành công
                await save_success_account(email, password, CONFIG["SUCCESS_FILE"])
                success_count += 1
            else:
                print(f"[-] Đăng ký thất bại cho tài khoản: {email}")
                fail_count += 1
                
        except Exception as e:
            print(f"[-] Lỗi crash ngoài tầm kiểm soát khi xử lý {email}: {e}")
            fail_count += 1
            
        # Nghỉ giãn cách giữa các tài khoản
        await asyncio.sleep(3.0)

    print("\n" + "=" * 60)
    print(f"   KẾT QUẢ ĐĂNG KÝ: Tổng={len(accounts)} | Thành công={success_count} | Thất bại={fail_count}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
