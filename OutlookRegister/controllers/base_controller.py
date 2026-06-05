import os
import time
import json
import random
import threading
from faker import Faker
from abc import ABC, abstractmethod

try:
    from utils import CONFIG_PATH, ROOT_CONFIG_PATH, RESULTS_DIR
except ImportError:
    from ..utils import CONFIG_PATH, ROOT_CONFIG_PATH, RESULTS_DIR

OUTLOOK_SIGNUP_URL = "https://signup.live.com/signup?lic=1"
OUTLOOK_NAVIGATION_TIMEOUT_MS = 45000
OUTLOOK_READY_TIMEOUT_MS = 45000
EMAIL_INPUT_SELECTOR = "input[id='usernameInput'], input[id='MemberName'], input[name='MemberName'], input[type='email'], input[name='loginfmt'], input[autocomplete='username']"

def get_navigation_timeout_ms():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return max(5, int(data.get("timeout_secs", 45))) * 1000
    except Exception:
        return OUTLOOK_NAVIGATION_TIMEOUT_MS

def stop_page_loading(page):
    try:
        page.evaluate("window.stop()")
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

def wait_for_outlook_ready(page, timeout_ms=None):
    page.wait_for_selector(EMAIL_INPUT_SELECTOR, state="visible", timeout=timeout_ms or OUTLOOK_READY_TIMEOUT_MS)

def parse_proxy_config(proxy_value):
    if not proxy_value:
        return None

    if isinstance(proxy_value, dict):
        server = str(proxy_value.get("server", "")).strip()
        if not server:
            return None
        if "://" not in server:
            server = f"http://{server}"
        proxy_config = {"server": server}
        username = proxy_value.get("username")
        password = proxy_value.get("password")
        if username is not None and str(username).strip():
            proxy_config["username"] = str(username).strip()
        if password is not None and str(password).strip():
            proxy_config["password"] = str(password).strip()
        return proxy_config

    raw = str(proxy_value).strip()
    if not raw:
        return None
    if "://" in raw:
        return {"server": raw}

    parts = raw.split(":")
    if len(parts) >= 4:
        host = parts[0].strip()
        port = parts[1].strip()
        username = parts[2].strip()
        password = ":".join(parts[3:]).strip()
        return {
            "server": f"http://{host}:{port}",
            "username": username,
            "password": password,
        }
    if len(parts) >= 2:
        host = parts[0].strip()
        port = parts[1].strip()
        return {"server": f"http://{host}:{port}"}
    return {"server": raw}

def build_browser_launch_args(proxy_value, bypass=None):
    launch_args = {}
    proxy_settings = parse_proxy_config(proxy_value)
    if proxy_settings:
        if bypass:
            proxy_settings = dict(proxy_settings)
            proxy_settings["bypass"] = bypass
        launch_args["proxy"] = proxy_settings
    return launch_args

def short_proxy(proxy_value, limit=24):
    if not proxy_value:
        return "DIRECT"
    if isinstance(proxy_value, dict):
        proxy_value = proxy_value.get("server") or str(proxy_value)
    proxy_value = str(proxy_value)
    return proxy_value if len(proxy_value) <= limit else proxy_value[:limit - 3] + "..."

def load_outlook_signup_page(page, proxy_value=""):
    navigation_timeout_ms = get_navigation_timeout_ms()
    page.set_default_navigation_timeout(navigation_timeout_ms)
    page.set_default_timeout(navigation_timeout_ms)
    try:
        page.goto(OUTLOOK_SIGNUP_URL, timeout=navigation_timeout_ms, wait_until="domcontentloaded")
    except Exception as nav_err:
        stop_page_loading(page)
        proxy_hint = f" qua proxy {short_proxy(proxy_value)}" if proxy_value else " truc tiep"
        raise TimeoutError(f"Outlook khong phan hoi sau {navigation_timeout_ms // 1000}s{proxy_hint}: {nav_err}") from nav_err
    try:
        wait_for_outlook_ready(page, navigation_timeout_ms)
    except Exception as ready_err:
        stop_page_loading(page)
        raise TimeoutError(f"Outlook da mo nhung khong thay o email. URL={page.url}") from ready_err

