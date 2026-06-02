import queue
import concurrent.futures
import json
import os
import random
import re
import string
import sys
import threading
import time
import unicodedata
from datetime import datetime
from utils import random_email, generate_strong_password, get_random_user_agent
from get_token import get_access_token

# Cấu hình UTF-8 cho console output trên Windows tránh lỗi UnicodeEncodeError
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    pass

# ============ STATE ============
bot_state = "OFFLINE"
stats = {"total": 0, "success": 0, "fail": 0}
workers = []
BAD_PROXY_BLACKLIST = set()
LIVE_PROXY_CACHE = {}
LIVE_PROXY_CACHE_TTL_SECONDS = 90.0
PROXY_PROBE_BATCH_SIZE = 16
PROXY_PROBE_WORKERS = 8
PROXY_PROBE_TIMEOUT_SECONDS = 1.2
proxy_cache_lock = threading.Lock()
should_stop = False
is_paused = False
lock = threading.Lock()
SESSION_LOG_FILE = f"outlook_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
ui_log_lines = []
MAX_UI_LOG_LINES = 1000
# Sử dụng URL đăng ký trực tiếp để tránh chuyển hướng OAuth2 và mở form Email chuẩn
DEFAULT_OUTLOOK_SIGNUP_URL = "https://signup.live.com/signup?lic=1"
OUTLOOK_SIGNUP_URL = DEFAULT_OUTLOOK_SIGNUP_URL
OUTLOOK_NAVIGATION_TIMEOUT_MS = 20000
OUTLOOK_READY_TIMEOUT_MS = 15000
OUTLOOK_ACTION_TIMEOUT_MS = 10000
BROWSER_LAUNCH_TIMEOUT_MS = 15000
# ========================================================
# CẬP NHẬT SELECTOR ĐA TẦNG (BẮT TRỌN TRANG OAUTH2 LIVE)
# ========================================================
EMAIL_INPUT_SELECTOR = (
    "input[id='usernameInput'], "
    "input[id='MemberName'], "
    "input[name='loginfmt'], "
    "input[id='loginfmt'], "
    "input[type='email']"
)

# ========================================================
# CẬP NHẬT SELECTOR NÚT BẤM TIẾP THEO (NEXT BUTTON)
# ========================================================
NEXT_BUTTON_SELECTOR = (
    "button[id='nextButton'], "
    "input[id='idSIButton9'], "
    "button[type='submit'], "
    "input[type='submit']"
)

CONFIG = {
    "choose_browser": "chromium",
    "email_suffix": "@hotmail.com",
    "proxy": "",
    "use_parent_proxies": False,
    "bot_protection_wait": 5.0,
    "max_captcha_retries": 3,
    "concurrent_flows": 1,
    "max_tasks": 1000,
    "headless": False,
    "timeout_secs": 20,
    "launch_timeout_ms": 15000,
    "proxies": [],
    "oauth2": {
        "enable_oauth2": False,
        "client_id": "",
        "redirect_url": "",
        "Scopes": ["offline_access", "https://graph.microsoft.com/Mail.ReadWrite"]
    },
    "playwright": {
        "browser_path": ""
    }
}

CAPTCHA_INITIAL_WAIT_MS = 6000
CAPTCHA_HOLD_MIN_MS = 5200
CAPTCHA_HOLD_MAX_MS = 6800
CAPTCHA_POST_HOLD_WAIT_MS = 1200
CAPTCHA_RESULT_WAIT_MS = 14000

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def apply_runtime_timeouts():
    global OUTLOOK_NAVIGATION_TIMEOUT_MS, OUTLOOK_READY_TIMEOUT_MS, OUTLOOK_ACTION_TIMEOUT_MS, BROWSER_LAUNCH_TIMEOUT_MS
    timeout_ms = max(5000, int(CONFIG.get("timeout_secs", 20)) * 1000)
    OUTLOOK_NAVIGATION_TIMEOUT_MS = min(timeout_ms, 20000)
    OUTLOOK_READY_TIMEOUT_MS = min(timeout_ms, 15000)
    OUTLOOK_ACTION_TIMEOUT_MS = min(timeout_ms, 10000)
    BROWSER_LAUNCH_TIMEOUT_MS = min(
        max(5000, int(CONFIG.get("launch_timeout_ms", 15000))),
        15000,
    )

def normalize_chromium_browser_path(browser_path):
    browser_path = str(browser_path or "").strip()
    if not browser_path or not os.path.exists(browser_path):
        return ""

    executable_name = os.path.basename(browser_path).lower()
    if any(name in executable_name for name in ("chrome", "chromium", "msedge")):
        return os.path.abspath(browser_path)
    return ""

def normalize_signup_url(value):
    from urllib.parse import urlsplit

    url = str(value or "").strip()
    if not url:
        return DEFAULT_OUTLOOK_SIGNUP_URL

    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme in ("http", "https") and (
            host == "live.com"
            or host.endswith(".live.com")
            or host == "microsoft.com"
            or host.endswith(".microsoft.com")
        ):
            return url
    except Exception:
        pass
    return DEFAULT_OUTLOOK_SIGNUP_URL

def build_full_email(local_part, suffix):
    local_part = str(local_part or "").strip()
    suffix = str(suffix or "").strip() or "@hotmail.com"
    if "@" in local_part:
        return local_part
    if not suffix.startswith("@"):
        suffix = f"@{suffix}"
    return f"{local_part}{suffix}"

def find_firefox_path():
    try:
        import winreg
        reg_paths = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\firefox.exe"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\firefox.exe"),
        ]
        for hkey, subkey in reg_paths:
            try:
                with winreg.OpenKey(hkey, subkey) as key:
                    path, _ = winreg.QueryValueEx(key, "")
                    if path and os.path.exists(path) and "WindowsApps" not in path and "windowsapps" not in path:
                        return os.path.abspath(path)
            except Exception:
                pass
    except Exception:
        pass

    username = os.environ.get("USERNAME", "")
    default_locations = [
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
        os.path.join(r"C:\Users", username, r"AppData\Local\Mozilla Firefox\firefox.exe"),
        os.path.join(r"C:\Users", username, r"AppData\Local\Local\Mozilla Firefox\firefox.exe"),
    ]
    for loc in default_locations:
        if os.path.exists(loc) and "WindowsApps" not in loc and "windowsapps" not in loc:
            return os.path.abspath(loc)

    # Dò tìm Playwright Firefox trong ms-playwright
    try:
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        if not local_appdata:
            local_appdata = os.path.join(r"C:\Users", username, "AppData", "Local")
        ms_playwright = os.path.join(local_appdata, "ms-playwright")
        if os.path.exists(ms_playwright):
            # Lọc các thư mục firefox-*
            for folder in os.listdir(ms_playwright):
                if folder.startswith("firefox-"):
                    p = os.path.join(ms_playwright, folder, "firefox", "firefox.exe")
                    if os.path.exists(p):
                        return os.path.abspath(p)
    except Exception:
        pass
    return ""

def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                CONFIG.update(loaded)
        except Exception as e:
            print(f"Loi doc config.json: {e}")

    if "playwright" not in CONFIG or not isinstance(CONFIG.get("playwright"), dict):
        CONFIG["playwright"] = {"browser_path": ""}

    CONFIG["choose_browser"] = "chromium"
    CONFIG["playwright"]["browser_path"] = normalize_chromium_browser_path(
        CONFIG["playwright"].get("browser_path", "")
    )
    apply_runtime_timeouts()

def save_config():
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"Loi luu config.json: {e}")

load_config()

# Outlook flow is Chromium-only to avoid Firefox profile-lock popups.
CONFIG["choose_browser"] = "chromium"

OUTLOOK_SIGNUP_URL = normalize_signup_url(CONFIG.get("SIGNUP_URL", ""))

def log(msg, msg_type="INFO", worker_id=0):
    timestamp = datetime.now().strftime("%H:%M:%S")
    # Chỉ lọc giữ lại các log hệ thống quan trọng hoặc kết quả terminal cho UI
    if msg_type not in ["SUCCESS", "ERROR", "WARN", "STEP"]:
        # Bỏ qua log phụ trên UI nhưng vẫn ghi vào danh sách nếu cần thiết,
        # Tuy nhiên yêu cầu là ẩn chúng đi, do đó ta trả về luôn để log gọn gàng.
        # Nhưng đợi đã, nếu ta return luôn thì nó không in ra terminal.
        # Thiết kế yêu cầu: "Chỉ lọc giữ lại các log hệ thống quan trọng hoặc kết quả terminal"
        # "Bỏ qua hoàn toàn các log bước phụ"
        return

    prefix = {"SUCCESS": "[+]", "ERROR": "[-]", "WARN": "[!]", "STEP": "[>]"}
    prefix_str = prefix.get(msg_type, "[*]")
    tag = f"W{worker_id:02d}" if worker_id > 0 else "BOT"
    console_line = f"[{timestamp}] {prefix_str} [{tag}] {msg}"
    ui_line = f"[{timestamp}] > {prefix_str} [{tag}] {msg}"

    try:
        print(console_line)
    except UnicodeEncodeError:
        try:
            print(console_line.encode('ascii', errors='replace').decode('ascii'))
        except Exception:
            pass
    except Exception:
        pass
    with lock:
        ui_log_lines.append({"line": ui_line, "type": msg_type})
        if len(ui_log_lines) > MAX_UI_LOG_LINES:
            del ui_log_lines[:-MAX_UI_LOG_LINES]

def get_parent_proxy():
    # Ưu tiên đọc danh sách proxy của riêng Outlook trước
    local_proxies = CONFIG.get("proxies") or CONFIG.get("proxy_list") or []
    if local_proxies and isinstance(local_proxies, list):
        return local_proxies

    local_proxy = CONFIG.get("proxy", "")
    if isinstance(local_proxy, dict):
        if str(local_proxy.get("server", "")).strip():
            return [local_proxy]
    elif str(local_proxy).strip():
        local_proxy = str(local_proxy).strip()
        return [local_proxy]

    if not CONFIG.get("use_parent_proxies", False):
        return []

    # Doc proxy tu file config tong khi duoc bat ro trong OutlookRegister/config.json.
    config_parent_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json")
    if os.path.exists(config_parent_path):
        try:
            with open(config_parent_path, "r", encoding="utf-8") as f:
                parent_config = json.load(f)
            proxies_list = parent_config.get("PROXIES", [])
            if proxies_list:
                return proxies_list
        except Exception:
            pass
    return []

