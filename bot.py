import asyncio
import json
import os
import random
import re
import string
import threading
import time
import urllib.request
from datetime import datetime
from urllib.parse import unquote, urlparse

# ============ CẤU HÌNH MẶC ĐỊNH ============
CONFIG = {
    "MAIL_API_BASE": "https://mail.cskh-group.com",
    "MAIL_DOMAIN": "cskh-group.com",
    "SIGNUP_URL": "https://openart.ai/signup?callbackUrl=%2Fcredit%2FYT+Affiliate",
    "CREDIT_URL": "https://openart.ai/credit/YT%20Affiliate",
    "PASSWORD": "ShadyPro123!@#",
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
    "LOG_FILE": "success_accounts.txt",
}

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    global CONFIG
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                CONFIG.update(loaded)
        except Exception as e:
            print(f"Lỗi đọc config.json: {e}")

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

    print(console_line)
    with lock:
        ui_log_lines.append({"line": ui_line, "type": msg_type})
        if len(ui_log_lines) > MAX_UI_LOG_LINES:
            del ui_log_lines[:-MAX_UI_LOG_LINES]

def save_account_sync(email, password, status):
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

def get_worker_proxy(worker_id):
    proxies = CONFIG.get("PROXIES") or []
    if not proxies:
        return ""
    return proxies[(worker_id - 1) % len(proxies)]

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
def create_email():
    username = generate_username()
    return {"email": f"{username}@{CONFIG['MAIL_DOMAIN']}"}

def check_inbox(email_address):
    try:
        url = f"{CONFIG['MAIL_API_BASE']}/api/inbox/{email_address}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return data.get("messages", data.get("emails", []))
    except Exception:
        return []

async def wait_for_otp(email_address, worker_id=0):
    """Poll inbox bất đồng bộ không gây block event loop."""
    for attempt in range(1, CONFIG["OTP_MAX_ATTEMPTS"] + 1):
        if should_stop:
            return None
        await asyncio.sleep(CONFIG["OTP_POLL_INTERVAL"])
        messages = await asyncio.to_thread(check_inbox, email_address)
        if messages:
            content = json.dumps(messages)
            match = re.search(r"\b\d{6}\b", content)
            if match:
                otp_code = match.group()
                log(f"Đã nhận OTP: {otp_code} (Lần thử {attempt})", "SUCCESS", worker_id)
                return otp_code
    return None

