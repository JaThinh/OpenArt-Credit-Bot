import asyncio
import re
import os
import httpx
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# ============ CAU HINH HE THONG & SELECTORS ============
# Vui long kiem tra lai F12 tren OpenArt thuc te de dieu chinh cac Selector neu can.
CONFIG = {
    "ACCOUNTS_FILE": "accounts.txt",
    "SUCCESS_FILE": "reg_success.txt",
    "MAIL_API_BASE": "https://mail.cskh-group.com",
    
    # URL OpenArt
    "LOGIN_URL": "https://openart.ai/login",
    "SIGNUP_URL": "https://openart.ai/signup",
    
    # Selectors cho form dang ky
    "SELECTOR_EMAIL_INPUT": 'input[type="email"]', # O nhap Email
    "SELECTOR_PASSWORD_INPUT": 'input[type="password"]', # O nhap Mat khau
    
    # Nut bam submit dang ky (Co the dung text "Sign Up" / "Continue" / "Register")
    "SELECTOR_SUBMIT_BUTTON": 'button:has-text("Sign Up"), button:has-text("Continue"), button:has-text("Register")',
    
    # Selector cho o nhap ma OTP (neu OpenArt yeu cau nhap ma OTP 6 so)
    # OpenArt thuong su dung 6 o nhap ma doc lap hoac 1 o input chung.
    "SELECTOR_OTP_INPUT_CONTAINER": 'input[maxlength="1"]', # Truong hop 6 o input don le
    "SELECTOR_OTP_SINGLE_INPUT": 'input[autocomplete="one-time-code"], input[name*="code" i], input[name*="otp" i]', # Truong hop 1 o input chung
    
    # Nut bam Xac minh OTP
    "SELECTOR_VERIFY_BUTTON": 'button:has-text("Verify"), button:has-text("Continue"), button:has-text("Submit")',
    
    # Cho nhan Email
    "OTP_MAX_ATTEMPTS": 12,        # So lan thu check mail toi da
    "OTP_POLL_INTERVAL": 5,        # Thoi gian cho giua moi lan check mail (giay)
}

# ============ MODULE 1: DOC/GHI FILE TAI KHOAN BAT DONG BO ============
async def read_accounts(file_path: str) -> list[dict]:
    """Doc danh sach tai khoan tu file accounts.txt."""
    accounts = []
    if not os.path.exists(file_path):
        print(f"[-] Khong tim thay file nguon tai khoan: {file_path}")
        return accounts

    try:
        # Su dung asyncio.to_thread de tranh blocking khi doc file lon
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
        print(f"[+] Da tai thanh cong {len(accounts)} tai khoan tu file.")
    except Exception as e:
        print(f"[-] Loi khi doc file tai khoan: {e}")
    return accounts

async def save_success_account(email: str, password: str, file_path: str):
    """Ghi nhan tai khoan dang ky thanh cong vao file success."""
    try:
        def write_sync():
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(f"{email}|{password}\n")
        await asyncio.to_thread(write_sync)
        print(f"[+] Da luu tai khoan thanh cong: {email}")
    except Exception as e:
        print(f"[-] Khong the ghi file luu thanh cong cho {email}: {e}")