# ============ PARSE PROXY FOR PLAYWRIGHT ============
def parse_proxy_object(proxy_value):
    from urllib.parse import unquote, urlsplit, urlunsplit

    if not proxy_value:
        return None

    def with_scheme(server):
        server = str(server or "").strip()
        if not server:
            return ""
        return server if "://" in server else f"http://{server}"

    def split_auth_url(raw_url):
        parsed = urlsplit(with_scheme(raw_url))
        if not parsed.hostname:
            return None

        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = f"{host}:{parsed.port}" if parsed.port else host
        proxy_config = {"server": urlunsplit((parsed.scheme or "http", netloc, "", "", ""))}
        if parsed.username:
            proxy_config["username"] = unquote(parsed.username)
        if parsed.password:
            proxy_config["password"] = unquote(parsed.password)
        return proxy_config

    if isinstance(proxy_value, dict):
        server = str(proxy_value.get("server") or "").strip()
        if not server and proxy_value.get("host") and proxy_value.get("port"):
            server = f"{proxy_value.get('host')}:{proxy_value.get('port')}"
        if not server:
            return None
        proxy_config = split_auth_url(server) or {"server": with_scheme(server)}
        username = proxy_value.get("username") or proxy_value.get("user")
        password = proxy_value.get("password") or proxy_value.get("pass")
        if username is not None and str(username).strip():
            proxy_config["username"] = str(username).strip()
        if password is not None and str(password).strip():
            proxy_config["password"] = str(password).strip()
        return proxy_config

    raw = str(proxy_value).strip()
    if not raw:
        return None
    if "://" in raw:
        parsed_proxy = split_auth_url(raw)
        return parsed_proxy or {"server": raw}

    if "@" in raw:
        credentials, server = raw.rsplit("@", 1)
        proxy_config = split_auth_url(server) or {"server": with_scheme(server)}
        if ":" in credentials:
            username, password = credentials.split(":", 1)
            proxy_config["username"] = unquote(username.strip())
            proxy_config["password"] = unquote(password.strip())
        return proxy_config

    parts = raw.split(":")
    if len(parts) >= 4:
        if parts[1].strip().isdigit():
            host = parts[0].strip()
            port = parts[1].strip()
            username = parts[2].strip()
            password = ":".join(parts[3:]).strip()
        else:
            username = parts[0].strip()
            password = parts[1].strip()
            host = parts[2].strip()
            port = parts[3].strip()
        return {
            "server": f"http://{host}:{port}",
            "username": username,
            "password": password
        }
    elif len(parts) >= 2:
        host = parts[0].strip()
        port = parts[1].strip()
        return {"server": f"http://{host}:{port}"}
    return {"server": raw}

def build_browser_launch_args(proxy_value):
    launch_args = {}
    proxy_settings = parse_proxy_object(proxy_value)
    if proxy_settings:
        launch_args["proxy"] = proxy_settings
    return launch_args

def short_proxy(proxy_value, limit=24):
    if not proxy_value:
        return "DIRECT"
    parsed_proxy = parse_proxy_object(proxy_value)
    proxy_value = (parsed_proxy or {}).get("server") or str(proxy_value)
    return proxy_value if len(proxy_value) <= limit else proxy_value[:limit - 3] + "..."


def proxy_key(proxy_value):
    parsed_proxy = parse_proxy_object(proxy_value)
    if parsed_proxy:
        username = parsed_proxy.get("username", "")
        return f"{parsed_proxy.get('server', '').strip()}|{username}"
    return str(proxy_value).strip()

def stop_page_loading(page):
    try:
        page.evaluate("window.stop()")
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

def get_page_body_text(page, timeout=1500):
    try:
        return page.locator("body").inner_text(timeout=timeout)
    except Exception:
        return ""

def normalize_visible_text(value):
    text = str(value or "").lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.replace("đ", "d")

def is_proxy_auth_error_text(text):
    text = normalize_visible_text(text)
    return (
        "not authenticated" in text
        or "invalid authentication credentials" in text
        or "proxy username" in text
        or "proxy address" in text and "proxy username" in text
    )

def assert_not_proxy_auth_error(page, assigned_proxy):
    body_text = get_page_body_text(page)
    if not is_proxy_auth_error_text(body_text):
        return

    if assigned_proxy:
        BAD_PROXY_BLACKLIST.add(proxy_key(assigned_proxy))
    raise TimeoutError(
        f"Proxy bi tu choi xac thuc: {short_proxy(assigned_proxy)}. "
        "Kiem tra server/username/password/port cua proxy."
    )

def wait_for_outlook_ready(page):
    page.wait_for_selector(EMAIL_INPUT_SELECTOR, state="visible", timeout=OUTLOOK_READY_TIMEOUT_MS)

def load_outlook_signup_page(page, worker_id, assigned_proxy):
    page.set_default_navigation_timeout(OUTLOOK_NAVIGATION_TIMEOUT_MS)
    page.set_default_timeout(OUTLOOK_READY_TIMEOUT_MS)

    try:
        page.goto(
            OUTLOOK_SIGNUP_URL,
            timeout=OUTLOOK_NAVIGATION_TIMEOUT_MS,
            wait_until="commit"
        )
        assert_not_proxy_auth_error(page, assigned_proxy)
    except Exception as nav_err:
        if "Proxy bi tu choi xac thuc" in str(nav_err):
            raise
        stop_page_loading(page)
        proxy_hint = f" qua proxy {short_proxy(assigned_proxy)}" if assigned_proxy else " truc tiep"
        timeout_seconds = OUTLOOK_NAVIGATION_TIMEOUT_MS // 1000
        raise TimeoutError(f"Outlook khong phan hoi sau {timeout_seconds}s{proxy_hint}: {nav_err}") from nav_err

    try:
        wait_for_outlook_ready(page)
    except Exception as ready_err:
        assert_not_proxy_auth_error(page, assigned_proxy)
        stop_page_loading(page)
        title = ""
        try:
            title = page.title()
        except Exception:
            pass
        raise TimeoutError(
            "Outlook da mo nhung khong thay o email; co the mang/proxy cham, bi chan, hoac trang Microsoft doi giao dien. "
            f"URL={page.url} Title={title or '-'}"
        ) from ready_err