# ============ REGISTER ENGINE (Single account flow) ============
async def register_one(worker_id, account_index, assigned_proxy):
    global stats
    from camoufox.async_api import AsyncCamoufox

    email_data = create_email()
    email = email_data["email"]
    registered = False
    claimed = False
    start_time = time.time()

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

        # STEP 1: Vào trang đăng ký
        ws(1, "Vào trang signup...")
        await page.goto(CONFIG["SIGNUP_URL"], wait_until="domcontentloaded", timeout=45000)

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

        await email_input.wait_for(state="visible", timeout=15000)
        await email_input.click()
        await asyncio.sleep(0.1)
        await email_input.fill(email)

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
                await inp.wait_for(state="visible", timeout=2000)
                await inp.click()
                await asyncio.sleep(0.05)
                await inp.fill(CONFIG["PASSWORD"])
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
                    await otp_inputs[i].fill(otp[i])
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
                    await all_inputs.nth(idx).fill(otp)
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
                await page.goto(CONFIG["CREDIT_URL"], wait_until="domcontentloaded", timeout=30000)

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

    # Chuẩn bị khởi chạy trình duyệt Camoufox
    launch_args = {"headless": CONFIG["HEADLESS"]}
    
    # Thiết lập Proxy đúng định dạng Playwright cấu hình an toàn
    proxy_config = parse_proxy_for_camoufox(assigned_proxy)
    if proxy_config:
        launch_args["proxy"] = proxy_config
    if CONFIG["FIREFOX_PATH"] and os.path.exists(CONFIG["FIREFOX_PATH"]):
        launch_args["executable_path"] = CONFIG["FIREFOX_PATH"]

    try:
        async with AsyncCamoufox(**launch_args) as browser:
            claimed = await automate(browser)
    except TypeError as exc:
        # Cơ chế fallback nếu có lỗi kiểu dữ liệu khi truyền proxy/options
        log(f"Camoufox lỗi cấu hình ({exc}). Khởi chạy không proxy/headless mặc định.", "WARN", worker_id)
        fallback_args = {"headless": CONFIG["HEADLESS"]}
        if CONFIG["FIREFOX_PATH"] and os.path.exists(CONFIG["FIREFOX_PATH"]):
            fallback_args["executable_path"] = CONFIG["FIREFOX_PATH"]
        try:
            async with AsyncCamoufox(**fallback_args) as browser:
                claimed = await automate(browser)
        except Exception as e:
            log(f"Trình duyệt lỗi nghiêm trọng: {e}", "ERROR", worker_id)
    except Exception as e:
        log(f"Trình duyệt lỗi: {e}", "ERROR", worker_id)

    # Tổng kết trạng thái và ghi log
    status_str = "REG+CLAIM_OK" if claimed else ("REG_OK" if registered else "FAIL")
    await save_account(email, CONFIG["PASSWORD"], status_str)
    
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
        # Đảm bảo mỗi worker lấy đúng proxy được phân bổ
        assigned_proxy = get_worker_proxy(worker_id)
        
        log(f"Nhận tài khoản thứ #{account_idx} (Proxy: {short_proxy(assigned_proxy)})", "INFO", worker_id)
        
        # Thực thi quy trình đăng ký
        await register_one(worker_id, account_idx, assigned_proxy)
        
        queue.task_done()
        
        # Delay giữa các lần đăng ký
        if CONFIG["DELAY_BETWEEN_ACCOUNTS"] > 0 and not should_stop:
            await asyncio.sleep(CONFIG["DELAY_BETWEEN_ACCOUNTS"])
            
    # Đặt trạng thái worker slot về IDLE khi kết thúc
    with lock:
        w = workers[worker_id - 1]
        w["status"] = "idle"
        w["step"] = "Hoàn thành / Idle"
    log(f"Worker {worker_id:02d} dừng hoạt động.", "INFO", worker_id)


async def queue_producer(queue):
    """Task nền sản sinh công việc không giới hạn khi total_accounts = 0."""
    idx = 1
    while not should_stop:
        if queue.qsize() < 10:
            await queue.put({"index": idx})
            idx += 1
        else:
            await asyncio.sleep(0.2)


async def run_pool(concurrency, total_accounts):
    global should_stop, is_paused, bot_state
    
    # Khởi tạo hàng đợi công việc
    queue = asyncio.Queue()
    producer_task = None
    
    if total_accounts > 0:
        log(f"Khởi tạo hàng đợi với {total_accounts} tài khoản.", "INFO")
        for idx in range(1, total_accounts + 1):
            await queue.put({"index": idx})
    else:
        log("Hệ thống chạy ở chế độ VÔ HẠN tài khoản.", "INFO")
        producer_task = asyncio.create_task(queue_producer(queue))
        
    # Tạo các worker task hoạt động song song
    worker_tasks = []
    for w_id in range(1, concurrency + 1):
        task = asyncio.create_task(worker_loop(w_id, queue, total_accounts))
        worker_tasks.append(task)
        
    # Chờ tất cả worker hoàn thành hoặc hệ thống dừng
    if total_accounts > 0:
        await asyncio.gather(*worker_tasks, return_exceptions=True)
    else:
        # Chạy vô hạn cho tới khi nhấn STOP
        while not should_stop:
            await asyncio.sleep(1.0)
        # Hủy các task đang chờ
        for task in worker_tasks:
            task.cancel()
        if producer_task:
            producer_task.cancel()
            
    bot_state = "OFFLINE"
    log(f"DỪNG: Tổng {stats['total']} | Thành công {stats['success']} | Lỗi {stats['fail']}", "SUCCESS")