# ============ MODULE 2: GOI API MAIL HE THONG (HTTPX) ============
async def fetch_activation_data(email: str) -> dict:
    """
    Goi API check hom thu de lay OTP 6 so hoac Link kich hoat tu OpenArt.
    Tra ve dict dang: {"otp": "123456", "link": None} hoac {"otp": None, "link": "https://..."}
    """
    url = f"{CONFIG['MAIL_API_BASE']}/api/inbox/{email}"
    print(f"[*] Dang kiem tra hop thu cua: {email}...")

    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(1, CONFIG["OTP_MAX_ATTEMPTS"] + 1):
            try:
                response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if response.status_code == 200:
                    data = response.json()
                    messages = data.get("messages", data.get("emails", []))
                    
                    # Tim mail tu openart.ai
                    for msg in messages:
                        sender = msg.get("from", "").lower()
                        subject = msg.get("subject", "").lower()
                        text_content = msg.get("text", "")
                        html_content = msg.get("html", "")
                        
                        # Ket hop ca text va html de quet regex
                        full_content = f"{subject} {text_content} {html_content}"
                        
                        if "openart" in sender or "openart" in subject:
                            # 1. Tim Link kich hoat chua openart.ai
                            link_match = re.search(r"https://openart\.ai/[^\s\"'>]+", full_content)
                            if link_match:
                                link = link_match.group(0)
                                print(f"[+] Tim thay link kich hoat: {link}")
                                return {"otp": None, "link": link}

                            # 2. Tim ma OTP (6 so)
                            otp_match = re.search(r"\b\d{6}\b", full_content)
                            if otp_match:
                                otp = otp_match.group(0)
                                print(f"[+] Tim thay ma OTP: {otp}")
                                return {"otp": otp, "link": None}
                
                print(f"[*] (Lan thu {attempt}/{CONFIG['OTP_MAX_ATTEMPTS']}) Chua nhan duoc mail xac nhan. Cho 5s...")
            except Exception as e:
                print(f"[-] (Lan thu {attempt}) Loi ket noi API Mail: {e}")
                
            await asyncio.sleep(CONFIG["OTP_POLL_INTERVAL"])
            
    print(f"[-] Het thoi gian cho! Khong nhan duoc email xac nhan tu OpenArt cho: {email}")
    return {"otp": None, "link": None}


# ============ MODULE 3: BROWSER AUTOMATION (PLAYWRIGHT) ============
async def process_registration(email: str, password: str) -> bool:
    """Thuc thi chu trinh dang ky bang Playwright cho 1 tai khoan."""
    print(f"\n==================================================")
    print(f"[*] Bat dau xu ly dang ky tai khoan: {email}")
    print(f"==================================================")

    # Khoi tao Stealth
    stealth = Stealth()

    async with async_playwright() as p:
        # Khoi chay Chromium (headless=False de de giam sat)
        browser = await p.chromium.launch(headless=False)
        
        # Thiet lap context va user agent sach de bypass Cloudflare
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        
        page = await context.new_page()
        # Ap dung Stealth de che dau Playwright
        await stealth.apply_stealth_async(page)

        try:
            # 1. Di toi trang Login/Signup
            print("[*] Dang dieu huong toi OpenArt Login...")
            await page.goto(CONFIG["LOGIN_URL"], wait_until="domcontentloaded", timeout=40000)
            await asyncio.sleep(2.0)

            # Chuyen sang form Dang ky (neu trang mac dinh dang la Login)
            if "signup" not in page.url:
                try:
                    signup_tab = page.locator('a[href*="signup"], button:has-text("Sign up"), button:has-text("Register")').first
                    if await signup_tab.is_visible(timeout=3000):
                        await signup_tab.click()
                        await asyncio.sleep(1.0)
                except Exception:
                    print("[*] Chuyen huong truc tiep toi trang Signup...")
                    await page.goto(CONFIG["SIGNUP_URL"], wait_until="domcontentloaded", timeout=30000)

            # 2. Dien email vao form
            print("[*] Dien thong tin Email...")
            email_input = page.locator(CONFIG["SELECTOR_EMAIL_INPUT"]).first
            await email_input.wait_for(state="visible", timeout=15000)
            await email_input.click()
            await email_input.fill(email)
            await asyncio.sleep(0.5)

            # 3. Dien mat khau
            print("[*] Dien thong tin Password...")
            password_input = page.locator(CONFIG["SELECTOR_PASSWORD_INPUT"]).first
            await password_input.wait_for(state="visible", timeout=5000)
            await password_input.click()
            await password_input.fill(password)
            await asyncio.sleep(0.5)

            # 4. Click Submit Dang ky
            print("[*] Bam nut Submit Dang ky...")
            submit_btn = page.locator(CONFIG["SELECTOR_SUBMIT_BUTTON"]).first
            await submit_btn.wait_for(state="visible", timeout=5000)
            await submit_btn.click()
            
            # Cho mot luc de he thong OpenArt gui email xac nhan di
            await asyncio.sleep(5.0)

            # 5. Goi API Mail he thong lay thong tin OTP hoac Link
            activation_data = await fetch_activation_data(email)
            
            # Truong hop 1: Nhan duoc Link kich hoat
            if activation_data["link"]:
                print("[*] Tien hanh xac thuc bang Link kich hoat...")
                # Mo tab moi chay link kich hoat
                activation_page = await context.new_page()
                await stealth.apply_stealth_async(activation_page)
                await activation_page.goto(activation_data["link"], wait_until="domcontentloaded", timeout=40000)
                await asyncio.sleep(5.0)
                print("[+] Xac thuc qua Link hoan tat.")
                await activation_page.close()
                await browser.close()
                return True

            # Truong hop 2: Nhan duoc ma OTP 6 so
            elif activation_data["otp"]:
                otp_code = activation_data["otp"]
                print(f"[*] Tien hanh nhap ma OTP: {otp_code}...")

                # Kiem tra dang o nhap OTP: Nhieu o don le hay 1 o chung
                otp_inputs = page.locator(CONFIG["SELECTOR_OTP_INPUT_CONTAINER"])
                otp_count = await otp_inputs.count()

                if otp_count >= 6:
                    print("[*] Dien OTP vao 6 o doc lap...")
                    for idx in range(6):
                        await otp_inputs.nth(idx).fill(otp_code[idx])
                        await asyncio.sleep(0.1)
                else:
                    print("[*] Dien OTP vao o input chung...")
                    single_otp_input = page.locator(CONFIG["SELECTOR_OTP_SINGLE_INPUT"]).first
                    await single_otp_input.wait_for(state="visible", timeout=5000)
                    await single_otp_input.click()
                    await single_otp_input.fill(otp_code)

                await asyncio.sleep(0.5)

                # Bam xac nhan OTP
                verify_btn = page.locator(CONFIG["SELECTOR_VERIFY_BUTTON"]).first
                if await verify_btn.is_visible(timeout=3000):
                    await verify_btn.click()
                
                # Doi he thong phan hoi xac nhan thanh cong
                await asyncio.sleep(6.0)
                print("[+] Xac thuc OTP hoan tat.")
                await browser.close()
                return True

            else:
                print("[-] Dang ky that bai do khong co thong tin xac thuc tu Email.")
                await browser.close()
                return False

        except Exception as e:
            print(f"[-] Gap loi trong qua trinh tu dong hoa trinh duyet: {e}")
            await browser.close()
            return False


