import asyncio
import inspect
import json
import os
import random
import re
import shutil
import string
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime
from urllib.parse import quote, unquote, urlparse

from playwright.async_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    from tempmail import TempMailClient
    TEMPMAIL_LIB_AVAILABLE = True
except ImportError:
    TEMPMAIL_LIB_AVAILABLE = False

# ============ CẤU HÌNH MẶC ĐỊNH ============
CONFIG = {
    "MAIL_API_BASE": "",
    "MAIL_DOMAIN": "",
    "SIGNUP_URL": "",
    "CREDIT_URL": "",
    "PASSWORD": "",
    "LOOP_COUNT": 0,
    "CONCURRENCY": 2,
    "DELAY_BETWEEN_ACCOUNTS": 3.0,
    "OTP_POLL_INTERVAL": 2,
    "OTP_MAX_ATTEMPTS": 30,
    "HEADLESS": True,
    "MINIMIZE_TASKBAR": False,
    "CAPTCHA_API_KEY": "",
    "FIREFOX_PATH": "",
    "PROXIES": [],
    "MAILVIP_API_TOKEN": "",
    "LOG_FILE": "success_accounts.txt",
}

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
ACCOUNTS_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "accounts.txt")

# Danh sách các domain được tải từ API
AVAILABLE_DOMAINS = ["cskh-group.com"]

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
            print(f"Lỗi đọc config.json: {e}")

    # Tự động dò tìm đường dẫn Firefox nếu trống hoặc không hợp lệ
    if not CONFIG.get("FIREFOX_PATH") or not os.path.exists(CONFIG["FIREFOX_PATH"]) or "WindowsApps" in CONFIG["FIREFOX_PATH"] or "windowsapps" in CONFIG["FIREFOX_PATH"]:
        auto_path = find_firefox_path()
        # Gán auto_path (có thể trống "") để nếu không tìm thấy Firefox ngoài WindowsApps, nó sẽ dùng Firefox mặc định của Playwright
        CONFIG["FIREFOX_PATH"] = auto_path
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(CONFIG, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

def save_config():
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Lỗi lưu config.json: {e}")

# Tải cấu hình ngay khi chạy script
load_config()

# ============ STATE ============
bot_state = "OFFLINE"
stats = {"total": 0, "success": 0, "fail": 0}
workers = []  # Danh sách trạng thái cố định cho từng worker slot
should_stop = False
is_paused = False
lock = threading.Lock()
_vpn_manager = None
_vpn_manager_lock = threading.Lock()
_vpn_manager_import_error = None
SESSION_LOG_FILE = f"accounts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
ui_log_lines = []
MAX_UI_LOG_LINES = 1000

# ============ HELPER ============
def log(msg, msg_type="INFO", worker_id=0):
    timestamp = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "[*]", "SUCCESS": "[+]", "ERROR": "[-]", "WARN": "[!]", "STEP": "[>]"}.get(msg_type, "[*]")
    tag = f"W{worker_id:02d}" if worker_id > 0 else "BOT"
    console_line = f"[{timestamp}] {prefix} [{tag}] {msg}"
    ui_line = f"[{timestamp}] > {prefix} [{tag}] {msg}"

    try:
        print(console_line)
    except UnicodeEncodeError:
        try:
            print(console_line.encode("ascii", errors="replace").decode("ascii"))
        except Exception:
            pass
    except Exception:
        pass
    with lock:
        ui_log_lines.append({"line": ui_line, "type": msg_type})
        if len(ui_log_lines) > MAX_UI_LOG_LINES:
            del ui_log_lines[:-MAX_UI_LOG_LINES]

def get_vpn_manager(worker_id=0):
    """Lazy-load vpn_manager.py so importing bot.py does not start VPN setup."""
    global _vpn_manager, _vpn_manager_import_error

    if _vpn_manager is not None:
        return _vpn_manager

    with _vpn_manager_lock:
        if _vpn_manager is not None:
            return _vpn_manager
        try:
            from vpn_manager import VpnManager

            _vpn_manager = VpnManager()
            return _vpn_manager
        except Exception as exc:
            _vpn_manager_import_error = exc
            log(f"Khong the khoi tao vpn_manager.py: {exc}", "WARN", worker_id)
            return None

def save_account_sync(email, password, status):
    if "OK" not in status:
        return

    line = f"{datetime.now().isoformat()} | {email} | {password} | {status}\n"
    # Ghi vào session log file
    session_filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), SESSION_LOG_FILE)
    try:
        with open(session_filepath, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"Lỗi ghi session log: {e}")

    # Ghi vào global log file nếu thành công
    if "OK" in status:
        global_filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), CONFIG.get("LOG_FILE", "success_accounts.txt"))
        try:
            with open(global_filepath, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            print(f"Lỗi ghi global log: {e}")

async def save_account(email, password, status):
    await asyncio.to_thread(save_account_sync, email, password, status)

def generate_username():
    return "shady" + "".join(random.choices(string.ascii_lowercase + string.digits, k=6))

def get_modern_user_agent():
    useragent_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "useragent.txt")
    try:
        with open(useragent_path, "r", encoding="utf-8", errors="ignore") as f:
            candidates = [line.strip() for line in f if line.strip()]
        modern_candidates = [
            ua for ua in candidates
            if "Windows NT 5." not in ua and "Windows NT 6.0" not in ua and "MSIE" not in ua
        ]
        if modern_candidates:
            return random.choice(modern_candidates)
    except Exception:
        pass
    return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

def is_proxy_alive(proxy_str, timeout=2.5):
    import urllib.request
    raw = str(proxy_str).strip()
    if not raw:
        return False
    proxy_url = f"http://{raw}" if "://" not in raw else raw
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

def get_worker_proxy(worker_id):
    proxies = CONFIG.get("PROXIES") or []
    if not proxies:
        return ""
    # Thử tối đa 12 proxy ngẫu nhiên để chọn ra proxy hoạt động tốt
    for _ in range(12):
        candidate = random.choice(proxies)
        if is_proxy_alive(candidate):
            return candidate
    return random.choice(proxies)

def parse_proxy_for_camoufox(proxy_value):
    raw = (proxy_value or "").strip()
    if not raw:
        return None

    if "://" in raw:
        parsed = urlparse(raw)
        if not parsed.hostname or not parsed.port:
            return None
        proxy_config = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username:
            proxy_config["username"] = unquote(parsed.username)
        if parsed.password:
            proxy_config["password"] = unquote(parsed.password)
        return proxy_config

    parts = raw.split(":")
    if len(parts) >= 4:
        host = parts[0].strip()
        port = parts[1].strip()
        username = parts[2].strip()
        password = ":".join(parts[3:]).strip()
        if not host or not port:
            return None
        return {
            "server": f"http://{host}:{port}",
            "username": username,
            "password": password,
        }

    if len(parts) >= 2:
        host = parts[0].strip()
        port = parts[1].strip()
        if not host or not port:
            return None
        return {"server": f"http://{host}:{port}"}

    return None

def short_proxy(proxy_value, limit=24):
    if not proxy_value:
        return "DIRECT"
    return proxy_value if len(proxy_value) <= limit else proxy_value[:limit - 3] + "..."

# ============ MAIL API ============
def get_tempmail_client():
    if TEMPMAIL_LIB_AVAILABLE:
        try:
            api_key = CONFIG.get("TEMP_MAIL_API_KEY", "")
            base_url = CONFIG.get("MAIL_API_BASE", "https://api.temp-mail.io")
            base_url_lower = str(base_url).lower()
            if "api.internal.temp-mail.io" in base_url_lower:
                return None
            if "api.temp-mail.io" in base_url_lower and not str(api_key).strip():
                return None
            return TempMailClient(
                api_key=api_key,
                base_url=base_url,
            )
        except Exception as e:
            log(f"Lỗi khởi tạo TempMailClient SDK: {e}. Sử dụng HTTP fallback.", "WARN")
    return None