def start_bot_thread(concurrency, total_accounts):
    global bot_state, should_stop, is_paused, workers
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

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("green")

    # Màu sắc thiết kế Cyberpunk
    BG = "#000000"
    PANEL = "#030803"
    PANEL_ALT = "#061006"
    GREEN = "#00ff00"
    GREEN_SOFT = "#39ff14"
    GREEN_DARK = "#006600"
    GREEN_DIM = "#003d00"
    BLUE = "#00e5ff"
    RED = "#ff4060"
    RED_DARK = "#260006"
    AMBER = "#ffb000"
    TEXT = "#caffca"
    MUTED = "#69b869"
    MONO = "Consolas"

    def make_frame(parent, fg=PANEL, border=GREEN_DARK, radius=0, **kwargs):
        return ctk.CTkFrame(
            parent,
            fg_color=fg,
            border_color=border,
            border_width=1,
            corner_radius=radius,
            **kwargs,
        )

    def label(parent, text, size=11, color=GREEN, weight="normal", **kwargs):
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
            height=26,
            fg_color=BG,
            border_color=GREEN,
            border_width=1,
            text_color=GREEN_SOFT,
            placeholder_text=placeholder,
            placeholder_text_color=GREEN_DARK,
            font=(MONO, 11),
            justify="center",
            corner_radius=0,
        )

    def checkbox(parent, text, variable):
        return ctk.CTkCheckBox(
            parent,
            text=text,
            variable=variable,
            font=(MONO, 10, "bold"),
            text_color=GREEN_SOFT,
            fg_color=GREEN,
            hover_color=GREEN_DIM,
            border_color=GREEN,
            border_width=1,
            checkmark_color=BG,
            checkbox_width=15,
            checkbox_height=15,
            corner_radius=0,
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
    app.title("OPENART.CREDIT.BOT // Cyberpunk Edition")
    app.geometry("1180x920")
    app.minsize(1040, 760)
    app.configure(fg_color=BG)

    main = make_frame(app, fg=BG, border=GREEN, radius=0)
    main.pack(fill="both", expand=True, padx=12, pady=12)
    main.grid_columnconfigure(0, weight=1)
    main.grid_rowconfigure(4, weight=1)

    # ---- Header Bar ----
    header = make_frame(main, fg=PANEL, border=GREEN, radius=0)
    header.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 8))
    header.grid_columnconfigure(1, weight=1)

    title_group = ctk.CTkFrame(header, fg_color=PANEL, corner_radius=0)
    title_group.grid(row=0, column=0, sticky="w", padx=12, pady=8)
    label(title_group, " █ O P E N A R T . C R E D I T . B O T ░░░", 15, GREEN, "bold").pack(side="left")
    label(title_group, "v3.0 // worker-pool // async", 10, GREEN_DARK, "bold").pack(side="left", padx=(12, 0))

    lbl_system_status = label(header, "[ OFFLINE ]", 13, RED, "bold")
    lbl_system_status.grid(row=0, column=2, sticky="e", padx=14, pady=8)

    # ---- Big Stats Display ----
    stats_frame = make_frame(main, fg=PANEL, border=GREEN_DARK, radius=0)
    stats_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
    stats_frame.grid_columnconfigure((0, 1, 2), weight=1)

    stat_meta = [
        ("total", "// TỔNG CỘNG", BLUE),
        ("success", "// THÀNH CÔNG", GREEN),
        ("fail", "// THẤT BẠI", RED),
    ]
    stat_labels = {}
    for col, (key, caption, color) in enumerate(stat_meta):
        block = ctk.CTkFrame(stats_frame, fg_color=PANEL, corner_radius=0)
        block.grid(row=0, column=col, sticky="ew", padx=1)
        block.grid_columnconfigure(0, weight=1)
        value_label = label(block, "000", 42, color, "bold")
        value_label.grid(row=0, column=0, pady=(10, 0))
        label(block, caption, 11, MUTED, "bold").grid(row=1, column=0, pady=(0, 10))
        stat_labels[key] = value_label

    # ---- Extended Configuration Form ----
    config_frame = make_frame(main, fg=BG, border=GREEN_DARK, radius=0)
    config_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 8))
    config_frame.grid_columnconfigure(0, weight=1)

    row1 = ctk.CTkFrame(config_frame, fg_color=BG, corner_radius=0)
    row1.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
    row1.grid_columnconfigure(5, weight=1)

    label(row1, "[song song]:", 11, GREEN, "bold").grid(row=0, column=0, sticky="w")
    ent_concurrency = entry(row1, 58)
    ent_concurrency.grid(row=0, column=1, padx=(5, 14))
    ent_concurrency.insert(0, str(CONFIG["CONCURRENCY"]))

    label(row1, "[tổng acc]:", 11, GREEN, "bold").grid(row=0, column=2, sticky="w")
    ent_total_accounts = entry(row1, 70)
    ent_total_accounts.grid(row=0, column=3, padx=(5, 14))
    ent_total_accounts.insert(0, "20")

    label(row1, "[domain]:", 11, GREEN, "bold").grid(row=0, column=4, sticky="w")
    ent_domain = entry(row1, 170)
    ent_domain.grid(row=0, column=5, padx=(5, 14), sticky="w")
    ent_domain.insert(0, CONFIG["MAIL_DOMAIN"])

    label(row1, "[delay]:", 11, GREEN, "bold").grid(row=0, column=6, sticky="w")
    ent_delay = entry(row1, 65)
    ent_delay.grid(row=0, column=7, padx=(5, 14))
    ent_delay.insert(0, str(CONFIG["DELAY_BETWEEN_ACCOUNTS"]))

    chk_headless_var = ctk.BooleanVar(value=CONFIG["HEADLESS"])
    checkbox(row1, "[ẩn hoàn toàn]", chk_headless_var).grid(row=0, column=8, padx=(2, 10), sticky="w")

    chk_minimize_var = ctk.BooleanVar(value=CONFIG["MINIMIZE_TASKBAR"])
    checkbox(row1, "[thu nhỏ taskbar]", chk_minimize_var).grid(row=0, column=9, sticky="w")

    row2 = ctk.CTkFrame(config_frame, fg_color=BG, corner_radius=0)
    row2.grid(row=1, column=0, sticky="ew", padx=10, pady=4)
    row2.grid_columnconfigure(1, weight=1)

    label(row2, "[2captcha]:", 11, GREEN, "bold", width=118, anchor="w").grid(row=0, column=0, sticky="w")
    ent_captcha = entry(row2, placeholder="để trống = manual mode")
    ent_captcha.configure(justify="left")
    ent_captcha.grid(row=0, column=1, sticky="ew", padx=(4, 0))
    ent_captcha.insert(0, CONFIG.get("CAPTCHA_API_KEY", ""))

    row3 = ctk.CTkFrame(config_frame, fg_color=BG, corner_radius=0)
    row3.grid(row=2, column=0, sticky="ew", padx=10, pady=4)
    label(row3, "[proxy list]:", 11, GREEN, "bold", width=118, anchor="w").pack(side="left")
    label(row3, "// mỗi dòng 1 proxy -> phân bổ worker theo dạng round-robin", 10, GREEN_DARK, "bold").pack(side="left", padx=(4, 0))

    txt_proxy = ctk.CTkTextbox(
        config_frame,
        height=72,
        fg_color=BG,
        border_color=GREEN_DARK,
        border_width=1,
        text_color=GREEN_SOFT,
        font=(MONO, 10),
        corner_radius=0,
        wrap="none",
        scrollbar_button_color=GREEN_DIM,
        scrollbar_button_hover_color=GREEN,
    )
    txt_proxy.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 4))
    
    # Nạp proxy từ CONFIG đã tải
    if CONFIG.get("PROXIES"):
        txt_proxy.insert(tk.END, "\n".join(CONFIG["PROXIES"]))

    row4 = ctk.CTkFrame(config_frame, fg_color=BG, corner_radius=0)
    row4.grid(row=4, column=0, sticky="ew", padx=10, pady=(4, 10))
    row4.grid_columnconfigure(1, weight=1)

    label(row4, "[firefox path]:", 11, GREEN, "bold", width=118, anchor="w").grid(row=0, column=0, sticky="w")
    ent_firefox = entry(row4)
    ent_firefox.configure(justify="left")
    ent_firefox.grid(row=0, column=1, sticky="ew", padx=(4, 6))
    ent_firefox.insert(0, CONFIG.get("FIREFOX_PATH", ""))

    def choose_firefox_path():
        path = filedialog.askopenfilename(
            title="Chọn Firefox executable",
            filetypes=[("Firefox executable", "*.exe"), ("All files", "*.*")],
        )
        if path:
            ent_firefox.delete(0, tk.END)
            ent_firefox.insert(0, path)

    ctk.CTkButton(
        row4,
        text="[...]",
        width=54,
        height=26,
        fg_color=BG,
        hover_color=GREEN_DIM,
        border_color=GREEN,
        border_width=1,
        text_color=GREEN,
        font=(MONO, 10, "bold"),
        corner_radius=0,
        command=choose_firefox_path,
    ).grid(row=0, column=2, sticky="e")

    # ---- Action Buttons ----
    actions = ctk.CTkFrame(main, fg_color=BG, corner_radius=0)
    actions.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 8))
    actions.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)

    def refresh_config_from_gui():
        CONFIG["CONCURRENCY"] = parse_int(ent_concurrency.get(), CONFIG["CONCURRENCY"], 1)
        CONFIG["MAIL_DOMAIN"] = ent_domain.get().strip() or CONFIG["MAIL_DOMAIN"]
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
        log(
            f"RUN concurrency={CONFIG['CONCURRENCY']} total={total_accounts} domain={CONFIG['MAIL_DOMAIN']} "
            f"delay={CONFIG['DELAY_BETWEEN_ACCOUNTS']}s proxies={len(CONFIG['PROXIES'])}",
            "INFO",
        )
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
                with open(filepath, "a", encoding="utf-8"):
                    pass
            except Exception:
                pass
        try:
            os.startfile(filepath)
        except Exception as exc:
            log(f"Không mở được file accounts: {exc}", "ERROR")

    button_style = {"height": 34, "corner_radius": 0, "font": (MONO, 12, "bold")}
    ctk.CTkButton(
        actions,
        text="▶ RUN",
        fg_color=GREEN,
        hover_color="#00cc00",
        text_color=BG,
        command=action_run,
        **button_style,
    ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
    
    ctk.CTkButton(
        actions,
        text="▐▐ PAUSE",
        fg_color=BG,
        hover_color="#241800",
        border_color=AMBER,
        border_width=1,
        text_color=AMBER,
        command=action_pause,
        **button_style,
    ).grid(row=0, column=1, sticky="ew", padx=4)
    
    ctk.CTkButton(
        actions,
        text="■ STOP",
        fg_color=RED_DARK,
        hover_color="#4a0010",
        border_color=RED,
        border_width=1,
        text_color=RED,
        command=action_stop,
        **button_style,
    ).grid(row=0, column=2, sticky="ew", padx=4)
    
    ctk.CTkButton(
        actions,
        text="🔧 FIX TOKEN",
        fg_color=BG,
        hover_color="#241800",
        border_color=AMBER,
        border_width=1,
        text_color=AMBER,
        command=action_fix_token,
        **button_style,
    ).grid(row=0, column=3, sticky="ew", padx=4)
    
    ctk.CTkButton(
        actions,
        text="📂 ACCOUNTS",
        fg_color=BG,
        hover_color="#001a24",
        border_color=BLUE,
        border_width=1,
        text_color=BLUE,
        command=action_accounts,
        **button_style,
    ).grid(row=0, column=4, sticky="ew", padx=(4, 0))

    # ---- Live Workers Dashboard ----
    live_panel = make_frame(main, fg=BG, border=GREEN_DARK, radius=0)
    live_panel.grid(row=4, column=0, sticky="nsew", padx=10, pady=(0, 8))
    live_panel.grid_columnconfigure(0, weight=1)
    live_panel.grid_rowconfigure(2, weight=1)

    label(live_panel, "// LIVE WORKERS DASHBOARD — Giám sát luồng song song", 11, GREEN_DARK, "bold").grid(
        row=0, column=0, sticky="w", padx=10, pady=(8, 4)
    )

    header_row = ctk.CTkFrame(live_panel, fg_color=PANEL, corner_radius=0)
    header_row.grid(row=1, column=0, sticky="ew", padx=10)
    live_columns = [
        ("WORKER", 85),
        ("TARGET EMAIL", 230),
        ("SUB-STEP PROCESS", 260),
        ("STATUS", 100),
        ("SUCCESS", 70),
        ("FAIL", 60),
        ("ELAPSED", 80),
        ("PROXY IP", 170),
    ]
    for index, (text, width) in enumerate(live_columns):
        label(header_row, text, 10, GREEN_DARK, "bold", width=width, anchor="w").grid(
            row=0, column=index, sticky="w", padx=4, pady=5
        )

    live_scroll = ctk.CTkScrollableFrame(
        live_panel,
        fg_color=BG,
        border_color=GREEN_DARK,
        border_width=1,
        corner_radius=0,
        scrollbar_button_color=GREEN_DIM,
        scrollbar_button_hover_color=GREEN,
    )
    live_scroll.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))

    live_rows = []

    def row_label(parent, text="-", width=80, color=TEXT, weight="normal"):
        return ctk.CTkLabel(
            parent,
            text=text,
            width=width,
            height=26,
            anchor="w",
            font=(MONO, 10, weight),
            text_color=color,
        )

    def ensure_live_rows():
        # Xóa các dòng giao diện cũ nếu số worker cấu hình thay đổi
        if len(live_rows) != len(workers):
            for row_widgets in live_rows:
                for widget in row_widgets.values():
                    widget.destroy()
            live_rows.clear()

        while len(live_rows) < len(workers):
            idx = len(live_rows)
            row = ctk.CTkFrame(
                live_scroll,
                fg_color=BG if idx % 2 == 0 else PANEL_ALT,
                corner_radius=0,
                height=28,
            )
            row.pack(fill="x", pady=(0, 1))

            widgets = {}
            for col, (key, width) in enumerate([
                ("worker", 85),
                ("email", 230),
                ("step", 260),
                ("status", 100),
                ("ok", 70),
                ("fail", 60),
                ("time", 80),
                ("proxy", 170),
            ]):
                color = BLUE if key == "worker" else TEXT
                weight = "bold" if key in ("worker", "status") else "normal"
                widgets[key] = row_label(row, "-", width, color, weight)
                widgets[key].grid(row=0, column=col, padx=4, pady=1, sticky="w")
            live_rows.append(widgets)

    # ---- Terminal Debug Log ----
    log_panel = make_frame(main, fg=BG, border=GREEN_DARK, radius=0)
    log_panel.grid(row=5, column=0, sticky="ew", padx=10, pady=(0, 10))
    log_panel.grid_columnconfigure(0, weight=1)

    log_title = ctk.CTkFrame(log_panel, fg_color=BG, corner_radius=0)
    log_title.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
    log_title.grid_columnconfigure(0, weight=1)
    label(log_title, "// NHẬT KÝ CHẠY HỆ THỐNG", 11, GREEN_DARK, "bold").grid(row=0, column=0, sticky="w")

    txt_log = ctk.CTkTextbox(
        log_panel,
        height=132,
        fg_color=BG,
        border_color=GREEN_DARK,
        border_width=1,
        text_color=GREEN_SOFT,
        font=(MONO, 10),
        corner_radius=0,
        wrap="word",
        scrollbar_button_color=GREEN_DIM,
        scrollbar_button_hover_color=GREEN,
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
        log_title,
        text="[clear]",
        width=72,
        height=22,
        fg_color=BG,
        hover_color=GREEN_DIM,
        border_color=GREEN_DARK,
        border_width=1,
        text_color=GREEN_SOFT,
        font=(MONO, 9, "bold"),
        corner_radius=0,
        command=clear_log,
    ).grid(row=0, column=1, sticky="e")

    append_log_line(f"[{datetime.now().strftime('%H:%M:%S')}] > hệ thống sẵn sàng. nhấn RUN để khởi chạy.")

    # Cập nhật GUI mỗi 500ms thay vì 600ms
    def update_gui():
        stat_labels["total"].configure(text=f"{stats['total']:03d}")
        stat_labels["success"].configure(text=f"{stats['success']:03d}")
        stat_labels["fail"].configure(text=f"{stats['fail']:03d}")

        status_color = RED
        if bot_state == "RUNNING":
            status_color = GREEN
        elif bot_state == "PAUSED":
            status_color = AMBER
        lbl_system_status.configure(text=f"[ {bot_state} ]", text_color=status_color)

        ensure_live_rows()
        for idx, worker in enumerate(workers):
            if idx >= len(live_rows):
                continue

            status = worker.get("status", "idle")
            if status == "running":
                status_color = BLUE
                elapsed = int(time.time() - worker.get("start_time", time.time()))
            elif status == "done":
                status_color = GREEN
                elapsed = int(worker.get("elapsed", 0))
            elif status == "fail":
                status_color = RED
                elapsed = int(worker.get("elapsed", 0))
            else:
                status_color = MUTED
                elapsed = int(worker.get("elapsed", 0))

            widgets = live_rows[idx]
            widgets["worker"].configure(text=f"W{worker.get('id', idx + 1):02d}", text_color=status_color)
            widgets["email"].configure(text=trim(worker.get("email", "-"), 31))
            widgets["step"].configure(text=trim(f"[{worker.get('step_num', 0)}/8] {worker.get('step', '-')}", 36))
            widgets["status"].configure(text=status.upper(), text_color=status_color)
            widgets["ok"].configure(text=str(worker.get("ok", 0)), text_color=GREEN)
            widgets["fail"].configure(text=str(worker.get("fail", 0)), text_color=RED)
            widgets["time"].configure(text=f"{elapsed}s", text_color=MUTED)
            widgets["proxy"].configure(text=trim(worker.get("proxy", "DIRECT"), 24), text_color=MUTED)

        with lock:
            if last_log_index[0] > len(ui_log_lines):
                last_log_index[0] = max(0, len(ui_log_lines) - 200)
            new_logs = ui_log_lines[last_log_index[0]:]
            last_log_index[0] = len(ui_log_lines)
            
        for item in new_logs:
            append_log_line(item["line"] if isinstance(item, dict) else str(item))

        app.after(500, update_gui)

    update_gui()
    app.mainloop()


# ============ MAIN ============
if __name__ == "__main__":
    print("=" * 50)
    print("   OPENART AUTO REG + CLAIM 20K CREDITS")
    print("   Async Worker Pool | Playwright Camoufox | GUI")
    print("=" * 50)
    start_gui()