# ============ MODULE 4: HAM DIEU PHOI CHINH (MAIN PROCESS) ============
async def main():
    print("=" * 60)
    print("      OPENART.AI AUTO REGISTER SYSTEM - PLAYWRIGHT")
    print("=" * 60)

    # 1. Doc danh sach tai khoan can dang ky
    accounts = await read_accounts(CONFIG["ACCOUNTS_FILE"])
    if not accounts:
        print("[-] Khong co tai khoan nao duoc nap hoac file trong. Dung chuong trinh.")
        return

    success_count = 0
    fail_count = 0

    # 2. Lap qua tung tai khoan de thuc hien dang ky tuan tu
    for idx, acc in enumerate(accounts, start=1):
        email = acc["email"]
        password = acc["password"]
        
        print(f"\n[*] Dang xu ly tai khoan {idx}/{len(accounts)}")
        
        try:
            # Thuc hien dang ky
            success = await process_registration(email, password)
            
            if success:
                # Ghi nhan thanh cong
                await save_success_account(email, password, CONFIG["SUCCESS_FILE"])
                success_count += 1
            else:
                print(f"[-] Dang ky that bai cho tai khoan: {email}")
                fail_count += 1
                
        except Exception as e:
            print(f"[-] Loi crash ngoai tam kiem soat khi xu ly {email}: {e}")
            fail_count += 1
            
        # Nghi gian cach giua cac tai khoan
        await asyncio.sleep(3.0)

    print("\n" + "=" * 60)
    print(f"   KET QUA DANG KY: Tong={len(accounts)} | Thanh cong={success_count} | That bai={fail_count}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