# ============ REGISTER ENGINE ============
class PlaywrightWorkerController:
    def __init__(self, assigned_proxy):
        self.proxy = assigned_proxy
        self.wait_time = CONFIG['bot_protection_wait'] * 1000
        self.max_captcha_retries = CONFIG['max_captcha_retries']
        self.enable_oauth2 = CONFIG["oauth2"]['enable_oauth2']
        self.email_suffix = CONFIG['email_suffix']
        self.browser_path = CONFIG["playwright"]["browser_path"]
        self.thread_local = threading.local()

    def launch_browser(self):
        try:
            import asyncio
            try:
                # Dập tắt loop đang tồn tại trên thread này để sync_playwright hoạt động an toàn
                asyncio.set_event_loop(None)
            except Exception:
                pass
            p = sync_playwright().start()
            launch_args = build_browser_launch_args(self.proxy)
            launch_options = {
                "headless": CONFIG.get("headless", False),
                "timeout": BROWSER_LAUNCH_TIMEOUT_MS,
                "args": [
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-gpu",
                    "--disable-webrtc",
                ],
            }
            # Chỉ gán executable_path nếu có đường dẫn hợp lệ
            browser_path_lower = (self.browser_path or "").lower()
            is_chromium_path = any(name in browser_path_lower for name in ("chrome", "chromium", "msedge"))
            if self.browser_path and os.path.exists(self.browser_path) and is_chromium_path:
                launch_options["executable_path"] = self.browser_path
            launch_options.update(launch_args)

            # Chọn engine trình duyệt theo config choose_browser
            CONFIG["choose_browser"] = "chromium"
            chosen = "chromium"

            if chosen in ("chromium", "chrome", "playwright") or "chrome" in browser_path_lower or "google" in browser_path_lower:
                # Chromium: hỗ trợ đầy đủ is_mobile, has_touch, device_scale_factor
                b = p.chromium.launch(**launch_options)
                self.browser_type = "chromium"
                log("Đã khởi chạy nhân Chromium (hỗ trợ Mobile Emulation).", "INFO")
            elif False:
                b = p.firefox.launch(**launch_options)
                self.browser_type = "firefox"
                log("Đã khởi chạy nhân Firefox (Desktop Responsive). Mobile flags sẽ bị bỏ qua.", "INFO")
            else:
                # Mặc định: dùng Chromium
                b = p.chromium.launch(**launch_options)
                self.browser_type = "chromium"
                log(f"choose_browser='{chosen}' không nhận diện, fallback sang Chromium.", "WARN")
            return p, b
        except Exception as e:
            raise RuntimeError(f"Loi khoi chay trinh duyet: {e}")

    def get_browser(self):
        if not hasattr(self.thread_local, "browser"):
            p, b = self.launch_browser()
            self.thread_local.playwright = p
            self.thread_local.browser = b
        return self.thread_local.browser

    def clean_up(self):
        if hasattr(self.thread_local, "browser"):
            try:
                self.thread_local.browser.close()
            except Exception: pass
        if hasattr(self.thread_local, "playwright"):
            try:
                self.thread_local.playwright.stop()
            except Exception: pass

    def select_birth_dropdown(self, page, dropdown_selector, option_names, fallback_index):
        dropdown = page.locator(dropdown_selector).first
        dropdown.wait_for(state="visible", timeout=8000)
        dropdown.click(force=True, timeout=3000)
        page.wait_for_timeout(120)

        normalized_names = [normalize_visible_text(name).strip() for name in option_names if str(name).strip()]
        options = page.locator('[role="option"]')
        try:
            option_count = min(options.count(), 80)
        except Exception:
            option_count = 0

        for idx in range(option_count):
            option = options.nth(idx)
            try:
                if not option.is_visible(timeout=200):
                    continue
                text = normalize_visible_text(option.inner_text(timeout=250)).strip()
                if text in normalized_names or any(name and name in text for name in normalized_names):
                    option.click(force=True, timeout=1000)
                    return True
            except Exception:
                continue

        try:
            dropdown.press("Home")
            for _ in range(max(0, int(fallback_index) - 1)):
                dropdown.press("ArrowDown")
            dropdown.press("Enter")
            return True
        except Exception:
            return False

    def fill_birth_details_fast(self, page, day, month, year):
        day_value = int(day)
        month_value = int(month)
        vietnamese_months = [
            "Tháng Một", "Tháng Hai", "Tháng Ba", "Tháng Tư", "Tháng Năm", "Tháng Sáu",
            "Tháng Bảy", "Tháng Tám", "Tháng Chín", "Tháng Mười", "Tháng Mười Một", "Tháng Mười Hai",
        ]
        english_months = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]

        self.select_birth_dropdown(
            page,
            '#BirthDayDropdown, [name="BirthDay"], [aria-label*="Day"], [aria-label*="Ngày"]',
            [str(day_value), f"Ngày {day_value}", f"Day {day_value}"],
            day_value,
        )
        self.select_birth_dropdown(
            page,
            '#BirthMonthDropdown, [name="BirthMonth"], [aria-label*="Month"], [aria-label*="Tháng"]',
            [
                vietnamese_months[month_value - 1],
                english_months[month_value - 1],
                str(month_value),
                f"Tháng {month_value}",
            ],
            month_value,
        )

        year_input = page.locator(
            'input[name="BirthYear"], input[id="BirthYear"], input[aria-label*="Year"], input[aria-label*="Năm"]'
        ).first
        year_input.wait_for(state="visible", timeout=8000)
        year_input.fill(str(year))

    def handle_captcha(self, page, worker_id, ws):
        def captcha_retry_text_visible():
            body_text = normalize_visible_text(get_page_body_text(page, timeout=1000))
            return (
                "try again" in body_text
                or "try another" in body_text
                or "vui long thu lai" in body_text
                or "thu lai" in body_text
            )

        def enforcement_frame_visible():
            selectors = (
                'iframe#enforcementFrame',
                'iframe[src*="enforcement"]',
                'iframe[src*="arkose"]',
                'iframe[src*="funcaptcha"]',
                'iframe[src*="captcha"]',
                'iframe[src*="crcldu"]',
            )
            for selector in selectors:
                try:
                    locator = page.locator(selector)
                    for idx in range(min(locator.count(), 3)):
                        if locator.nth(idx).is_visible(timeout=200):
                            return True
                except Exception:
                    continue
            return False

        def captcha_challenge_visible():
            body_text = normalize_visible_text(get_page_body_text(page, timeout=1000))
            return (
                "chứng minh" in body_text
                or "nhấn và giữ" in body_text
                or "nhan va giu" in body_text
                or "chung minh" in body_text
                or "prove" in body_text and "human" in body_text
                or "press and hold" in body_text
                or "verify" in body_text and "human" in body_text
                or enforcement_frame_visible()
            )

        def scope_is_captcha(scope):
            if scope is page:
                return captcha_challenge_visible()
            try:
                frame_url = str(scope.url or "").lower()
            except Exception:
                frame_url = ""
            return any(token in frame_url for token in (
                "captcha",
                "enforcement",
                "arkose",
                "funcaptcha",
                "hsprotect",
                "crcldu",
            ))

        def wait_for_captcha_result(timeout_ms):
            deadline = time.time() + timeout_ms / 1000
            while time.time() < deadline and not should_stop:
                if captcha_retry_text_visible():
                    return "retry"
                if not captcha_challenge_visible():
                    return "passed"
                page.wait_for_timeout(400)
            return "pending"

        def find_hold_button(timeout_ms):
            deadline = time.time() + timeout_ms / 1000
            no_challenge_deadline = time.time() + 1.6
            selectors = [
                'button:has-text("Hold")',
                '[role="button"]:has-text("Hold")',
                'button:has-text("Press")',
                '[role="button"]:has-text("Press")',
                'button',
                '[role="button"]',
            ]

            while time.time() < deadline and not should_stop:
                challenge_visible = captcha_challenge_visible()
                scopes = [page] + list(page.frames)
                fallback = None
                fallback_area = 0
                for scope in scopes:
                    allow_fallback = scope_is_captcha(scope)
                    for selector in selectors:
                        try:
                            locator = scope.locator(selector)
                            for idx in range(min(locator.count(), 8)):
                                candidate = locator.nth(idx)
                                if not candidate.is_visible(timeout=250):
                                    continue
                                box = candidate.bounding_box(timeout=500)
                                if not box or box["width"] < 80 or box["height"] < 28:
                                    continue
                                text = ""
                                try:
                                    text = normalize_visible_text(candidate.inner_text(timeout=250)).strip()
                                except Exception:
                                    pass
                                if "hold" in text or "press" in text or "nhan" in text or "giu" in text:
                                    return candidate
                                area = box["width"] * box["height"]
                                if allow_fallback and area > fallback_area:
                                    fallback = candidate
                                    fallback_area = area
                        except Exception:
                            continue
                if fallback is not None:
                    return fallback
                if not challenge_visible and time.time() >= no_challenge_deadline:
                    return None
                page.wait_for_timeout(300)
            return None

        def press_and_hold(locator, duration_ms):
            box = locator.bounding_box(timeout=1000)
            if not box:
                return False
            x = box["x"] + box["width"] / 2
            y = box["y"] + box["height"] / 2
            page.mouse.move(x, y, steps=random.randint(8, 14))
            page.wait_for_timeout(random.randint(150, 350))
            page.mouse.down()
            try:
                page.wait_for_timeout(duration_ms)
            finally:
                page.mouse.up()
            return True

        ws(6, "Tim nut captcha nhan-giu...")
        hold_button = find_hold_button(CAPTCHA_INITIAL_WAIT_MS)
        if hold_button is None:
            if captcha_challenge_visible():
                log("Captcha dang hien nhung khong tim thay nut nhan-giu.", "WARN", worker_id)
                return False
            return True

        max_attempts = max(1, min(int(self.max_captcha_retries or 1), 3))
        for attempt in range(1, max_attempts + 1):
            if should_stop:
                return False
            ws(6, f"Giu nut captcha... (Lan {attempt}/{max_attempts})")
            try:
                press_and_hold(hold_button, random.randint(CAPTCHA_HOLD_MIN_MS, CAPTCHA_HOLD_MAX_MS))
                page.wait_for_timeout(CAPTCHA_POST_HOLD_WAIT_MS)
            except Exception as exc:
                log(f"Loi thao tac captcha nhan-giu: {exc}", "WARN", worker_id)

            if page.get_by_text('异常活动').count() or page.get_by_text('维护').count() > 0:
                log("IP bi gioi han tan suat (Rate Limit).", "ERROR", worker_id)
                return False

            captcha_result = wait_for_captcha_result(CAPTCHA_RESULT_WAIT_MS)
            if captcha_result == "passed":
                return True
            if captcha_result == "pending":
                log("Captcha da hien dau tick nhung chua chuyen trang, thu lai nhanh.", "WARN", worker_id)

            hold_button = find_hold_button(2500)
            if hold_button is None:
                return False

        log("Captcha nhan-giu bi Microsoft yeu cau thu lai qua nhieu lan.", "WARN", worker_id)
        return False

    def register(self, page, email, password, worker_id, ws):
        # Tự động sinh tên ngẫu nhiên an toàn (không lo thiếu thư viện faker)
        try:
            from faker import Faker
            fake = Faker()
            lastname = fake.last_name()
            firstname = fake.first_name()
        except Exception:
            # Fallback nếu thiếu thư viện faker trên máy
            firstnames = ["John", "David", "James", "Robert", "Michael", "William", "Richard", "Thomas", "Charles", "Daniel", "Matthew", "Anthony", "Mark", "Donald", "Steven", "Paul", "Andrew", "Joshua", "Kenneth", "Kevin"]
            lastnames = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis", "Wilson", "Anderson", "Taylor", "Thomas", "Moore", "Martin", "Jackson", "Thompson", "White", "Harris", "Sanchez", "Clark", "Ramirez"]
            firstname = random.choice(firstnames)
            lastname = random.choice(lastnames)

        year = str(random.randint(1965, 2004))
        month = str(random.randint(1, 12))
        day = str(random.randint(1, 28))

        try:
            ws(1, "Vao trang chu dang ky...")
            max_attempts = 1 if self.proxy else 2
            for attempt in range(1, max_attempts + 1):
                try:
                    log(f"Dang tai trang chu dang ky (Lan thu {attempt}/{max_attempts})...", "INFO", worker_id)
                    load_outlook_signup_page(page, worker_id, self.proxy)
                    break
                except Exception as e:
                    if attempt == max_attempts:
                        raise e
                    log(f"Loi ket noi trang chu ({e}), dang thu lai sau 2s...", "WARN", worker_id)
                    page.wait_for_timeout(2000)

            # Click dong y dieu khoan
            for selector in ['button:has-text("同意并继续")', 'button:has-text("Accept")', 'button:has-text("Đồng ý")', '#acceptButton']:
                try:
                    btn = page.locator(selector).first
                    if btn.is_visible(timeout=3000):
                        btn.click()
                        break
                except: continue

            start_time = time.time()
            page.wait_for_timeout(1000)
        except Exception as e:
            log(f"Loi tai trang chu: {e}", "ERROR", worker_id)
            if self.proxy:
                log("Proxy dang dung co the cham/chet. De proxy trong OutlookRegister/config.json va use_parent_proxies=false de chay DIRECT.", "WARN", worker_id)
            return False

        try:
            # === BƯỚC 1: NHẬP EMAIL ===
            # LƯU Ý KHI CHẠY MOBILE: Nếu bot kẹt tại bước nào đó, hãy bật Devtools (F12)
            # hoặc xem trực tiếp GUI để kiểm tra ID/Class của các nút bấm có thay đổi trên
            # giao diện mobile so với desktop. Cập nhật các selector tương ứng nếu cần.
            ws(2, "Dien email muon tao...")
            try:
                page.wait_for_selector(EMAIL_INPUT_SELECTOR, state="visible", timeout=OUTLOOK_READY_TIMEOUT_MS)
                print("[+] Da tim thay o nhap lieu Email thanh cong.")
            except Exception as selector_err:
                print(f"[-] Khong tim thay o nhap Email do timeout hoac doi giao dien: {selector_err}")
                try:
                    page.screenshot(path="outlook_error_debug.png")
                except Exception as screenshot_err:
                    print(f"[-] Khong chup duoc screenshot debug: {screenshot_err}")
                raise
            email_input = page.locator(EMAIL_INPUT_SELECTOR).first
            full_email = build_full_email(email, self.email_suffix)

            # Mô phỏng người dùng nhập email chậm rãi
            page.wait_for_timeout(random.randint(250, 700))
            email_input.fill(full_email)
            page.wait_for_timeout(random.randint(400, 900)) # Trì hoãn trước khi click Next
            page.locator(NEXT_BUTTON_SELECTOR).click()
            page.wait_for_timeout(random.randint(800, 1500)) # Trì hoãn chờ trang load tiếp

            # === BƯỚC 2: NHẬP PASSWORD ===
            ws(3, "Nhap password...")
            page.wait_for_selector('input[type="password"]', timeout=15000)

            page.wait_for_timeout(random.randint(250, 700))
            page.locator('input[type="password"]').fill(password)
            page.wait_for_timeout(random.randint(400, 900)) # Trì hoãn trước khi click Next
            page.locator(NEXT_BUTTON_SELECTOR).click()
            page.wait_for_timeout(random.randint(800, 1500)) # Trì hoãn chờ trang load tiếp

            # === BƯỚC 3: NGÀY THÁNG NĂM SINH (ADD DETAILS) ===
            ws(4, "Dien ngay thang nam sinh...")
            self.fill_birth_details_fast(page, day, month, year)
            page.keyboard.press("Escape")
            month_selected = True
            month_name = str(month)

            # 1. Chọn Month qua combobox
            # Ánh xạ tên tháng tiếng Anh
            for lb in []:
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

            page.wait_for_timeout(1)

            # 2. Chọn Day qua combobox
            day_selected = True
            page.wait_for_timeout(1)
            for lb in []:
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

            page.wait_for_timeout(1)

            # 3. Nhập Year sau cùng
            page.locator('input[name="BirthYear"]').fill(year)
            page.wait_for_timeout(random.randint(500, 1000)) # Trì hoãn trước khi click Next

            # Click Next
            page.locator(NEXT_BUTTON_SELECTOR).click()
            page.wait_for_timeout(random.randint(900, 1600)) # Trì hoãn chờ trang load tiếp

            # === BƯỚC 4: NHẬP HỌ TÊN (ADD NAME) ===
            ws(5, "Dien ho ten...")
            page.wait_for_selector('input[name="firstNameInput"]', timeout=15000)

            page.wait_for_timeout(random.randint(500, 1200))
            page.locator('input[name="firstNameInput"]').fill(firstname)
            page.wait_for_timeout(random.randint(400, 1000))
            page.locator('input[name="lastNameInput"]').fill(lastname)

            # Chờ bảo vệ bot đủ thời gian nếu cần thiết
            if time.time() - start_time < self.wait_time / 1000:
                page.wait_for_timeout(self.wait_time - (time.time() - start_time) * 1000)

            page.wait_for_timeout(random.randint(500, 1000)) # Trì hoãn trước khi click Next
            page.locator(NEXT_BUTTON_SELECTOR).click()

            # Đợi load qua trang xác thực
            ws(6, "Cho he thong hoan thanh xac thuc...")
            page.locator('span > [href="https://go.microsoft.com/fwlink/?LinkID=521839"]').wait_for(state='detached', timeout=22000)
            page.wait_for_timeout(800)

            if page.get_by_text('异常活动').count() or page.get_by_text('维护').count() > 0:
                if self.proxy:
                    BAD_PROXY_BLACKLIST.add(proxy_key(self.proxy))
                log("IP hoac thiet bi bi gioi han tan suat (Rate Limit).", "ERROR", worker_id)
                return False

            if page.locator('iframe#enforcementFrame').count() > 0:
                # Ẩn log phụ
                pass

            # Xu ly Captcha
            captcha_ok = self.handle_captcha(page, worker_id, ws)
            if not captcha_ok:
                return False