class BaseBrowserController(ABC):
    """
    所有浏览器通用的接口和共享逻辑
    """

    def __init__(self):
        # Đọc config.json của OutlookRegister
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.wait_time = data['bot_protection_wait'] * 1000
        self.max_captcha_retries = data['max_captcha_retries']
        self.enable_oauth2 = data["oauth2"]['enable_oauth2']
        self.email_suffix = data['email_suffix']
        self.headless = data.get("headless", False)

        # Đọc proxy động từ config.json tổng ở thư mục cha
        self.proxy = ""
        local_proxies = data.get("proxies") or data.get("proxy_list") or []
        if isinstance(local_proxies, list) and local_proxies:
            self.proxy = random.choice(local_proxies)
        else:
            local_proxy = data.get('proxy', '')
            if isinstance(local_proxy, dict):
                self.proxy = local_proxy if str(local_proxy.get("server", "")).strip() else ""
            elif str(local_proxy).strip():
                self.proxy = str(local_proxy).strip()
        if not self.proxy and data.get("use_parent_proxies", False) and os.path.exists(ROOT_CONFIG_PATH):
            try:
                with open(ROOT_CONFIG_PATH, "r", encoding="utf-8") as f:
                    parent_config = json.load(f)
                proxies_list = parent_config.get("PROXIES", [])
                if proxies_list:
                    selected = random.choice(proxies_list)
                    self.proxy = selected
                    print(f"[Proxy] Phan bo proxy ngau nhien cho luong: {short_proxy(selected)}")
            except Exception as e:
                print(f"[Warning] Khong the doc file config.json tong de lay proxy: {e}")

        # Fallback sang proxy trong config cục bộ nếu không lấy được
        self.thread_local = threading.local()
        self.cleanup_lock = threading.Lock()
        self.active_resources = []  # 记录资源以便关闭

        self.results_dir = RESULTS_DIR
        os.makedirs(self.results_dir, exist_ok=True)


    @abstractmethod
    def launch_browser(self):
        """
        获取浏览器实例,返回playwright_instance, browser_instance
        """
        pass

    @abstractmethod
    def handle_captcha(self, page):
        """
        验证码处理流程
        """
        pass

    @abstractmethod
    def clean_up(self, page=None, type = "all_browser"):
        """
        清理自己创建的内容
        一个是单进程结束后关闭进程，另一个是程序结束后清除所有内容
        """
        pass

    @abstractmethod
    def get_thread_page(self):
        """
        返回页面
        """


    def get_thread_browser(self):
        """
        通用逻辑:获取不同进程的浏览器
        """

        if not hasattr(self.thread_local,"browser"):

            p, b  = self.launch_browser()
            if not p:
                return False

            self.thread_local.playwright = p
            self.thread_local.browser = b

            with self.cleanup_lock:
                self.active_resources.append((p, b))

        return self.thread_local.browser

    def outlook_register(self, page, email, password, progress_callback=None):
        """
        通用逻辑:注册邮箱
        """
        def ws(step, message):
            if progress_callback:
                progress_callback(step, message)
            else:
                print(f"[Step {step}] {message}")

        try:
            from faker import Faker
            fake = Faker()
            lastname = fake.last_name()
            firstname = fake.first_name()
        except Exception:
            firstnames = ["John", "David", "James", "Robert", "Michael", "William", "Richard", "Thomas", "Charles", "Daniel", "Matthew", "Anthony", "Mark", "Donald", "Steven", "Paul", "Andrew", "Joshua", "Kenneth", "Kevin"]
            lastnames = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis", "Wilson", "Anderson", "Taylor", "Thomas", "Moore", "Martin", "Jackson", "Thompson", "White", "Harris", "Sanchez", "Clark", "Ramirez"]
            firstname = random.choice(firstnames)
            lastname = random.choice(lastnames)

        year = str(random.randint(1960, 2005))
        month = str(random.randint(1, 12))
        day = str(random.randint(1, 28))

        try:
            max_attempts = 2
            for attempt in range(1, max_attempts + 1):
                try:
                    print(f"[Info] Dang tai trang chu dang ky Outlook (Lan thu {attempt}/{max_attempts})...")
                    load_outlook_signup_page(page, self.proxy)
                    break
                except Exception as e:
                    if attempt == max_attempts:
                        raise e
                    print(f"[Warning] Loi ket noi trang chu ({e}), dang thu lai sau 2s...")
                    page.wait_for_timeout(2000)
            # Thu nhan nut dong y va tiep tuc (accept cookie / terms neu co)
            for selector in ['button:has-text("同意并继续")', 'button:has-text("Accept")', 'button:has-text("Đồng ý")', '#acceptButton']:
                try:
                    btn = page.locator(selector).first
                    if btn.is_visible(timeout=3000):
                        btn.click()
                        break
                except:
                    continue
            start_time = time.time()
            page.wait_for_timeout(1000)
        except:
            print("[Error: IP] - IP chat luong kem, khong the vao trang dang ky.")
            return False

        try:
            # === BƯỚC 1: NHẬP EMAIL ===
            ws(2, "Dien email muon tao...")
            try:
                page.wait_for_selector(EMAIL_INPUT_SELECTOR, state="visible", timeout=20000)
                print("[+] Da tim thay o nhap lieu Email thanh cong.")
            except Exception as selector_err:
                print(f"[-] Khong tim thay o nhap Email do timeout hoac doi giao dien: {selector_err}")
                try:
                    page.screenshot(path="outlook_error_debug.png")
                except Exception as screenshot_err:
                    print(f"[-] Khong chup duoc screenshot debug: {screenshot_err}")
                raise
            email_input = page.locator(EMAIL_INPUT_SELECTOR).first
            email_input.fill(email)

            # Chọn đúng miền nếu trang có dropdown/gợi ý miền email
            try:
                if self.email_suffix == "@outlook.com":
                    page.get_by_text("@outlook.com").click(timeout=3000)
                elif self.email_suffix == "@hotmail.com":
                    try:
                        page.get_by_text("@outlook.com").click(timeout=3000)
                    except Exception:
                        pass
                    try:
                        page.locator('[role="option"]:text-is("@hotmail.com")').click(timeout=3000)
                    except Exception:
                        pass
            except Exception:
                pass

            page.locator('button[type="submit"]').click()
            page.wait_for_timeout(1500)

            # === BƯỚC 2: NHẬP PASSWORD ===
            ws(3, "Nhap password...")
            page.wait_for_selector('input[type="password"]', timeout=15000)
            page.locator('input[type="password"]').fill(password)
            page.locator('button[type="submit"]').click()
            page.wait_for_timeout(1500)

            # === BƯỚC 3: NGÀY THÁNG NĂM SINH (ADD DETAILS) ===
            ws(4, "Dien ngay thang nam sinh...")
            page.wait_for_selector('input[name="BirthYear"]', timeout=15000)

            # 1. Chọn Month qua combobox
            page.locator('#BirthMonthDropdown').first.click(force=True)
            page.wait_for_timeout(800)
            month_selected = False
            # Ánh xạ tên tháng tiếng Anh
            english_months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
            month_name = english_months[int(month) - 1]

            for lb in page.locator('[role="listbox"]').all():
                if lb.is_visible():
                    try:
                        # Thử tìm theo tháng tiếng Anh hoặc số tháng tùy locale
                        opt = lb.locator(f'[role="option"]:has-text("{month_name}")').first
                        if not opt.is_visible():
                            opt = lb.locator(f'[role="option"]:has-text("{month}")').first
                        opt.click(force=True)
                        month_selected = True
                        break
                    except:
                        continue
            if not month_selected:
                # Fallback nếu không click được listbox: bấm phím mũi tên
                page.locator('#BirthMonthDropdown').first.press("ArrowDown")
                page.locator('#BirthMonthDropdown').first.press("Enter")

            page.wait_for_timeout(500)

            # 2. Chọn Day qua combobox
            page.locator('#BirthDayDropdown').first.click(force=True)
            page.wait_for_timeout(800)
            day_selected = False
            for lb in page.locator('[role="listbox"]').all():
                if lb.is_visible():
                    try:
                        opt = lb.locator(f'[role="option"]:text-is("{day}")').first
                        opt.click(force=True)
                        day_selected = True
                        break
                    except:
                        continue
            if not day_selected:
                page.locator('#BirthDayDropdown').first.press("ArrowDown")
                page.locator('#BirthDayDropdown').first.press("Enter")

            page.wait_for_timeout(500)

            # 3. Nhập Year sau cùng
            page.locator('input[name="BirthYear"]').fill("")
            page.locator('input[name="BirthYear"]').type(year, delay=100)
            page.wait_for_timeout(500)

            # Click Next
            submit_btn = page.locator('button[type="submit"]').first
            try:
                submit_btn.click(timeout=5000)
            except Exception:
                try:
                    submit_btn.click(force=True, timeout=5000)
                except Exception:
                    page.keyboard.press('Enter')
            page.wait_for_timeout(1500)

            # === BƯỚC 4: NHẬP HỌ TÊN (ADD NAME) ===
            ws(5, "Dien ho ten...")
            page.wait_for_selector('input[name="firstNameInput"]', timeout=15000)
            page.locator('input[name="firstNameInput"]').fill(firstname)
            page.locator('input[name="lastNameInput"]').fill(lastname)

            # Chờ bảo vệ bot đủ thời gian nếu cần thiết
            if time.time() - start_time < self.wait_time / 1000:
                page.wait_for_timeout(self.wait_time - (time.time() - start_time) * 1000)

            submit_btn = page.locator('button[type="submit"]').first
            try:
                submit_btn.click(timeout=5000)
            except Exception:
                try:
                    submit_btn.click(force=True, timeout=5000)
                except Exception:
                    page.keyboard.press('Enter')

            # Đợi load qua trang xác thực
            ws(6, "Cho he thong hoan thanh xac thuc...")
            page.locator('span > [href="https://go.microsoft.com/fwlink/?LinkID=521839"]').wait_for(state='detached', timeout=22000)
            page.wait_for_timeout(400)

            if page.get_by_text('异常活动').count() or page.get_by_text('维护').count() > 0:
                print("[Error: IP or browser] - IP nay dang ky qua nhanh, vui long kiem tra lai.")
                return False

            if page.locator('iframe#enforcementFrame').count() > 0:
                print("[Error: FunCaptcha] - Gap loi xac minh capcha.")
                return False

            captcha_result = self.handle_captcha(page)
            if not captcha_result:
                raise TimeoutError

        except Exception as e:
            print(f"[Error: Signup] - Gap loi: {e}")
            return False

        filename = os.path.join(self.results_dir, 'logged_email.txt' if self.enable_oauth2 else 'unlogged_email.txt')
        with open(filename, 'a', encoding='utf-8') as f:
            f.write(f"{email}{self.email_suffix}: {password}\n")
        print(f'[Success: Email Registration] - {email}{self.email_suffix}: {password}')

        # Đồng bộ tự động sang danh sách tài khoản của OpenArt ở thư mục cha
        parent_accounts_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "accounts.txt"))
        try:
            with open(parent_accounts_path, 'a', encoding='utf-8') as f_parent:
                f_parent.write(f"{email}{self.email_suffix}|{password}|ACTIVE|\n")
        except Exception as sync_err:
            print(f"[Warning] Khong the dong bo tai khoan sang file accounts.txt goc: {sync_err}")

        if not self.enable_oauth2:
            return True

        try:
            page.locator('[aria-label="新邮件"]').wait_for(timeout=32000)
            return True
        except:
            print('[Error: Timeout] - Hop thu chua khoi tao.')
            return False