def normalize_domain_names(raw_domains):
    domains = []
    for item in raw_domains or []:
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = item.get("name") or item.get("domain")
        else:
            name = getattr(item, "name", None)

        if isinstance(name, str):
            name = name.strip()
            if name and name not in domains:
                domains.append(name)
    return domains

def is_temp_mail_api():
    return "api.temp-mail.io" in str(CONFIG.get("MAIL_API_BASE", "")).lower()

def is_temp_mail_internal_api():
    return "api.internal.temp-mail.io" in str(CONFIG.get("MAIL_API_BASE", "")).lower()

def temp_mail_web_headers():
    return {
        "Accept": "*/*",
        "Application-Name": "web",
        "Application-Version": "4.0.0",
        "Content-Type": "application/json",
        "Origin": "https://temp-mail.io",
        "Referer": "https://temp-mail.io/",
        "User-Agent": "Mozilla/5.0",
    }

def normalize_messages_payload(data):
    if not data:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    if "subject" in data:
        return [data]
    for key in ("messages", "data", "items", "hydra:member"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [item for item in value.values() if isinstance(item, dict)]
    return []

def message_text(message):
    if not isinstance(message, dict):
        return ""
    keys = ("subject", "intro", "text", "html", "body_text", "body_html", "body", "content")
    parts = [str(message.get(key, "")) for key in keys]
    return " ".join(parts)

def create_email():
    client = get_tempmail_client()
    if client:
        try:
            domain_param = CONFIG.get("MAIL_DOMAIN")
            if domain_param == "temp-mail.io":
                domain_param = None

            email_obj = client.create_email(domain=domain_param) if domain_param else client.create_email()
            if email_obj and hasattr(email_obj, "email"):
                return {"email": email_obj.email}
        except Exception as e:
            log(f"SDK tạo mail lỗi: {e}. Thử fallback bằng HTTP request.", "WARN")

    if is_temp_mail_internal_api():
        try:
            domain = str(CONFIG.get("MAIL_DOMAIN", "")).strip()
            payload_data = (
                {"domain": domain, "name": generate_username()}
                if domain and domain != "temp-mail.io"
                else {"min_name_length": 20, "max_name_length": 25}
            )
            req = urllib.request.Request(
                f"{CONFIG['MAIL_API_BASE'].rstrip('/')}/v3/email/new",
                data=json.dumps(payload_data).encode("utf-8"),
                headers=temp_mail_web_headers(),
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("email"):
                return {"email": data["email"]}
        except Exception as e:
            log(f"Lỗi gọi temp-mail.io internal tạo mail: {e}. Sử dụng email sinh offline.", "WARN")

    if is_temp_mail_api() and not str(CONFIG.get("TEMP_MAIL_API_KEY", "")).strip():
        username = generate_username()
        return {"email": f"{username}@{CONFIG.get('MAIL_DOMAIN', 'temp-mail.io')}"}

    try:
        username = generate_username()
        url = f"{CONFIG['MAIL_API_BASE'].rstrip('/')}/v1/emails" if is_temp_mail_api() else f"{CONFIG['MAIL_API_BASE'].rstrip('/')}/api/new"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
            "X-API-Key": CONFIG.get("TEMP_MAIL_API_KEY", ""),
        }
        if is_temp_mail_api():
            payload_data = {"email": f"{username}@{CONFIG['MAIL_DOMAIN']}"} if CONFIG.get("MAIL_DOMAIN") != "temp-mail.io" else {}
        else:
            payload_data = {"domain": CONFIG["MAIL_DOMAIN"], "username": username}
        payload = json.dumps(payload_data).encode("utf-8") if payload_data else None
        req = urllib.request.Request(
            url,
            data=payload,
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("email"):
            return {"email": data["email"]}
    except Exception as e:
        log(f"Lỗi gọi API tạo mail: {e}. Sử dụng email sinh offline.", "WARN")

    username = generate_username()
    return {"email": f"{username}@{CONFIG['MAIL_DOMAIN']}"}

def check_inbox(email_address):
    client = get_tempmail_client()
    if client:
        try:
            messages = client.list_email_messages(email_address)
            return [
                {
                    "id": getattr(msg, "id", None),
                    "from": getattr(msg, "from_addr", None),
                    "subject": getattr(msg, "subject", None),
                    "created_at": getattr(msg, "created_at", None),
                }
                for msg in messages
            ]
        except Exception as e:
            log(f"SDK lấy inbox lỗi: {e}. Thử fallback bằng HTTP request.", "WARN")

    if is_temp_mail_internal_api():
        urls = [
            f"{CONFIG['MAIL_API_BASE'].rstrip('/')}/v3/email/{email_address}/messages",
            f"{CONFIG['MAIL_API_BASE'].rstrip('/')}/v3/email/{quote(email_address, safe='')}/messages",
        ]
        for url in urls:
            try:
                req = urllib.request.Request(url, headers=temp_mail_web_headers())
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read())
                messages = normalize_messages_payload(data)
                if messages:
                    return messages
            except Exception:
                continue
        return []

    if is_temp_mail_api() and not str(CONFIG.get("TEMP_MAIL_API_KEY", "")).strip():
        return []

    try:
        url = f"{CONFIG['MAIL_API_BASE'].rstrip('/')}/v1/emails/{email_address}/messages" if is_temp_mail_api() else f"{CONFIG['MAIL_API_BASE'].rstrip('/')}/api/inbox/{email_address}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "X-API-Key": CONFIG.get("TEMP_MAIL_API_KEY", "")},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return data.get("messages", data.get("emails", []))
    except Exception:
        return []

def get_email_detail(mail_id):
    if isinstance(mail_id, dict):
        return mail_id

    client = get_tempmail_client()
    if client:
        try:
            msg = client.get_message(mail_id)
            if msg:
                return {
                    "id": getattr(msg, "id", None),
                    "subject": getattr(msg, "subject", None),
                    "body_text": getattr(msg, "body_text", None),
                    "body_html": getattr(msg, "body_html", None),
                    "text": getattr(msg, "body_text", None),
                    "html": getattr(msg, "body_html", None),
                    "from": getattr(msg, "from_addr", None),
                }
        except Exception as e:
            log(f"SDK lấy chi tiết mail lỗi: {e}. Thử fallback bằng HTTP request.", "WARN")

    if is_temp_mail_api() and not str(CONFIG.get("TEMP_MAIL_API_KEY", "")).strip():
        return None

    try:
        url = f"{CONFIG['MAIL_API_BASE'].rstrip('/')}/v1/messages/{mail_id}" if is_temp_mail_api() else f"{CONFIG['MAIL_API_BASE'].rstrip('/')}/api/email/{mail_id}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "X-API-Key": CONFIG.get("TEMP_MAIL_API_KEY", "")},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return data
    except Exception:
        return None

def lookup_hotmail_credentials(email_address):
    # Dò tìm thông tin credentials trong accounts.txt hoặc trong outlook_success.txt
    current_dir = os.path.dirname(os.path.abspath(__file__))
    paths = [
        os.path.join(current_dir, "accounts.txt"),
        os.path.join(current_dir, "OutlookRegister", "thanhcong.txt")
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        parts = line.split("|")
                        if len(parts) >= 1 and parts[0].strip().lower() == email_address.strip().lower():
                            r_token = parts[2].strip() if len(parts) >= 3 else ""
                            c_id = parts[3].strip() if len(parts) >= 4 else ""
                            return r_token, c_id
            except Exception:
                pass
    return None, None

def fetch_hotmail_otp(email_address, refresh_token, client_id, worker_id=0):
    import urllib.request
    import json
    url = "https://mailvip.net/index.php?action=check_hotmail"
    mailvip_token = os.environ.get("MAILVIP_API_TOKEN") or CONFIG.get("MAILVIP_API_TOKEN", "")
    headers = {
        "Content-Type": "application/json",
    }
    if str(mailvip_token).strip():
        headers["Authorization"] = f"Bearer {str(mailvip_token).strip()}"
    payload = {
        "email": email_address,
        "refresh_token": refresh_token,
        "client_id": client_id
    }
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                res_data = json.loads(resp.read().decode('utf-8'))
                if res_data.get("status") is True or res_data.get("code"):
                    return res_data.get("code")
                else:
                    err_msg = res_data.get("content", "Không có nội dung lỗi")
                    # Rút gọn bớt log không cần thiết trên giao diện
                    # log(f"API Hotmail: {err_msg}", "INFO", worker_id)
                    pass
    except Exception as e:
        log(f"Lỗi gọi API Hotmail mailvip.net: {e}", "WARN", worker_id)
    return None

async def wait_for_otp(email_address, worker_id=0):
    """Poll inbox bất đồng bộ không gây block event loop."""
    is_microsoft_mail = email_address.strip().lower().endswith(("@hotmail.com", "@outlook.com"))

    if is_microsoft_mail:
        r_token, c_id = lookup_hotmail_credentials(email_address)
        if not r_token or r_token == "NO_REFRESH_TOKEN":
            log(f"Không tìm thấy Refresh Token hợp lệ cho {email_address} trong accounts.txt. Thử dùng mặc định.", "WARN", worker_id)
            r_token = "NO_REFRESH_TOKEN"
        if not c_id:
            c_id = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"

        log(f"Đang dò tìm OTP từ Microsoft API (mailvip.net)...", "INFO", worker_id)

        for attempt in range(1, CONFIG["OTP_MAX_ATTEMPTS"] + 1):
            if should_stop:
                return None
            await asyncio.sleep(CONFIG["OTP_POLL_INTERVAL"])

            otp_code = await asyncio.to_thread(fetch_hotmail_otp, email_address, r_token, c_id, worker_id)
            if otp_code:
                match = re.search(r"\b\d{6}\b", str(otp_code))
                if match:
                    log(f"Đã nhận OTP từ Hotmail API: {match.group()} (Lần thử {attempt})", "SUCCESS", worker_id)
                    return match.group()
        return None

    # Fallback cho Temp-Mail
    for attempt in range(1, CONFIG["OTP_MAX_ATTEMPTS"] + 1):
        if should_stop:
            return None
        await asyncio.sleep(CONFIG["OTP_POLL_INTERVAL"])
        messages = await asyncio.to_thread(check_inbox, email_address)
        if messages:
            for msg in messages:
                match = re.search(r"\b\d{6}\b", message_text(msg))
                if match:
                    otp_code = match.group()
                    log(f"Đã nhận OTP: {otp_code} (Lần thử {attempt})", "SUCCESS", worker_id)
                    return otp_code

                mail_id = msg.get("id")
                if not mail_id:
                    continue
                # Gọi API lấy chi tiết nội dung email
                detail = await asyncio.to_thread(get_email_detail, mail_id)
                if detail:
                    content_str = (
                        f"{detail.get('subject', '')} "
                        f"{detail.get('text', '')} {detail.get('html', '')} "
                        f"{detail.get('body_text', '')} {detail.get('body_html', '')}"
                    )
                    match = re.search(r"\b\d{6}\b", content_str)
                    if match:
                        otp_code = match.group()
                        log(f"Đã nhận OTP: {otp_code} (Lần thử {attempt})", "SUCCESS", worker_id)
                        return otp_code
    return None

async def launch_safe_browser(worker_id, assigned_proxy, user_data_dir):
    """
    Khởi tạo Camoufox với cơ chế chống chặn vân tay nâng cao và ép cấu hình Proxy.
    """
    from camoufox.async_api import AsyncCamoufox

    # 1. Cấu hình cơ bản cho trình duyệt
    launch_args = {
        "headless": CONFIG.get("HEADLESS", True),
        "persistent_context": True,
        "user_data_dir": user_data_dir,
    }

    # 2. Xử lý Proxy (Bắt buộc phải dùng để tránh lỗi NS_ERROR_NET_TIMEOUT)
    proxy_config = parse_proxy_for_camoufox(assigned_proxy)
    if proxy_config:
        launch_args["proxy"] = proxy_config
        log(f"[W{worker_id:02d}] Đã gán Proxy thành công vào luồng.", "INFO", worker_id)
    else:
        log(f"[W{worker_id:02d}] CẢNH BÁO: Không có Proxy! Chạy IP gốc rất dễ lỗi mạng.", "WARN", worker_id)

    if CONFIG.get("FIREFOX_PATH") and os.path.exists(CONFIG["FIREFOX_PATH"]):
        launch_args["executable_path"] = CONFIG["FIREFOX_PATH"]

    # 3. Giu lai WebRTC de tranh mot so flow bi dut ket noi bat thuong.
    launch_args["block_webrtc"] = False

    return AsyncCamoufox(**launch_args)

NETWORK_RETRY_SIGNALS = (
    "timeout",
    "timed out",
    "net::err",
    "err_internet_disconnected",
    "err_network_changed",
    "err_connection_reset",
    "err_connection_closed",
    "err_connection_refused",
    "err_connection_timed_out",
    "err_name_not_resolved",
    "err_proxy_connection_failed",
    "err_tunnel_connection_failed",
    "err_socks_connection_failed",
    "ns_error_net_timeout",
    "ns_error_net_reset",
    "ns_error_net_interrupt",
    "econnreset",
    "econnrefused",
    "econnaborted",
    "connection reset",
    "connection refused",
    "connection aborted",
    "connection closed",
    "internet disconnected",
    "network changed",
    "proxy",
    "tunnel",
    "socket",
    "socket hang up",
    "dns",
    "name_not_resolved",
    "could not resolve",
)


def is_retryable_network_error(error):
    if isinstance(error, PlaywrightTimeoutError):
        return True
    error_msg = str(error).lower()
    return any(signal in error_msg for signal in NETWORK_RETRY_SIGNALS)

async def maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def network_recovery_hook(worker_id, attempt, error):
    log(
        f"Network hook sau loi mang lan {attempt}: {type(error).__name__}: {error}",
        "WARN",
        worker_id,
    )
    vpn_manager = get_vpn_manager(worker_id)
    if vpn_manager is None:
        if _vpn_manager_import_error is not None:
            log(f"Bo qua recovery vi vpn_manager.py loi import: {_vpn_manager_import_error}", "WARN", worker_id)
        return False

    try:
        log("Dang goi vpn_manager.change_ip() de tai lap ket noi WAN/VPN...", "WARN", worker_id)
        recovered = await asyncio.to_thread(vpn_manager.change_ip)
        if recovered:
            log("vpn_manager.change_ip() hoan tat, se thu lai dieu huong.", "SUCCESS", worker_id)
        else:
            log("vpn_manager.change_ip() khong doi duoc ket noi hoac dang bi tat trong config.", "WARN", worker_id)
        return recovered
    except Exception as exc:
        log(f"Loi khi chay vpn_manager.change_ip(): {exc}", "WARN", worker_id)
        return False


async def network_exception_wrapper(
    goto_factory,
    worker_id=0,
    *,
    max_retries=3,
    retry_delay_seconds=5.0,
    description="page.goto",
    recovery_hook=network_recovery_hook,
):
    """
    Run a Playwright page.goto call with network/proxy recovery.

    goto_factory must be a callable that creates a fresh awaitable every time,
    for example: lambda: page.goto(url, wait_until="domcontentloaded").
    A coroutine object cannot be reused safely across retries.
    """
    if not callable(goto_factory):
        raise TypeError("goto_factory must be callable, e.g. lambda: page.goto(url)")

    last_error = None
    total_attempts = max_retries + 1

    for attempt in range(1, total_attempts + 1):
        try:
            log(
                f"Dang tai {description} (Lan thu {attempt}/{total_attempts})...",
                "INFO",
                worker_id,
            )
            return await maybe_await(goto_factory())
        except Exception as exc:
            if not is_retryable_network_error(exc):
                raise

            last_error = exc
            log(
                f"Loi mang/proxy khi tai {description} (Lan thu {attempt}/{total_attempts}): {exc}",
                "WARN",
                worker_id,
            )

            if attempt > max_retries:
                break

            if recovery_hook is not None:
                await maybe_await(recovery_hook(worker_id, attempt, last_error))

            log(
                f"Cho {retry_delay_seconds:.1f}s truoc khi thu lai {description}...",
                "INFO",
                worker_id,
            )
            await asyncio.sleep(retry_delay_seconds)

    raise RuntimeError(
        f"Khong tai duoc {description} sau {total_attempts} lan thu"
    ) from last_error

async def goto_with_network_retry(
    page,
    url,
    worker_id,
    *,
    wait_until="domcontentloaded",
    timeout=30000,
    max_attempts=3,
    description="trang",
):
    return await network_exception_wrapper(
        lambda: page.goto(url, wait_until=wait_until, timeout=timeout),
        worker_id,
        max_retries=max_attempts,
        retry_delay_seconds=5.0,
        description=description,
    )


async def safe_type_text(page, text, min_delay_ms=60, max_delay_ms=140):
    for char in str(text):
        delay = random.randint(min_delay_ms, max_delay_ms)
        await page.keyboard.type(char, delay=delay)

async def safe_type_selector(
    page,
    selector,
    text,
    *,
    timeout=15000,
    clear_first=True,
    min_delay_ms=60,
    max_delay_ms=140,
):
    """
    Focus an input/textarea/contenteditable target and type text one character at a time.

    selector can be a CSS selector string or an already resolved Playwright Locator.
    Keyboard clearing is used instead of locator.fill() so slow browsers do not skip UI updates.
    """
    locator = page.locator(selector).first if isinstance(selector, str) else selector

    await locator.wait_for(state="visible", timeout=timeout)
    try:
        await locator.scroll_into_view_if_needed(timeout=timeout)
    except Exception:
        pass

    await locator.click(timeout=timeout)
    await asyncio.sleep(0.05)

    if clear_first:
        await page.keyboard.press("Control+A")
        await asyncio.sleep(0.03)
        await page.keyboard.press("Backspace")
        await asyncio.sleep(0.05)

    await safe_type_text(
        page,
        text,
        min_delay_ms=min_delay_ms,
        max_delay_ms=max_delay_ms,
    )
    return locator

async def safe_navigate_to_signup(page, worker_id):
    """
    Hàm điều hướng an toàn tối đa giúp xử lý triệt để lỗi mạng:
    NS_ERROR_NET_TIMEOUT, NS_ERROR_NET_INTERRUPT
    """
    signup_url = CONFIG["SIGNUP_URL"]
    max_attempts = 3

    # Ép sử dụng User-Agent phiên bản mới, xóa bỏ hoàn toàn giả lập Windows XP/7/OS cũ
    chosen_ua = get_modern_user_agent()
    await page.set_extra_http_headers({"User-Agent": chosen_ua})

    try:
        response = await goto_with_network_retry(
            page,
            signup_url,
            worker_id,
            wait_until="commit",
            timeout=60000,
            max_attempts=max_attempts,
            description="trang dang ky OpenArt",
        )

        await asyncio.sleep(2.5)

        if response and response.status >= 400:
            log(f"OpenArt phan hoi loi HTTP {response.status}. Co the IP Proxy da bi blacklist.", "WARN", worker_id)

        return True

    except Exception as e:
        error_msg = str(e)
        log(f"Loi ket noi trang dang ky OpenArt: {error_msg}", "WARN", worker_id)

        if "Target page, context or browser has been closed" in error_msg:
            log("Trinh duyet bi sap bat thuong. Dung thu lai.", "ERROR", worker_id)
            return False

        log("Da thu het so lan quy dinh nhung van that bai do nghen mang.", "ERROR", worker_id)
        return False

# ============ REGISTER ENGINE (Single account flow) ============
async def register_one(worker_id, account_index, assigned_proxy, custom_email=None, custom_password=None, worker_profile_dir=None):
    from camoufox.async_api import AsyncCamoufox

    # Sử dụng tài khoản tùy chỉnh từ file nếu có, nếu không thì tự sinh
    if custom_email:
        email = custom_email
        password = custom_password
        log(f"Sử dụng tài khoản từ file: {email}", "INFO", worker_id)
    else:
        email_data = create_email()
        email = email_data["email"]
        password = CONFIG["PASSWORD"]

    registered = False
    claimed = False
    start_time = time.time()
    owns_worker_profile = False
    if worker_profile_dir is None:
        worker_profile_dir = tempfile.mkdtemp(prefix=f"openart-worker-{worker_id:02d}-")
        owns_worker_profile = True

    # Cập nhật trạng thái Worker Slot trong GUI
    w = workers[worker_id - 1]
    with lock:
        w["email"] = email
        w["step"] = "Khởi tạo trình duyệt..."
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

    async def automate(browser):
        nonlocal registered, claimed
        page = await browser.new_page()

        # Tăng tốc độ load trang OpenArt bằng cách chặn các tài nguyên nặng và tracking quảng cáo
        async def block_useless_resources(route):
            req_type = route.request.resource_type
            url = route.request.url.lower()
            if (
                req_type in ("image", "font", "media")
                or "google-analytics" in url
                or "googletagmanager" in url
                or "mixpanel" in url
                or "segment" in url
                or "sentry.io" in url
                or "facebook.net" in url
            ):
                await route.abort()
            else:
                await route.continue_()

        await page.route("**/*", block_useless_resources)

        # STEP 1: Vào trang đăng ký
        ws(1, "Vào trang signup...")
        nav_success = await safe_navigate_to_signup(page, worker_id)
        if not nav_success:
            return False

        # STEP 2: Điền email
        ws(2, "Điền email...")
        email_selectors = ['input[type="email"]', 'input[name="email"]', 'input[placeholder*="email" i]']
        email_input = None
        for sel in email_selectors:
            try:
                locator = page.locator(sel).first
                if await locator.is_visible(timeout=2000):
                    email_input = locator
                    break
            except Exception:
                continue

        if not email_input:
            email_input = page.locator('input[type="email"]').first

        await safe_type_selector(page, email_input, email, timeout=15000)

        # STEP 3: Điền password
        ws(3, "Điền password...")
        pw_inputs = page.locator('input[type="password"]')
        pw_count = await pw_inputs.count()
        if pw_count == 0:
            # Fallback nếu selector type="password" không tìm thấy
            pw_inputs = page.locator('input[placeholder*="password" i]')
            pw_count = await pw_inputs.count()

        for i in range(max(1, pw_count)):
            try:
                inp = pw_inputs.nth(i)
                await safe_type_selector(page, inp, password, timeout=2000)
            except Exception:
                continue
        await asyncio.sleep(0.2)

        # STEP 4: Bấm nút Sign Up / Đăng ký
        ws(4, "Bấm Sign Up...")
        signup_btn = None
        for btn_text in ["Sign Up", "Continue", "Register", "Create Account", "Sign up"]:
            btn = page.locator(f'button:has-text("{btn_text}")').first
            try:
                if await btn.is_visible(timeout=1000):
                    if not await btn.is_disabled():
                        signup_btn = btn
                        break
            except Exception:
                continue

        if not signup_btn:
            # Fallback CSS selector submit button
            signup_btn = page.locator('button[type="submit"]').first

        await signup_btn.wait_for(state="visible", timeout=10000)
        await signup_btn.click()

        # STEP 5: Chờ nhận OTP
        ws(5, "Đang chờ OTP...")
        otp = await wait_for_otp(email, worker_id)
        if not otp:
            ws(5, "Không nhận được OTP!")
            return False

        # STEP 6: Điền mã OTP
        ws(6, f"Điền OTP {otp}...")
        otp_filled = False
        for _ in range(10):
            if should_stop:
                return False
            # Phương pháp 1: Điền từng ô (maxlength="1")
            all_inputs = page.locator("input")
            count = await all_inputs.count()
            otp_inputs = []
            for idx in range(count):
                ml = await all_inputs.nth(idx).get_attribute("maxlength") or ""
                if ml == "1":
                    otp_inputs.append(all_inputs.nth(idx))

            if len(otp_inputs) >= 6:
                for i in range(6):
                    await safe_type_selector(page, otp_inputs[i], otp[i], timeout=5000)
                    await asyncio.sleep(0.05)
                otp_filled = True
                break

            # Phương pháp 2: Điền vào ô chung chứa code/otp/verify
            for idx in range(count):
                autocomplete = await all_inputs.nth(idx).get_attribute("autocomplete") or ""
                name = await all_inputs.nth(idx).get_attribute("name") or ""
                inp_type = await all_inputs.nth(idx).get_attribute("type") or ""
                ph = await all_inputs.nth(idx).get_attribute("placeholder") or ""
                if (
                    autocomplete == "one-time-code"
                    or "otp" in name.lower()
                    or "code" in name.lower()
                    or "verify" in name.lower()
                    or (
                        inp_type in ("text", "number", "tel")
                        and "email" not in ph.lower()
                        and "password" not in ph.lower()
                    )
                ):
                    otp_input = all_inputs.nth(idx)
                    await safe_type_selector(page, otp_input, otp, timeout=5000)
                    otp_filled = True
                    break

            if otp_filled:
                break
            await asyncio.sleep(1.0)

        # STEP 7: Bấm Verify xác nhận
        ws(7, "Xác minh tài khoản...")
        verify_btn = None
        for btn_text in ["Verify", "Create Account", "Continue", "Submit", "Xác nhận"]:
            btn = page.locator(f'button:has-text("{btn_text}")').first
            try:
                if await btn.is_visible(timeout=1000):
                    if not await btn.is_disabled():
                        verify_btn = btn
                        break
            except Exception:
                continue

        if verify_btn:
            await verify_btn.click()

        # Đợi DOM chuyển hướng hoặc hết màn hình OTP
        ws(7, "Đợi xác thực thành công...")
        for _ in range(30):
            if should_stop:
                return False
            await asyncio.sleep(0.5)
            body_text = await page.inner_text("body")
            if "Verification Code" not in body_text and "Verify Email" not in body_text:
                break

        body_text = await page.inner_text("body")
        if "Verification Code" not in body_text and "Verify Email" not in body_text:
            registered = True
            log(f"Đăng ký OK: {email}", "SUCCESS", worker_id)
        else:
            log(f"Lỗi: Không vượt qua được màn hình OTP", "ERROR", worker_id)
            return False

        # STEP 8: Nhận Credits
        if registered:
            ws(8, "Đang nhận credit...")
            current_url = page.url
            if "credit" not in current_url:
                await goto_with_network_retry(
                    page,
                    CONFIG["CREDIT_URL"],
                    worker_id,
                    wait_until="domcontentloaded",
                    timeout=30000,
                    max_attempts=3,
                    description="trang credit OpenArt",
                )

            # Các locator cho nút Claim Credits
            claim_selectors = [
                'button:has-text("Claim Credits")',
                'span:has-text("Claim Credits")',
                'div:has-text("Claim Credits")',
                'button:has-text("Claim")',
                '[class*="claim" i]'
            ]

            claimed_btn = None
            for sel in claim_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=3000):
                        claimed_btn = btn
                        break
                except Exception:
                    continue

            if claimed_btn:
                await claimed_btn.wait_for(state="visible", timeout=10000)
                await claimed_btn.click()
                claimed = True
                log(f"NHẬN CREDITS THÀNH CÔNG: {email}", "SUCCESS", worker_id)
                # Đợi một chút để hệ thống xử lý ghi nhận credit
                await asyncio.sleep(2.0)
            else:
                log("Không tìm thấy nút Claim Credits!", "ERROR", worker_id)

        return claimed

    # Chuẩn bị khởi chạy trình duyệt Camoufox an toàn
    try:
        async with await launch_safe_browser(worker_id, assigned_proxy, worker_profile_dir) as browser:
            claimed = await automate(browser)
    except TypeError as exc:
        # Cơ chế fallback nếu có lỗi kiểu dữ liệu khi truyền proxy/options
        log(f"Camoufox lỗi cấu hình ({exc}). Khởi chạy không proxy/headless mặc định.", "WARN", worker_id)
        fallback_args = {
            "headless": CONFIG["HEADLESS"],
            "persistent_context": True,
            "user_data_dir": worker_profile_dir,
        }
        if CONFIG["FIREFOX_PATH"] and os.path.exists(CONFIG["FIREFOX_PATH"]):
            fallback_args["executable_path"] = CONFIG["FIREFOX_PATH"]
        try:
            from camoufox.async_api import AsyncCamoufox
            async with AsyncCamoufox(**fallback_args) as browser:
                claimed = await automate(browser)
        except Exception as e:
            log(f"Trình duyệt lỗi nghiêm trọng: {e}", "ERROR", worker_id)
    except Exception as e:
        log(f"Trình duyệt lỗi: {e}", "ERROR", worker_id)
    finally:
        if owns_worker_profile:
            shutil.rmtree(worker_profile_dir, ignore_errors=True)
            log(f"Da xoa profile tam: {worker_profile_dir}", "INFO", worker_id)

    # Tổng kết trạng thái và ghi log
    status_str = "REG+CLAIM_OK" if claimed else ("REG_OK" if registered else "FAIL")
    await save_account(email, password, status_str)

    with lock:
        w["status"] = "done" if claimed else "fail"
        w["step"] = status_str
        w["elapsed"] = int(time.time() - start_time)
        if claimed:
            w["ok"] += 1
            stats["success"] += 1
        else:
            w["fail"] += 1
            stats["fail"] += 1
        stats["total"] += 1

    return claimed


# ============ WORKER POOL SYSTEM ============
async def worker_loop(worker_id, queue, total_accounts):
    """Luồng hoạt động độc lập của từng worker slot, lấy tác vụ từ queue."""
    log(f"Worker {worker_id:02d} đã khởi động.", "INFO", worker_id)
    worker_profile_dir = tempfile.mkdtemp(prefix=f"openart-worker-{worker_id:02d}-")
    log(f"Profile tam cua worker: {worker_profile_dir}", "INFO", worker_id)

    try:
        while not should_stop:
            if is_paused:
                await asyncio.sleep(0.5)
                continue

            # Kiểm tra xem queue có rỗng không khi ở chế độ giới hạn account
            if queue.empty() and total_accounts > 0:
                break

            try:
                # Chờ lấy task từ queue
                task_data = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if total_accounts > 0 and queue.empty():
                    break
                continue

            account_idx = task_data["index"]
            custom_email = task_data.get("email")
            custom_password = task_data.get("password")

            # Đảm bảo mỗi worker lấy đúng proxy được phân bổ
            assigned_proxy = get_worker_proxy(worker_id)

            target_display = custom_email if custom_email else "Tự sinh Email"
            log(f"Nhận tài khoản thứ #{account_idx} ({target_display})", "INFO", worker_id)

            try:
                # Thực thi quy trình đăng ký
                await register_one(
                    worker_id,
                    account_idx,
                    assigned_proxy,
                    custom_email,
                    custom_password,
                    worker_profile_dir,
                )
            finally:
                queue.task_done()

            # Delay giữa các lần đăng ký
            if CONFIG["DELAY_BETWEEN_ACCOUNTS"] > 0 and not should_stop:
                await asyncio.sleep(CONFIG["DELAY_BETWEEN_ACCOUNTS"])

    finally:
        shutil.rmtree(worker_profile_dir, ignore_errors=True)
        log(f"Da xoa profile tam cua worker: {worker_profile_dir}", "INFO", worker_id)

        # Đặt trạng thái worker slot về IDLE khi kết thúc
        with lock:
            w = workers[worker_id - 1]
            w["status"] = "idle"
            w["step"] = "Hoàn thành / Idle"
        log(f"Worker {worker_id:02d} dừng hoạt động.", "INFO", worker_id)


async def queue_producer(queue):
    """Task nền sản sinh công việc không giới hạn khi total_accounts = 0 và không dùng file accounts.txt."""
    idx = 1
    while not should_stop:
        if queue.qsize() < 10:
            await queue.put({"index": idx, "email": None, "password": None})
            idx += 1
        else:
            await asyncio.sleep(0.2)


def read_local_accounts():
    """Đọc tệp accounts.txt cục bộ và trả về danh sách dict."""
    acc_list = []
    if not os.path.exists(ACCOUNTS_FILE_PATH):
        return acc_list
    try:
        with open(ACCOUNTS_FILE_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("|")
                if len(parts) >= 2:
                    acc_list.append({
                        "email": parts[0].strip(),
                        "password": parts[1].strip()
                    })
    except Exception as e:
        print(f"Lỗi đọc accounts.txt: {e}")
    return acc_list


async def run_pool(concurrency, total_accounts):
    global bot_state

    # Khởi động hàng đợi
    queue = asyncio.Queue()
    producer_task = None

    # Đọc tài khoản cục bộ từ accounts.txt nếu có
    local_accounts = read_local_accounts()

    if local_accounts:
        log(f"Phát hiện tệp accounts.txt chứa {len(local_accounts)} tài khoản.", "SUCCESS")

        # Xác định số lượng tài khoản cần chạy
        limit = total_accounts if total_accounts > 0 else len(local_accounts)
        limit = min(limit, len(local_accounts))

        log(f"Nạp {limit} tài khoản từ accounts.txt vào hàng đợi...", "INFO")
        for idx in range(1, limit + 1):
            acc = local_accounts[idx - 1]
            await queue.put({
                "index": idx,
                "email": acc["email"],
                "password": acc["password"]
            })

        # Điều chỉnh lại tổng số acc chạy thực tế của phiên
        total_accounts = limit
    else:
        # Nếu không có file accounts.txt, chạy ở chế độ Tự sinh Email
        if total_accounts > 0:
            log(f"Khởi tạo hàng đợi tự sinh với {total_accounts} tài khoản.", "INFO")
            for idx in range(1, total_accounts + 1):
                await queue.put({"index": idx, "email": None, "password": None})
        else:
            log("Chạy ở chế độ VÔ HẠN tài khoản tự sinh.", "INFO")
            producer_task = asyncio.create_task(queue_producer(queue))

    # Tạo các worker task hoạt động song song
    worker_tasks = []
    for w_id in range(1, concurrency + 1):
        task = asyncio.create_task(worker_loop(w_id, queue, total_accounts))
        worker_tasks.append(task)

    # Chờ các worker kết thúc
    if total_accounts > 0:
        await asyncio.gather(*worker_tasks, return_exceptions=True)
    else:
        while not should_stop:
            await asyncio.sleep(1.0)
        for task in worker_tasks:
            task.cancel()
        if producer_task:
            producer_task.cancel()

    bot_state = "OFFLINE"
    log(f"DỪNG: Tổng {stats['total']} | Thành công {stats['success']} | Lỗi {stats['fail']}", "SUCCESS")


def start_bot_thread(concurrency, total_accounts):
    global bot_state, should_stop, is_paused
    should_stop = False
    is_paused = False
    bot_state = "RUNNING"

    # Khởi tạo/Reset danh sách worker slot cố định
    with lock:
        workers.clear()
        for w_id in range(1, concurrency + 1):
            workers.append({
                "id": w_id,
                "email": "-",
                "step": "Chờ lệnh...",
                "step_num": 0,
                "status": "idle",
                "ok": 0,
                "fail": 0,
                "start_time": time.time(),
                "elapsed": 0,
                "proxy": short_proxy(get_worker_proxy(w_id)),
            })

    def run():
        asyncio.run(run_pool(concurrency, total_accounts))

    t = threading.Thread(target=run, daemon=True)
    t.start()


# ============ GUI DESKTOP (CustomTkinter Cyberpunk Edition) ============
def start_gui():
    import customtkinter as ctk
    import tkinter as tk
    from tkinter import filedialog
    import os, json, time, urllib.request, threading
    from datetime import datetime

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    # Premium Flat Dark UI Palette
    BG = "#090d16"
    PANEL = "#131c2e"
    PANEL_ALT = "#1e293b"
    PRIMARY = "#3b82f6"
    PRIMARY_HOVER = "#2563eb"
    TEXT = "#f8fafc"
    MUTED = "#94a3b8"
    SUCCESS = "#10b981"
    ERROR = "#ef4444"
    ERROR_HOVER = "#dc2626"
    WARNING = "#f59e0b"
    WARNING_HOVER = "#d97706"
    BORDER = "#334155"
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
    app.title("OPENART CLAIM BOT")
    app.geometry("850x780")
    app.minsize(700, 650)
    app.configure(fg_color=BG)

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
    lbl_main_title = label(title_group, " OPENART CREDIT BOT ", 15, PRIMARY, "bold")
    lbl_main_title.pack(side="left")
    label(title_group, "v3.2 // modern ui // async", 10, MUTED, "bold").pack(side="left", padx=(12, 0))

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

    # ---- Compact Configuration Form ----
    config_frame = make_frame(main, fg=BG, border=BORDER, radius=8)
    config_frame.grid(row=2, column=0, sticky="ew", padx=0, pady=(0, 6))
    config_frame.grid_columnconfigure(0, weight=1)

    # Row 1: Concurrency, Total accounts, Domain, Delay
    row1 = ctk.CTkFrame(config_frame, fg_color=BG, corner_radius=0)
    row1.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
    row1.grid_columnconfigure((1, 3, 5, 7), weight=1)

    label(row1, "Số luồng:", 11, TEXT, "bold").grid(row=0, column=0, sticky="w")
    ent_concurrency = entry(row1, 50)
    ent_concurrency.grid(row=0, column=1, padx=(4, 10), sticky="ew")
    ent_concurrency.insert(0, str(CONFIG["CONCURRENCY"]))

    label(row1, "Tổng tài khoản:", 11, TEXT, "bold").grid(row=0, column=2, sticky="w")
    ent_total_accounts = entry(row1, 50)
    ent_total_accounts.grid(row=0, column=3, padx=(4, 10), sticky="ew")
    ent_total_accounts.insert(0, "20")

    label(row1, "Domain:", 11, TEXT, "bold").grid(row=0, column=4, sticky="w")
    opt_domain = ctk.CTkOptionMenu(
        row1, height=28, fg_color=PANEL_ALT, button_color=PRIMARY,
        button_hover_color=PRIMARY_HOVER, text_color=TEXT,
        font=(MONO, 11), corner_radius=6, values=AVAILABLE_DOMAINS
    )
    opt_domain.grid(row=0, column=5, padx=(4, 10), sticky="ew")
    opt_domain.set(CONFIG["MAIL_DOMAIN"])

    label(row1, "Độ trễ (s):", 11, TEXT, "bold").grid(row=0, column=6, sticky="w")
    ent_delay = entry(row1, 50)
    ent_delay.grid(row=0, column=7, padx=(4, 0), sticky="ew")
    ent_delay.insert(0, str(CONFIG["DELAY_BETWEEN_ACCOUNTS"]))

    # Row 2: Checkboxes, 2Captcha, Thêm Domain
    row2 = ctk.CTkFrame(config_frame, fg_color=BG, corner_radius=0)
    row2.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
    row2.grid_columnconfigure(3, weight=1)
    row2.grid_columnconfigure(5, weight=1)

    chk_headless_var = ctk.BooleanVar(value=CONFIG["HEADLESS"])
    checkbox(row2, "Ẩn trình duyệt", chk_headless_var).grid(row=0, column=0, sticky="w", padx=(0, 15))
    chk_minimize_var = ctk.BooleanVar(value=CONFIG["MINIMIZE_TASKBAR"])
    checkbox(row2, "Thu nhỏ taskbar", chk_minimize_var).grid(row=0, column=1, sticky="w", padx=(0, 15))

    label(row2, "2Captcha API:", 11, TEXT, "bold").grid(row=0, column=2, sticky="w")
    ent_captcha = entry(row2, placeholder="Để trống = manual")
    ent_captcha.grid(row=0, column=3, sticky="ew", padx=(4, 15))
    ent_captcha.insert(0, CONFIG.get("CAPTCHA_API_KEY", ""))

    label(row2, "Thêm Domain:", 11, TEXT, "bold").grid(row=0, column=4, sticky="w")
    ent_new_domain = entry(row2, placeholder="Nhập domain...")
    ent_new_domain.grid(row=0, column=5, sticky="ew", padx=(4, 4))

    def add_domain_to_api():
        new_dom = ent_new_domain.get().strip()
        if not new_dom:
            log("Vui lòng nhập tên miền muốn thêm!", "WARN")
            return
        log(f"Đang gửi yêu cầu thêm domain: {new_dom}...", "INFO")
        def run_post():
            try:
                base_url = CONFIG["MAIL_API_BASE"].rstrip("/")
                if is_temp_mail_internal_api():
                    log("temp-mail.io internal khong ho tro them domain tu tool nay.", "WARN")
                    return
                if is_temp_mail_api() and not str(CONFIG.get("TEMP_MAIL_API_KEY", "")).strip():
                    log("TEMP_MAIL_API_KEY chưa cấu hình, không thể thêm domain qua temp-mail.io.", "WARN")
                    return
                url = f"{base_url}/v1/domains" if is_temp_mail_api() else f"{base_url}/api/domains"
                payload = json.dumps({"domain": new_dom}).encode("utf-8")
                headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0", "X-API-Key": CONFIG.get("TEMP_MAIL_API_KEY", "")}
                req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                if data.get("ok") or data.get("domain") or data.get("name"):
                    log(f"Thành công: Đã thêm domain {new_dom} vào hệ thống!", "SUCCESS")
                    load_api_domains()
                else:
                    log("Lỗi: Không thể thêm domain.", "ERROR")
            except Exception as e:
                log(f"Lỗi thêm domain API: {e}", "ERROR")
        threading.Thread(target=run_post, daemon=True).start()

    ctk.CTkButton(
        row2, text="Thêm", width=60, height=26, fg_color=PRIMARY, hover_color=PRIMARY_HOVER,
        font=(MONO, 10, "bold"), corner_radius=6, command=add_domain_to_api
    ).grid(row=0, column=6, sticky="e")

    # Row 3: Proxy List & Firefox
    row3 = ctk.CTkFrame(config_frame, fg_color=BG, corner_radius=0)
    row3.grid(row=2, column=0, sticky="ew", padx=10, pady=(4, 8))
    row3.grid_columnconfigure(1, weight=1)
    row3.grid_columnconfigure(4, weight=1)

    label(row3, "Proxy:", 11, TEXT, "bold").grid(row=0, column=0, sticky="nw")
    txt_proxy = ctk.CTkTextbox(
        row3, height=36, fg_color=PANEL_ALT, border_color=BORDER, border_width=1,
        text_color=TEXT, font=(MONO, 11), corner_radius=6, wrap="none",
        scrollbar_button_color=BORDER, scrollbar_button_hover_color=PRIMARY
    )
    txt_proxy.grid(row=0, column=1, sticky="ew", padx=(4, 4))
    if CONFIG.get("PROXIES"):
        txt_proxy.insert(tk.END, "\n".join(CONFIG["PROXIES"]))

    def download_github_proxies():
        log("Đang kết nối tải danh sách proxy từ 7 nguồn chất lượng...", "WARN")
        proxy_sources = [
            "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
            "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
            "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
            "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
            "https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt",
            "https://raw.githubusercontent.com/mmpx12/proxy-list/master/https.txt",
            "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http.txt"
        ]

        all_fetched = set()

        def fetch_source(url):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    content_text = resp.read().decode("utf-8", errors="ignore")
                lines = [line.strip() for line in content_text.splitlines() if line.strip() and not line.strip().startswith("#") and not line.strip().startswith("//")]
                return lines
            except Exception:
                return []

        # Chạy tải tuần tự / song song đơn giản
        for url in proxy_sources:
            lines = fetch_source(url)
            all_fetched.update(lines)

        proxies_fetched = sorted(list(all_fetched))

        if proxies_fetched:
            app.after(0, lambda: txt_proxy.delete("1.0", tk.END))
            app.after(0, lambda: txt_proxy.insert(tk.END, "\n".join(proxies_fetched)))
            log(f"Tổng hợp thành công {len(proxies_fetched)} Proxy chất lượng từ 7 nguồn!", "SUCCESS")
            app.after(100, refresh_config_from_gui)
        else:
            log("Tải danh sách proxy thất bại hoặc danh sách trống.", "ERROR")

    def trigger_github_proxy_fetch():
        threading.Thread(target=download_github_proxies, daemon=True).start()

    ctk.CTkButton(
        row3, text="Tải Github", width=70, height=28, fg_color=PANEL_ALT,
        border_color=BORDER, border_width=1, hover_color=PRIMARY, text_color=TEXT,
        font=(MONO, 10, "bold"), corner_radius=6, command=trigger_github_proxy_fetch
    ).grid(row=0, column=2, sticky="w", padx=(0, 15))

    label(row3, "Firefox:", 11, TEXT, "bold").grid(row=0, column=3, sticky="w")
    ent_firefox = entry(row3, placeholder="Đường dẫn executable")
    ent_firefox.grid(row=0, column=4, sticky="ew", padx=(4, 4))
    ent_firefox.insert(0, CONFIG.get("FIREFOX_PATH", ""))

    def choose_firefox_path():
        path = filedialog.askopenfilename(title="Chọn Firefox executable", filetypes=[("Firefox executable", "*.exe"), ("All files", "*.*")])
        if path:
            ent_firefox.delete(0, tk.END)
            ent_firefox.insert(0, path)

    ctk.CTkButton(
        row3, text="...", width=36, height=28, fg_color=PANEL_ALT, border_color=BORDER,
        border_width=1, hover_color=PRIMARY, text_color=TEXT, font=(MONO, 10, "bold"),
        corner_radius=6, command=choose_firefox_path
    ).grid(row=0, column=5, sticky="e")

    # ---- Action Buttons ----
    actions = ctk.CTkFrame(main, fg_color=BG, corner_radius=0)
    actions.grid(row=3, column=0, sticky="ew", padx=0, pady=(0, 6))
    actions.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)

    def refresh_config_from_gui():
        CONFIG["CONCURRENCY"] = parse_int(ent_concurrency.get(), CONFIG["CONCURRENCY"], 1)
        CONFIG["MAIL_DOMAIN"] = opt_domain.get().strip() or CONFIG["MAIL_DOMAIN"]
        CONFIG["DELAY_BETWEEN_ACCOUNTS"] = parse_float(ent_delay.get(), CONFIG["DELAY_BETWEEN_ACCOUNTS"], 0)
        CONFIG["HEADLESS"] = bool(chk_headless_var.get())
        CONFIG["MINIMIZE_TASKBAR"] = bool(chk_minimize_var.get())
        CONFIG["CAPTCHA_API_KEY"] = ent_captcha.get().strip()
        CONFIG["FIREFOX_PATH"] = ent_firefox.get().strip()

        raw_lines = txt_proxy.get("1.0", tk.END).splitlines()
        CONFIG["PROXIES"] = [
            line.strip()
            for line in raw_lines
            if line.strip() and not line.strip().startswith("#") and not line.strip().startswith("//")
        ]
        save_config()

    def action_run():
        if bot_state == "RUNNING":
            log("Hệ thống đang chạy, bỏ qua lệnh RUN mới.", "WARN")
            return
        refresh_config_from_gui()
        total_accounts = parse_int(ent_total_accounts.get(), 0, 0)
        log(f"RUN concurrency={CONFIG['CONCURRENCY']} total={total_accounts} domain={CONFIG['MAIL_DOMAIN']} delay={CONFIG['DELAY_BETWEEN_ACCOUNTS']}s proxies={len(CONFIG['PROXIES'])}", "INFO")
        if CONFIG["MINIMIZE_TASKBAR"]:
            app.after(150, app.iconify)
        start_bot_thread(CONFIG["CONCURRENCY"], total_accounts)

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
        log("STOP khẩn cấp đã được gửi tới toàn bộ hệ thống.", "ERROR")

    def action_fix_token():
        log("Kích hoạt lệnh FIX TOKEN.", "WARN")

    def action_accounts():
        filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), SESSION_LOG_FILE)
        if not os.path.exists(filepath):
            try:
                with open(filepath, "a", encoding="utf-8"): pass
            except Exception: pass
        try:
            os.startfile(filepath)
        except Exception as exc:
            log(f"Không mở được file accounts: {exc}", "ERROR")

    btn_style = {"height": 36, "corner_radius": 6, "font": (MONO, 12, "bold")}
    ctk.CTkButton(actions, text="▶ RUN", fg_color=SUCCESS, hover_color="#059669", text_color="#ffffff", command=action_run, **btn_style).grid(row=0, column=0, sticky="ew", padx=(0, 4))
    ctk.CTkButton(actions, text="▐▐ PAUSE", fg_color=PANEL_ALT, hover_color=WARNING_HOVER, border_color=WARNING, border_width=1, text_color=WARNING, command=action_pause, **btn_style).grid(row=0, column=1, sticky="ew", padx=4)
    ctk.CTkButton(actions, text="■ STOP", fg_color=PANEL_ALT, hover_color=ERROR_HOVER, border_color=ERROR, border_width=1, text_color=ERROR, command=action_stop, **btn_style).grid(row=0, column=2, sticky="ew", padx=4)
    ctk.CTkButton(actions, text="🔧 FIX TOKEN", fg_color=PANEL_ALT, hover_color=PRIMARY_HOVER, border_color=PRIMARY, border_width=1, text_color=PRIMARY, command=action_fix_token, **btn_style).grid(row=0, column=3, sticky="ew", padx=4)
    ctk.CTkButton(actions, text="📂 ACCOUNTS", fg_color=PANEL_ALT, hover_color=PRIMARY_HOVER, border_color=PRIMARY, border_width=1, text_color=PRIMARY, command=action_accounts, **btn_style).grid(row=0, column=4, sticky="ew", padx=(4, 0))

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

            widgets["step"] = label(card, "[0/8] Chờ lệnh...", 10, TEXT)
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
        txt_log.insert(tk.END, line + "\n")
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

    # Tải danh sách domain từ API mail
    def load_api_domains():
        log("Đang tải danh sách domain từ API mail...", "INFO")
        domains = []
        client = get_tempmail_client()
        if client:
            try:
                domains = normalize_domain_names(client.list_domains())
            except Exception as e:
                log(f"SDK lấy domain lỗi: {e}. Thử fallback bằng HTTP request.", "WARN")

        try:
            if not domains and is_temp_mail_internal_api():
                base_url = CONFIG["MAIL_API_BASE"].rstrip("/")
                req = urllib.request.Request(f"{base_url}/v4/domains", headers=temp_mail_web_headers(), method="GET")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                if isinstance(data, dict):
                    domains = normalize_domain_names(data.get("domains", []))
                else:
                    domains = normalize_domain_names(data)

            if not domains and ((not is_temp_mail_api() and not is_temp_mail_internal_api()) or str(CONFIG.get("TEMP_MAIL_API_KEY", "")).strip()):
                base_url = CONFIG["MAIL_API_BASE"].rstrip("/")
                url = f"{base_url}/v1/domains" if is_temp_mail_api() else f"{base_url}/api/domains"
                headers = {"User-Agent": "Mozilla/5.0", "X-API-Key": CONFIG.get("TEMP_MAIL_API_KEY", "")}
                req = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                if isinstance(data, list):
                    domains = normalize_domain_names(data)
                elif isinstance(data, dict):
                    domains = normalize_domain_names(data.get("domains", []))
        except Exception as e:
            log(f"Không thể kết nối API lấy domain: {e}", "ERROR")

        if not domains:
            log("Sử dụng danh sách domain mặc định làm fallback.", "WARN")
            domains = normalize_domain_names([CONFIG.get("MAIL_DOMAIN"), "temp-mail.io", "cskh-group.com"])

        AVAILABLE_DOMAINS.clear()
        AVAILABLE_DOMAINS.extend(domains)

        app.after(0, lambda: opt_domain.configure(values=AVAILABLE_DOMAINS))
        if CONFIG["MAIL_DOMAIN"] in AVAILABLE_DOMAINS:
            app.after(0, lambda: opt_domain.set(CONFIG["MAIL_DOMAIN"]))
        else:
            app.after(0, lambda: opt_domain.set(AVAILABLE_DOMAINS[0]))
        log(f"Đã tải {len(domains)} domain.", "SUCCESS")

    threading.Thread(target=load_api_domains, daemon=True).start()

    # Cập nhật GUI mỗi 500ms
    def update_gui():
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
            widgets["step"].configure(text=trim(f"[{worker.get('step_num', 0)}/8] {worker.get('step', '-')}", 32))
            widgets["status"].configure(text=status.upper(), text_color=status_color)

        with lock:
            if last_log_index[0] > len(ui_log_lines):
                last_log_index[0] = max(0, len(ui_log_lines) - 200)
            new_logs = ui_log_lines[last_log_index[0]:]
            last_log_index[0] = len(ui_log_lines)

        for item in new_logs:
            append_log_line(item["line"] if isinstance(item, dict) else str(item))

        app.after(500, update_gui)

    title_colors = [PRIMARY, "#60a5fa", "#93c5fd", "#bfdbfe", "#93c5fd", "#60a5fa"]
    title_color_idx = [0]
    def animate_title():
        try:
            lbl_main_title.configure(text_color=title_colors[title_color_idx[0]])
            title_color_idx[0] = (title_color_idx[0] + 1) % len(title_colors)
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
            pulse_list = status_pulse_colors.get(bot_state, [ERROR])
            color = pulse_list[status_pulse_idx[0] % len(pulse_list)]
            lbl_system_status.configure(text_color=color)
            status_pulse_idx[0] += 1
            app.after(300, animate_status)
        except Exception: pass

    animate_title()
    animate_status()
    update_gui()
    app.mainloop()


# ============ MAIN ============
if __name__ == "__main__":
    print("=" * 50)
    print("   OPENART AUTO REG + CLAIM 20K CREDITS")
    print("   Async Worker Pool | Playwright Camoufox | GUI")
    print("=" * 50)
    start_gui()