# Màn hình check sau bước Captcha & Hoàn tất Duy trì đăng nhập (Stay signed in) để log vào hộp thư luôn
            is_success = False
            ws(6, "Đang hoàn tất đăng nhập (Stay signed in)...")
            for _ in range(40):
                page.wait_for_timeout(500)
                try:
                    # 1. Click "Yes" trên màn hình Duy trì đăng nhập (Stay signed in) nếu xuất hiện
                    kmsi_btn = page.locator('#idSIButton9, input[type="submit"], button[type="submit"]').first
                    if kmsi_btn.is_visible():
                        # Tích chọn "Don't show this again" (nếu có)
                        try:
                            chk = page.locator('input[type="checkbox"], #KmsiCheckboxField').first
                            if chk.is_visible():
                                chk.click(force=True)
                        except:
                            pass
                        kmsi_btn.click()
                        log("Đã click 'Yes' trên màn hình Duy trì đăng nhập.", "INFO", worker_id)
                        page.wait_for_timeout(2000)

                    # 2. Kiểm tra xem đã vào đến Inbox chưa
                    current_url = page.url.lower()
                    if "outlook.live.com/mail" in current_url or "/mail" in current_url:
                        is_success = True
                        log("Đã đăng nhập thẳng vào hộp thư Outlook thành công!", "SUCCESS", worker_id)
                        break

                    # 3. Hoặc kiểm tra sự xuất hiện của nút tạo thư mới đại diện cho hòm thư
                    for mail_btn in ['[aria-label="新邮件"]', '[aria-label="New mail"]', '[aria-label="Thư mới"]', 'button:has-text("New mail")', 'button:has-text("Thư mới")']:
                        if page.locator(mail_btn).count() > 0 and page.locator(mail_btn).first.is_visible():
                            is_success = True
                            log("Đã đăng nhập thẳng vào hộp thư Outlook thành công!", "SUCCESS", worker_id)
                            break
                    if is_success:
                        break

                    body_text = page.inner_text("body")
                    if "verification code" not in body_text.lower() and "verify email" not in body_text.lower() and "sign in" in current_url:
                        is_success = True
                except Exception:
                    pass

            return is_success
        except Exception:
            return False

# ============ SINGLE WORKER FLOW ============
def get_modern_firefox_config():
    """
    Cung cấp danh sách các User-Agent Firefox hiện đại nhất (Bản 130+).
    Bao gồm cả cấu hình nền tảng tương ứng để gán vào Browser Context.
    """
    firefox_variants = [
        {
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
            "platform": "Win32",
            "oscpu": "Windows NT 10.0; Win64; x64"
        },
        {
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
            "platform": "Win32",
            "oscpu": "Windows NT 10.0; Win64; x64"
        },
        {
            "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:135.0) Gecko/20100101 Firefox/135.0",
            "platform": "MacIntel",
            "oscpu": "Intel Mac OS X 10.15"
        },
        {
            "ua": "Mozilla/5.0 (X11; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0",
            "platform": "Linux x86_64",
            "oscpu": "Linux x86_64"
        }
    ]
    import random
    return random.choice(firefox_variants)

def get_browser_profile(browser_engine="chromium"):
    """Lấy cấu hình browser desktop/desktop responsive để giảm khả năng bị phát hiện."""
    import random
    chromium_profiles = [
        {
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "viewport": {"width": 1366, "height": 768},
            "device_scale_factor": 1,
            "is_mobile": False,
            "has_touch": False
        },
        {
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "viewport": {"width": 1440, "height": 900},
            "device_scale_factor": 1,
            "is_mobile": False,
            "has_touch": False
        },
        {
            "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
            "viewport": {"width": 1440, "height": 900},
            "device_scale_factor": 1,
            "is_mobile": False,
            "has_touch": False
        },
    ]
    try:
        random_ua = get_random_user_agent()
        if random_ua and "mozilla" in random_ua.lower() and "mobile" not in random_ua.lower():
            chromium_profiles.append({
                "ua": random_ua,
                "viewport": {"width": random.choice([1366, 1440, 1536, 1280]), "height": random.choice([768, 800, 900, 1024])},
                "device_scale_factor": 1,
                "is_mobile": False,
                "has_touch": False
            })
    except Exception:
        pass
    firefox_profiles = [
        {
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
            "viewport": {"width": 1366, "height": 768},
            "device_scale_factor": 1,
            "is_mobile": False,
            "has_touch": False
        },
        {
            "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
            "viewport": {"width": 1440, "height": 900},
            "device_scale_factor": 1,
            "is_mobile": False,
            "has_touch": False
        },
    ]
    return random.choice(firefox_profiles if browser_engine == "firefox" else chromium_profiles)

def register_one_outlook(worker_id, account_index, assigned_proxy):
    email = random_email()
    password = generate_strong_password()
    full_email = build_full_email(email, CONFIG.get('email_suffix', '@hotmail.com'))

    start_time = time.time()
    w = workers[worker_id - 1]

    with lock:
        w["email"] = full_email
        w["step"] = "Khoi tao trinh duyet..."
        w["step_num"] = 1
        w["status"] = "running"
        w["start_time"] = start_time
        w["elapsed"] = 0
        w["proxy"] = short_proxy(assigned_proxy)

    def ws(num, msg):
        with lock:
            w["step"] = msg
            w["step_num"] = num
            w["elapsed"] = int(time.time() - start_time)
        log(msg, "STEP", worker_id)

    success = False
    refresh_token = ""
    controller = PlaywrightWorkerController(assigned_proxy)
    try:
        ws(1, "Khoi tao Chromium...")
        browser = controller.get_browser()
        browser_engine = getattr(controller, "browser_type", "chromium")
        browser_profile = get_browser_profile(browser_engine)

        context_options = {
            "user_agent": browser_profile["ua"],
            "ignore_https_errors": True,
            "viewport": browser_profile["viewport"],
            "locale": "vi-VN",
            "timezone_id": "Asia/Ho_Chi_Minh",
            "extra_http_headers": {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "vi-VN,vi;q=0.8,en-US;q=0.5,en;q=0.3",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Connection": "keep-alive"
            }
        }

        if browser_engine == "firefox":
            # ========================================================
            # Firefox chỉ chạy Desktop Responsive, không dùng Mobile Emulation
            # ========================================================
            context_options["user_agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0"
            context_options["viewport"] = {"width": 1280, "height": 900}
            context_options["device_scale_factor"] = 1
            log(f"Khởi tạo Firefox Desktop Responsive với User-Agent chuẩn: {context_options['user_agent'][:60]}...", "INFO", worker_id)
        else:
            log(f"Khởi tạo Chromium Desktop với User-Agent: {browser_profile['ua'][:55]}...", "INFO", worker_id)
            context_options["device_scale_factor"] = browser_profile.get("device_scale_factor", 1)
            context_options["is_mobile"] = browser_profile.get("is_mobile", False)
            context_options["has_touch"] = browser_profile.get("has_touch", False)

        if browser_engine == "firefox":
            # Khi chạy Firefox, loại bỏ hoàn toàn các flag mobile không hỗ trợ
            context_options.pop("is_mobile", None)
            context_options.pop("has_touch", None)
            context_options.pop("isMobile", None)

        context = browser.new_context(**context_options)
        context.set_default_navigation_timeout(OUTLOOK_NAVIGATION_TIMEOUT_MS)
        context.set_default_timeout(OUTLOOK_ACTION_TIMEOUT_MS)
        ws(1, "Tao context va mo tab...")
        page = context.new_page()
        page.set_default_navigation_timeout(OUTLOOK_NAVIGATION_TIMEOUT_MS)
        page.set_default_timeout(OUTLOOK_ACTION_TIMEOUT_MS)

        # Tăng tốc độ load trang Outlook bằng cách chặn các tài nguyên nặng và tracking quảng cáo
        def block_useless_resources(route):
            req_type = route.request.resource_type
            url = route.request.url.lower()
            captcha_asset = any(token in url for token in (
                "captcha",
                "hsprotect",
                "hip",
                "enforcement",
                "solve_captcha",
                "fluent_web",
                "signup.live.com",
                "login.live.com",
                "logincdn",
                "microsoft",
                "msauth",
            ))
            if (
                (req_type in ("font", "media") and not captcha_asset)
                or "google-analytics" in url
                or "googletagmanager" in url
                or "mixpanel" in url
                or "sentry.io" in url
            ):
                route.abort()
            else:
                route.continue_()

        page.route("**/*", block_useless_resources)

        # Áp dụng một số patch anti-fingerprint cho Chromium desktop
        if controller.browser_type != "firefox":
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                window.chrome = { runtime: {} };
            """)

        # Cơ chế 1: Đánh chặn mạng để lấy Token từ Gói tin (Network Interception)
        intercepted_token = {"token": None}
        def handle_response(response):
            url = response.url
            if "token" in url.lower() or "oauth" in url.lower() or "login.live.com/ppsecure" in url:
                try:
                    if response.status == 200:
                        text = response.text()
                        if "refresh_token" in text:
                            data = response.json()
                            refresh_token_val = data.get("refresh_token")
                            if refresh_token_val:
                                intercepted_token["token"] = refresh_token_val
                except Exception:
                    pass

        log("Đang kích hoạt bộ chặn mạng để 'săn' Refresh Token...", "INFO", worker_id)
        page.on("response", handle_response)

        is_success = controller.register(page, email, password, worker_id, ws)

        # ---- XỬ LÝ ĐẦU RA THEO YÊU CẦU ----
        if is_success:
            success = True
            # ---- BƯỚC KHẮC PHỤC: CHECK LIVE RE-VERIFICATION ----
            log("Đăng ký thành công trên giao diện. Tiến hành kiểm tra tính toàn vẹn (Check Live)...", "INFO", worker_id)

            try:
                # Ép trình duyệt chuyển hướng thẳng sang trang login để thử nghiệm đăng nhập ngầm
                page.goto("https://login.live.com/login.srf", wait_until="domcontentloaded", timeout=10000)

                # Tìm ô nhập email và điền email vừa reg vào
                page.locator('input[type="email"]').fill(full_email)
                page.locator(NEXT_BUTTON_SELECTOR).click() # Bấm nút Tiếp theo

                # Đợi 3 giây xem Microsoft có báo lỗi "Không tìm thấy tài khoản" hay không
                time.sleep(3.0)

                body_text = page.inner_text("body")

                # Các từ khóa nhận diện tài khoản đã bị Microsoft nuốt/xóa ngay lập tức
                if "không tìm thấy tài khoản" in body_text.lower() or "doesn't exist" in body_text.lower():
                    log(f"TÀI KHOẢN BỊ ROLLBACK (MICROSOFT XÓA NGAY): {full_email} -> KHÔNG LƯU FILE", "ERROR", worker_id)
                    success = False
                else:
                    log(f"Tài khoản hợp lệ (Đã check live thành công): {full_email}", "SUCCESS", worker_id)
            except Exception as e:
                log(f"Cảnh báo lỗi trong quá trình Check Live: {e}, bỏ qua ghi nhận an toàn (giữ tài khoản).", "WARN", worker_id)
                # Giữ success = True làm fallback để tránh loại bỏ nhầm tài khoản đã tạo thành công

            if success:
                # Tiến hành lấy OAuth2 Token nếu bật hoặc nếu cấu hình sẵn sàng
                r_token = "NO_REFRESH_TOKEN"
                c_id = CONFIG.get("oauth2", {}).get("client_id") or "9e5f94bc-e8a4-4e73-b8be-63364c29d753"

                if CONFIG.get("oauth2", {}).get("enable_oauth2", False):
                    log("Đang bắt đầu lấy Access Token & Refresh Token qua OAuth2...", "INFO", worker_id)
                    try:
                        # Chạy luồng lấy token OAuth2 bằng hàm get_access_token của dự án
                        token_res = get_access_token(page, email)
                        if token_res[0]:
                            r_token = token_res[0]
                            log("Lấy thành công Refresh Token từ Microsoft OAuth2!", "SUCCESS", worker_id)
                        else:
                            log("Microsoft từ chối cấp Refresh Token (hoặc chưa nhấn chấp thuận đồng ý trên popup), sử dụng fallback.", "WARN", worker_id)
                    except Exception as o_err:
                        log(f"Gặp lỗi khi lấy Access/Refresh Token OAuth2: {o_err}", "WARN", worker_id)

                # Định dạng chuẩn: email|password|refresh_token|client_id
                account_line = f"{full_email}|{password}|{r_token}|{c_id}\n"

                # Ghi file lưu trữ tài khoản thành công
                success_filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "thanhcong.txt")
                with open(success_filepath, "a", encoding="utf-8") as f:
                    f.write(account_line)

                # Báo log xanh lá chữ SUCCESS
                log(f"ĐĂNG KÝ THÀNH CÔNG (HỢP LỆ VÀ ĐÃ LƯU): {full_email} | Mật khẩu: {password}", "SUCCESS", worker_id)
        else:
            # Báo log đỏ chữ ERROR và KHÔNG LƯU FILE
            log(f"ĐĂNG KÝ THẤT BẠI: {full_email} (Kẹt Captcha hoặc Microsoft chặn kết nối)", "ERROR", worker_id)
            success = False

        context.close()
    except Exception as e:
        log(f"Loi trinh duyet nghiem trong: {e}", "ERROR", worker_id)
        success = False
    finally:
        controller.clean_up()

    status_str = "SUCCESS" if success else "FAIL"

    with lock:
        w["status"] = "done" if success else "fail"
        w["step"] = status_str
        w["elapsed"] = int(time.time() - start_time)
        if success:
            w["ok"] += 1
            stats["success"] += 1
        else:
            w["fail"] += 1
            stats["fail"] += 1
        stats["total"] += 1

    return success

def run_registration_isolated(worker_id, account_index, assigned_proxy):
    """Run the full sync Playwright registration flow on a clean thread.

    The Outlook flow still uses Playwright's sync API. Running the complete
    sync lifecycle in its own thread avoids the "Sync API inside asyncio loop"
    failure when the caller is hosted by an async-aware runtime.
    """
    result_queue = queue.Queue(maxsize=1)

    def target():
        try:
            try:
                import asyncio
                asyncio.set_event_loop(None)
            except Exception:
                pass
            result_queue.put(register_one_outlook(worker_id, account_index, assigned_proxy))
        except Exception as exc:
            result_queue.put(exc)

    worker = threading.Thread(
        target=target,
        name=f"outlook-sync-playwright-w{worker_id:02d}",
        daemon=True,
    )
    worker.start()
    worker.join()

    result = result_queue.get() if not result_queue.empty() else False
    if isinstance(result, Exception):
        log(f"Loi thread Playwright co lap: {result}", "ERROR", worker_id)
        return False
    return bool(result)

def is_proxy_alive(proxy_str, timeout=2.5):
    import urllib.request
    from urllib.parse import quote, urlsplit, urlunsplit

    proxy_config = parse_proxy_object(proxy_str)
    if not proxy_config:
        return False

    proxy_url = proxy_config["server"]
    proxy_url = f"http://{proxy_url}" if "://" not in proxy_url else proxy_url

    username = proxy_config.get("username")
    if username:
        parsed = urlsplit(proxy_url)
        if parsed.netloc and "@" not in parsed.netloc:
            password = proxy_config.get("password", "")
            credentials = f"{quote(str(username), safe='')}:{quote(str(password), safe='')}"
            proxy_url = urlunsplit((parsed.scheme, f"{credentials}@{parsed.netloc}", parsed.path, parsed.query, parsed.fragment))

    try:
        proxy_handler = urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url})
        opener = urllib.request.build_opener(proxy_handler)
        req = urllib.request.Request("http://checkip.amazonaws.com", headers={"User-Agent": "Mozilla/5.0"})
        with opener.open(req, timeout=timeout) as response:
            if response.status == 200:
                return True
    except Exception:
        pass
    return False

def remember_live_proxy(proxy_value):
    key = proxy_key(proxy_value)
    if not key:
        return
    with proxy_cache_lock:
        LIVE_PROXY_CACHE[key] = (proxy_value, time.monotonic() + LIVE_PROXY_CACHE_TTL_SECONDS)

def forget_live_proxy(proxy_value):
    key = proxy_key(proxy_value)
    if not key:
        return
    with proxy_cache_lock:
        LIVE_PROXY_CACHE.pop(key, None)

def get_cached_live_proxy():
    now = time.monotonic()
    with proxy_cache_lock:
        expired = [key for key, (_proxy, expires_at) in LIVE_PROXY_CACHE.items() if expires_at <= now]
        for key in expired:
            LIVE_PROXY_CACHE.pop(key, None)

        live_candidates = [
            proxy
            for key, (proxy, _expires_at) in LIVE_PROXY_CACHE.items()
            if key not in BAD_PROXY_BLACKLIST
        ]
    return random.choice(live_candidates) if live_candidates else ""

def select_fast_proxy(proxies, worker_id):
    if not proxies:
        return ""

    cached_proxy = get_cached_live_proxy()
    if cached_proxy:
        log(f"Dung proxy song trong cache: {short_proxy(cached_proxy)}", "INFO", worker_id)
        return cached_proxy

    candidates = [proxy for proxy in proxies if proxy_key(proxy) not in BAD_PROXY_BLACKLIST]
    if not candidates:
        candidates = list(proxies)
    random.shuffle(candidates)
    candidates = candidates[:min(PROXY_PROBE_BATCH_SIZE, len(candidates))]

    if not candidates:
        return ""

    probe_workers = min(PROXY_PROBE_WORKERS, len(candidates))
    log(f"Dang do nhanh {len(candidates)} proxy song song...", "INFO", worker_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=probe_workers) as executor:
        future_to_proxy = {
            executor.submit(is_proxy_alive, candidate, PROXY_PROBE_TIMEOUT_SECONDS): candidate
            for candidate in candidates
        }
        try:
            for future in concurrent.futures.as_completed(
                future_to_proxy,
                timeout=PROXY_PROBE_TIMEOUT_SECONDS + 0.8,
            ):
                candidate = future_to_proxy[future]
                try:
                    if future.result():
                        remember_live_proxy(candidate)
                        log(f"Da tim thay proxy hoat dong tot: {short_proxy(candidate)}", "SUCCESS", worker_id)
                        return candidate
                except Exception:
                    continue
        except concurrent.futures.TimeoutError:
            pass

    fallback = random.choice(candidates)
    log(f"Khong co proxy nao phan hoi nhanh, dung proxy ngau nhien: {short_proxy(fallback)}", "WARN", worker_id)
    return fallback

# ============ WORKER POOL LOOP ============
def worker_loop(worker_id, q, total_accounts):
    log(f"Worker {worker_id:02d} khoi dong.", "INFO", worker_id)
    proxies = get_parent_proxy()

    while not should_stop:
        if is_paused:
            time.sleep(0.5)
            continue
        if q.empty() and total_accounts > 0:
            break
        try:
            task_data = q.get(timeout=1.0)
        except queue.Empty:
            if total_accounts > 0 and q.empty():
                break
            continue
        account_idx = task_data["index"]

        # Phan bo proxy
        assigned_proxy = ""
        if proxies:
            assigned_proxy = select_fast_proxy(proxies, worker_id)

        log(f"Bat dau dang ky account thu #{account_idx}...", "INFO", worker_id)
        success = run_registration_isolated(worker_id, account_idx, assigned_proxy)
        if assigned_proxy and not success:
            forget_live_proxy(assigned_proxy)
        q.task_done()

        if CONFIG["bot_protection_wait"] > 0 and not should_stop:
            time.sleep(2.0)

    with lock:
        w = workers[worker_id - 1]
        w["status"] = "idle"
        w["step"] = "Hoan thanh / Idle"
    log(f"Worker {worker_id:02d} dung hoat dong.", "INFO", worker_id)

def run_pool(concurrency, total_accounts):
    global bot_state
    q = queue.Queue()

    log(f"Khoi tao luong dang ky: Concurrency={concurrency} | Total={total_accounts}", "INFO")
    for idx in range(1, total_accounts + 1):
        q.put({"index": idx})

    worker_threads = []
    for w_id in range(1, concurrency + 1):
        t = threading.Thread(target=worker_loop, args=(w_id, q, total_accounts), daemon=True)
        worker_threads.append(t)
        t.start()

    # Chờ tất cả các luồng hoàn thành
    for t in worker_threads:
        t.join()

    bot_state = "OFFLINE"
    log(f"HOAN THANH: Tong {stats['total']} | Success {stats['success']} | Fail {stats['fail']}", "SUCCESS")

def start_bot_thread(concurrency, total_accounts):
    global bot_state, should_stop, is_paused
    should_stop = False
    is_paused = False
    bot_state = "RUNNING"

    with lock:
        workers.clear()
        proxies = get_parent_proxy()
        for w_id in range(1, concurrency + 1):
            assigned_proxy = proxies[(w_id - 1) % len(proxies)] if proxies else ""
            workers.append({
                "id": w_id,
                "email": "-",
                "step": "Cho lenh...",
                "step_num": 0,
                "status": "idle",
                "ok": 0,
                "fail": 0,
                "start_time": time.time(),
                "elapsed": 0,
                "proxy": short_proxy(assigned_proxy),
            })

    def run():
        run_pool(concurrency, total_accounts)

    t = threading.Thread(target=run, daemon=True)
    t.start()





def start_gui():
    import customtkinter as ctk
    import tkinter as tk
    from tkinter import filedialog
    import os, json, time, threading
    from datetime import datetime

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    # Premium Flat Dark UI Palette - Ocean Outlook Blue Theme
    BG = "#0b132b"
    PANEL = "#1c2541"
    PANEL_ALT = "#3a506b"
    PRIMARY = "#00b4d8"
    PRIMARY_HOVER = "#0077b6"
    TEXT = "#f8f9fa"
    MUTED = "#6c757d"
    SUCCESS = "#2a9d8f"
    ERROR = "#e63946"
    ERROR_HOVER = "#d62828"
    WARNING = "#f4a261"
    WARNING_HOVER = "#e76f51"
    BORDER = "#5c677d"
    MONO = "Segoe UI"

    def make_frame(parent, fg=PANEL, border=BORDER, radius=8, **kwargs):
        return ctk.CTkFrame(
            parent,
            fg_color=fg,
            border_color=border,
            border_width=1,
            corner_radius=radius,
            **kwargs,
        )

    def label(parent, text, size=11, color=TEXT, weight="normal", **kwargs):
        return ctk.CTkLabel(
            parent,
            text=text,
            text_color=color,
            font=(MONO, size, weight),
            **kwargs,
        )

    def entry(parent, width=80, placeholder=""):
        return ctk.CTkEntry(
            parent,
            width=width,
            height=28,
            fg_color=PANEL_ALT,
            border_color=BORDER,
            border_width=1,
            text_color=TEXT,
            placeholder_text=placeholder,
            placeholder_text_color=MUTED,
            font=(MONO, 11),
            justify="left",
            corner_radius=6,
        )

    def checkbox(parent, text, variable):
        return ctk.CTkCheckBox(
            parent,
            text=text,
            variable=variable,
            font=(MONO, 11, "bold"),
            text_color=TEXT,
            fg_color=PRIMARY,
            hover_color=PRIMARY_HOVER,
            border_color=BORDER,
            border_width=1,
            checkmark_color=BG,
            checkbox_width=16,
            checkbox_height=16,
            corner_radius=4,
        )

    def trim(value, limit):
        value = str(value or "-")
        return value if len(value) <= limit else value[:max(0, limit - 3)] + "..."

    def parse_int(value, default, minimum=0):
        try:
            parsed = int(str(value).strip() or str(default))
        except ValueError:
            parsed = default
        return max(minimum, parsed)

    def parse_float(value, default, minimum=0):
        try:
            parsed = float(str(value).strip() or str(default))
        except ValueError:
            parsed = default
        return max(minimum, parsed)

    app = ctk.CTk()
    app.title("OUTLOOK CREATOR BOT")
    app.geometry("850x780")
    app.minsize(700, 650)
    app.configure(fg_color=BG)
    closing_state = {"closing": False}

    def on_close():
        global should_stop, bot_state
        if closing_state["closing"]:
            return
        closing_state["closing"] = True
        should_stop = True
        bot_state = "OFFLINE"
        try:
            app.destroy()
        except Exception:
            pass

    app.protocol("WM_DELETE_WINDOW", on_close)

    main_scroll = ctk.CTkScrollableFrame(app, fg_color=BG, border_width=0, corner_radius=0)
    main_scroll.pack(fill="both", expand=True, padx=5, pady=5)

    main = ctk.CTkFrame(main_scroll, fg_color=BG, border_width=0, corner_radius=0)
    main.pack(fill="both", expand=True)
    main.grid_columnconfigure(0, weight=1)

    # ---- Header Bar ----
    header = make_frame(main, fg=PANEL, border=BORDER, radius=8)
    header.grid(row=0, column=0, sticky="ew", padx=0, pady=(0, 6))
    header.grid_columnconfigure(1, weight=1)

    title_group = ctk.CTkFrame(header, fg_color=PANEL, corner_radius=8)
    title_group.grid(row=0, column=0, sticky="w", padx=12, pady=8)
    lbl_main_title = label(title_group, " OUTLOOK CREATOR BOT ", 15, PRIMARY, "bold")
    lbl_main_title.pack(side="left")
    label(title_group, "v2.0 // modern ui // async pool", 10, MUTED, "bold").pack(side="left", padx=(12, 0))

    lbl_system_status = label(header, "[ OFFLINE ]", 13, ERROR, "bold")
    lbl_system_status.grid(row=0, column=2, sticky="e", padx=14, pady=8)

    # ---- Big Stats Display ----
    stats_frame = make_frame(main, fg=PANEL, border=BORDER, radius=8)
    stats_frame.grid(row=1, column=0, sticky="ew", padx=0, pady=(0, 6))
    stats_frame.grid_columnconfigure((0, 1, 2), weight=1)

    stat_meta = [
        ("total", "TỔNG CỘNG", PRIMARY),
        ("success", "THÀNH CÔNG", SUCCESS),
        ("fail", "THẤT BẠI", ERROR),
    ]
    stat_labels = {}
    for col, (key, caption, color) in enumerate(stat_meta):
        block = ctk.CTkFrame(stats_frame, fg_color=PANEL, corner_radius=0)
        block.grid(row=0, column=col, sticky="ew", padx=1)
        block.grid_columnconfigure(0, weight=1)
        value_label = label(block, "000", 24, color, "bold")
        value_label.grid(row=0, column=0, pady=(10, 0))
        label(block, caption, 11, MUTED, "bold").grid(row=1, column=0, pady=(0, 10))
        stat_labels[key] = value_label

    # ---- Configuration Form ----
    config_frame = make_frame(main, fg=BG, border=BORDER, radius=8)
    config_frame.grid(row=2, column=0, sticky="ew", padx=0, pady=(0, 6))
    config_frame.grid_columnconfigure(0, weight=1)

    # Row 1: Concurrency, Total tasks, Email suffix, Delay
    row1 = ctk.CTkFrame(config_frame, fg_color=BG, corner_radius=0)
    row1.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
    row1.grid_columnconfigure((1, 3, 5, 7), weight=1)

    label(row1, "Số luồng:", 11, TEXT, "bold").grid(row=0, column=0, sticky="w")
    ent_concurrency = entry(row1, 50)
    ent_concurrency.grid(row=0, column=1, padx=(4, 10), sticky="ew")
    ent_concurrency.insert(0, str(CONFIG.get("concurrent_flows", 1)))

    label(row1, "Tổng tài khoản:", 11, TEXT, "bold").grid(row=0, column=2, sticky="w")
    ent_total_tasks = entry(row1, 50)
    ent_total_tasks.grid(row=0, column=3, padx=(4, 10), sticky="ew")
    ent_total_tasks.insert(0, str(CONFIG.get("max_tasks", 1000)))

    label(row1, "Hậu tố:", 11, TEXT, "bold").grid(row=0, column=4, sticky="w")
    opt_suffix = ctk.CTkOptionMenu(
        row1, height=28, fg_color=PANEL_ALT, button_color=PRIMARY,
        button_hover_color=PRIMARY_HOVER, text_color=TEXT,
        font=(MONO, 11), corner_radius=6, values=["@outlook.com", "@hotmail.com"]
    )
    opt_suffix.grid(row=0, column=5, padx=(4, 10), sticky="ew")
    opt_suffix.set(CONFIG.get("email_suffix", "@hotmail.com"))

    label(row1, "Độ trễ bot (s):", 11, TEXT, "bold").grid(row=0, column=6, sticky="w")
    ent_delay = entry(row1, 50)
    ent_delay.grid(row=0, column=7, padx=(4, 0), sticky="ew")
    ent_delay.insert(0, str(CONFIG.get("bot_protection_wait", 5.0)))

    # Row 2: Checkboxes, Browser Mode, Capcha Retries, Single Proxy
    row2 = ctk.CTkFrame(config_frame, fg_color=BG, corner_radius=0)
    row2.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
    row2.grid_columnconfigure((3, 5, 7), weight=1)

    chk_headless_var = ctk.BooleanVar(value=CONFIG.get("headless", False))
    checkbox(row2, "Ẩn trình duyệt", chk_headless_var).grid(row=0, column=0, sticky="w", padx=(0, 15))

    chk_parent_proxies_var = ctk.BooleanVar(value=CONFIG.get("use_parent_proxies", False))
    checkbox(row2, "Dùng proxy cha", chk_parent_proxies_var).grid(row=0, column=1, sticky="w", padx=(0, 15))

    label(row2, "Trình duyệt:", 11, TEXT, "bold").grid(row=0, column=2, sticky="w")
    opt_browser = ctk.CTkOptionMenu(
        row2, height=28, fg_color=PANEL_ALT, button_color=PRIMARY,
        button_hover_color=PRIMARY_HOVER, text_color=TEXT,
        font=(MONO, 11), corner_radius=6, values=["chromium"]
    )
    opt_browser.grid(row=0, column=3, padx=(4, 15), sticky="ew")
    opt_browser.set("chromium")

    label(row2, "Thử captcha:", 11, TEXT, "bold").grid(row=0, column=4, sticky="w")
    ent_captcha_retries = entry(row2, 50)
    ent_captcha_retries.grid(row=0, column=5, padx=(4, 15), sticky="ew")
    ent_captcha_retries.insert(0, str(CONFIG.get("max_captcha_retries", 3)))

    label(row2, "Proxy đơn lẻ:", 11, TEXT, "bold").grid(row=0, column=6, sticky="w")
    ent_single_proxy = entry(row2, placeholder="ip:port hoặc user:pass@ip:port")
    ent_single_proxy.grid(row=0, column=7, padx=(4, 0), sticky="ew")
    ent_single_proxy.insert(0, str(CONFIG.get("proxy", "")))

    # Row 3: optional Chromium executable path
    row3 = ctk.CTkFrame(config_frame, fg_color=BG, corner_radius=0)
    row3.grid(row=2, column=0, sticky="ew", padx=10, pady=(4, 8))
    row3.grid_columnconfigure(1, weight=1)

    label(row3, "Chromium path:", 11, TEXT, "bold").grid(row=0, column=0, sticky="w")
    ent_firefox = entry(row3, placeholder="Để trống để dùng Chromium mặc định của Playwright")
    ent_firefox.grid(row=0, column=1, sticky="ew", padx=(8, 8))
    ent_firefox.insert(0, CONFIG.get("playwright", {}).get("browser_path", ""))

    def choose_firefox_path():
        path = filedialog.askopenfilename(
            title="Chọn Chrome/Chromium executable",
            filetypes=[("Browser executable", "*.exe"), ("All files", "*.*")]
        )
        if path:
            ent_firefox.delete(0, tk.END)
            ent_firefox.insert(0, path)

    ctk.CTkButton(
        row3, text="...", width=36, height=28, fg_color=PANEL_ALT, border_color=BORDER,
        border_width=1, hover_color=PRIMARY, text_color=TEXT, font=(MONO, 10, "bold"),
        corner_radius=6, command=choose_firefox_path
    ).grid(row=0, column=2, sticky="e")

    label(row3, "Timeout (s):", 11, TEXT, "bold").grid(row=0, column=3, sticky="w", padx=(15, 4))
    ent_timeout = entry(row3, 40)
    ent_timeout.grid(row=0, column=4, sticky="w")
    ent_timeout.insert(0, str(CONFIG.get("timeout_secs", 20)))

    # ---- Action Buttons ----
    actions = ctk.CTkFrame(main, fg_color=BG, corner_radius=0)
    actions.grid(row=3, column=0, sticky="ew", padx=0, pady=(0, 6))
    actions.grid_columnconfigure((0, 1, 2, 3), weight=1)

    def refresh_config_from_gui():
        CONFIG["concurrent_flows"] = parse_int(ent_concurrency.get(), CONFIG.get("concurrent_flows", 1), 1)
        CONFIG["max_tasks"] = parse_int(ent_total_tasks.get(), CONFIG.get("max_tasks", 1000), 1)
        CONFIG["email_suffix"] = opt_suffix.get()
        CONFIG["bot_protection_wait"] = parse_float(ent_delay.get(), CONFIG.get("bot_protection_wait", 5.0), 0)
        CONFIG["headless"] = bool(chk_headless_var.get())
        CONFIG["use_parent_proxies"] = bool(chk_parent_proxies_var.get())
        CONFIG["choose_browser"] = "chromium"
        CONFIG["max_captcha_retries"] = parse_int(ent_captcha_retries.get(), CONFIG.get("max_captcha_retries", 3), 0)
        CONFIG["proxy"] = ent_single_proxy.get().strip()
        CONFIG["timeout_secs"] = parse_int(ent_timeout.get(), CONFIG.get("timeout_secs", 20), 5)
        apply_runtime_timeouts()

        if "playwright" not in CONFIG:
            CONFIG["playwright"] = {}
        CONFIG["playwright"]["browser_path"] = normalize_chromium_browser_path(ent_firefox.get().strip())
        save_config()

    def action_run():
        if bot_state == "RUNNING":
            log("Hệ thống đang chạy, bỏ qua lệnh RUN mới.", "WARN")
            return
        refresh_config_from_gui()
        concurrency = CONFIG["concurrent_flows"]
        total_accounts = CONFIG["max_tasks"]
        log(f"RUN concurrency={concurrency} total={total_accounts} suffix={CONFIG['email_suffix']} delay={CONFIG['bot_protection_wait']}s", "INFO")
        start_bot_thread(concurrency, total_accounts)

    def action_pause():
        global is_paused, bot_state
        if bot_state not in ("RUNNING", "PAUSED"):
            log("Không có tiến trình đang chạy để PAUSE.", "WARN")
            return
        is_paused = not is_paused
        bot_state = "PAUSED" if is_paused else "RUNNING"
        log("Đã tạm dừng luồng chạy." if is_paused else "Tiếp tục luồng chạy.", "WARN")

    def action_stop():
        global should_stop, bot_state
        should_stop = True
        bot_state = "STOPPING"
        log("STOP khẩn cấp đã được gửi tới toàn bộ luồng.", "ERROR")

    def action_accounts():
        success_filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "thanhcong.txt")
        if not os.path.exists(success_filepath):
            try:
                with open(success_filepath, "a", encoding="utf-8"): pass
            except Exception: pass
        try:
            os.startfile(success_filepath)
        except Exception as exc:
            log(f"Không mở được file accounts: {exc}", "ERROR")

    btn_style = {"height": 36, "corner_radius": 6, "font": (MONO, 12, "bold")}
    ctk.CTkButton(actions, text="▶ RUN", fg_color=SUCCESS, hover_color="#1f7a60", text_color="#ffffff", command=action_run, **btn_style).grid(row=0, column=0, sticky="ew", padx=(0, 4))
    ctk.CTkButton(actions, text="▐▐ PAUSE", fg_color=PANEL_ALT, hover_color=WARNING_HOVER, border_color=WARNING, border_width=1, text_color=WARNING, command=action_pause, **btn_style).grid(row=0, column=1, sticky="ew", padx=4)
    ctk.CTkButton(actions, text="■ STOP", fg_color=PANEL_ALT, hover_color=ERROR_HOVER, border_color=ERROR, border_width=1, text_color=ERROR, command=action_stop, **btn_style).grid(row=0, column=2, sticky="ew", padx=4)
    ctk.CTkButton(actions, text="📂 ACCOUNTS", fg_color=PANEL_ALT, hover_color=PRIMARY_HOVER, border_color=PRIMARY, border_width=1, text_color=PRIMARY, command=action_accounts, **btn_style).grid(row=0, column=3, sticky="ew", padx=(4, 0))

    # ---- Live Workers Dashboard ----
    live_panel = make_frame(main, fg=BG, border=BORDER, radius=8)
    live_panel.grid(row=4, column=0, sticky="nsew", padx=0, pady=(0, 6))
    live_panel.grid_columnconfigure(0, weight=1)

    label(live_panel, "TRẠNG THÁI HOẠT ĐỘNG WORKERS", 11, TEXT, "bold").grid(row=0, column=0, sticky="w", padx=10, pady=(8, 4))

    # Table Header
    th = ctk.CTkFrame(live_panel, fg_color=PANEL, corner_radius=6)
    th.grid(row=1, column=0, sticky="ew", padx=10, pady=(4, 0))
    th.grid_columnconfigure(0, minsize=50, weight=0)
    th.grid_columnconfigure(1, minsize=100, weight=1)
    th.grid_columnconfigure(2, minsize=180, weight=2)
    th.grid_columnconfigure(3, minsize=180, weight=2)
    th.grid_columnconfigure(4, minsize=70, weight=0)
    th.grid_columnconfigure(5, minsize=60, weight=0)

    label(th, "ID", 10, MUTED, "bold").grid(row=0, column=0, padx=5, pady=6, sticky="w")
    label(th, "PROXY", 10, MUTED, "bold").grid(row=0, column=1, padx=5, pady=6, sticky="w")
    label(th, "EMAIL", 10, MUTED, "bold").grid(row=0, column=2, padx=5, pady=6, sticky="w")
    label(th, "STEP", 10, MUTED, "bold").grid(row=0, column=3, padx=5, pady=6, sticky="w")
    label(th, "STATUS", 10, MUTED, "bold").grid(row=0, column=4, padx=5, pady=6, sticky="e")
    label(th, "TIME", 10, MUTED, "bold").grid(row=0, column=5, padx=5, pady=6, sticky="e")

    live_scroll = ctk.CTkScrollableFrame(live_panel, fg_color=BG, border_width=0, corner_radius=8, scrollbar_button_color=BORDER, scrollbar_button_hover_color=PRIMARY)
    live_scroll.grid(row=2, column=0, sticky="nsew", padx=10, pady=(4, 10))

    live_rows = []

    def ensure_live_rows():
        if len(live_rows) != len(workers):
            for row_widgets in live_rows:
                if "card" in row_widgets:
                    row_widgets["card"].destroy()
            live_rows.clear()

        while len(live_rows) < len(workers):
            idx = len(live_rows)
            card = ctk.CTkFrame(live_scroll, fg_color=PANEL_ALT, corner_radius=6, border_width=1, border_color=BORDER)
            card.pack(fill="x", pady=2, padx=0)

            card.grid_columnconfigure(0, minsize=50, weight=0)
            card.grid_columnconfigure(1, minsize=100, weight=1)
            card.grid_columnconfigure(2, minsize=180, weight=2)
            card.grid_columnconfigure(3, minsize=180, weight=2)
            card.grid_columnconfigure(4, minsize=70, weight=0)
            card.grid_columnconfigure(5, minsize=60, weight=0)

            widgets = {}
            widgets["worker"] = label(card, f"W{idx+1:02d}", 11, PRIMARY, "bold")
            widgets["worker"].grid(row=0, column=0, padx=5, pady=8, sticky="w")

            widgets["proxy"] = label(card, "DIRECT", 10, MUTED)
            widgets["proxy"].grid(row=0, column=1, padx=5, pady=8, sticky="w")

            widgets["email"] = label(card, "-", 10, TEXT)
            widgets["email"].grid(row=0, column=2, padx=5, pady=8, sticky="w")

            widgets["step"] = label(card, "[0/6] Chờ lệnh...", 10, TEXT)
            widgets["step"].grid(row=0, column=3, padx=5, pady=8, sticky="w")

            widgets["status"] = label(card, "IDLE", 10, MUTED, "bold")
            widgets["status"].grid(row=0, column=4, padx=5, pady=8, sticky="e")

            widgets["time"] = label(card, "0s", 10, MUTED)
            widgets["time"].grid(row=0, column=5, padx=5, pady=8, sticky="e")

            widgets["card"] = card
            live_rows.append(widgets)

    # ---- Terminal Debug Log ----
    log_panel = make_frame(main, fg=BG, border=BORDER, radius=8)
    log_panel.grid(row=5, column=0, sticky="ew", padx=0, pady=0)
    log_panel.grid_columnconfigure(0, weight=1)

    log_title = ctk.CTkFrame(log_panel, fg_color=BG, corner_radius=0)
    log_title.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
    log_title.grid_columnconfigure(0, weight=1)
    label(log_title, "NHẬT KÝ CHẠY HỆ THỐNG", 11, TEXT, "bold").grid(row=0, column=0, sticky="w")

    txt_log = ctk.CTkTextbox(
        log_panel, height=180, fg_color=PANEL_ALT, border_color=BORDER, border_width=1,
        text_color=TEXT, font=(MONO, 10), corner_radius=6, wrap="word",
        scrollbar_button_color=BORDER, scrollbar_button_hover_color=PRIMARY
    )
    txt_log.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))

    last_log_index = [0]

    def append_log_line(line):
        txt_log.configure(state="normal")
        txt_log.insert(tk.END, line + "\\n")
        txt_log.see(tk.END)
        txt_log.configure(state="disabled")

    def clear_log():
        with lock:
            last_log_index[0] = len(ui_log_lines)
        txt_log.configure(state="normal")
        txt_log.delete("1.0", tk.END)
        txt_log.configure(state="disabled")

    ctk.CTkButton(
        log_title, text="Clear", width=60, height=24, fg_color=PANEL_ALT,
        hover_color=PRIMARY_HOVER, border_color=BORDER, border_width=1, text_color=TEXT,
        font=(MONO, 9, "bold"), corner_radius=4, command=clear_log
    ).grid(row=0, column=1, sticky="e")

    append_log_line(f"[{datetime.now().strftime('%H:%M:%S')}] > hệ thống sẵn sàng. nhấn RUN để khởi chạy.")

    # Cập nhật GUI mỗi 500ms
    def update_gui():
        if closing_state["closing"]:
            return

        stat_labels["total"].configure(text=f"{stats['total']:03d}")
        stat_labels["success"].configure(text=f"{stats['success']:03d}")
        stat_labels["fail"].configure(text=f"{stats['fail']:03d}")

        lbl_system_status.configure(text=f"[ {bot_state} ]")

        ensure_live_rows()
        for idx, worker in enumerate(workers):
            if idx >= len(live_rows): continue
            status = worker.get("status", "idle")
            if status == "running":
                status_color = PRIMARY
                elapsed = int(time.time() - worker.get("start_time", time.time()))
            elif status == "done":
                status_color = SUCCESS
                elapsed = int(worker.get("elapsed", 0))
            elif status == "fail":
                status_color = ERROR
                elapsed = int(worker.get("elapsed", 0))
            else:
                status_color = MUTED
                elapsed = int(worker.get("elapsed", 0))

            widgets = live_rows[idx]
            widgets["worker"].configure(text=f"W{worker.get('id', idx + 1):02d}", text_color=status_color)
            widgets["proxy"].configure(text=trim(worker.get('proxy', 'DIRECT'), 18))
            widgets["time"].configure(text=f"{elapsed}s")
            widgets["email"].configure(text=trim(worker.get('email', '-'), 30))
            widgets["step"].configure(text=trim(f"[{worker.get('step_num', 0)}/6] {worker.get('step', '-')}", 32))
            widgets["status"].configure(text=status.upper(), text_color=status_color)

        with lock:
            if last_log_index[0] > len(ui_log_lines):
                last_log_index[0] = max(0, len(ui_log_lines) - 200)
            new_logs = ui_log_lines[last_log_index[0]:]
            last_log_index[0] = len(ui_log_lines)

        for item in new_logs:
            append_log_line(item["line"] if isinstance(item, dict) else str(item))

        if not closing_state["closing"]:
            app.after(500, update_gui)

    title_colors = [PRIMARY, "#60a5fa", "#93c5fd", "#bfdbfe", "#93c5fd", "#60a5fa"]
    title_color_idx = [0]
    def animate_title():
        try:
            if closing_state["closing"]:
                return
            lbl_main_title.configure(text_color=title_colors[title_color_idx[0]])
            title_color_idx[0] = (title_color_idx[0] + 1) % len(title_colors)
            if not closing_state["closing"]:
                app.after(350, animate_title)
        except Exception: pass

    status_pulse_colors = {
        "RUNNING": [SUCCESS, "#34d399", "#6ee7b7", "#34d399"],
        "PAUSED": [WARNING, "#fbbf24", "#fcd34d", "#fbbf24"],
        "OFFLINE": [ERROR, "#f87171", "#fca5a5", "#f87171"]
    }
    status_pulse_idx = [0]
    def animate_status():
        try:
            if closing_state["closing"]:
                return
            pulse_list = status_pulse_colors.get(bot_state, [ERROR])
            color = pulse_list[status_pulse_idx[0] % len(pulse_list)]
            lbl_system_status.configure(text_color=color)
            status_pulse_idx[0] += 1
            if not closing_state["closing"]:
                app.after(300, animate_status)
        except Exception: pass

    animate_title()
    animate_status()
    update_gui()
    try:
        app.mainloop()
    except KeyboardInterrupt:
        on_close()

if __name__ == "__main__":
    print("=" * 50)
    print("   OUTLOOK AUTO REGISTER BOT")
    print("   Async Worker Pool | Playwright / Patchright | GUI")
    print("=" * 50)
    start_gui()
