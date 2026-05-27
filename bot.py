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


# ============ CẤU HÌNH ============
CONFIG = {
    "MAIL_API_BASE": "https://mail.cskh-group.com",
    "MAIL_DOMAIN": "cskh-group.com",
    "SIGNUP_URL": "https://openart.ai/signup?callbackUrl=%2Fcredit%2FYT+Affiliate",
    "CREDIT_URL": "https://openart.ai/credit/YT%20Affiliate",
    "PASSWORD": "ShadyPro123!@#",
    "LOOP_COUNT": 0,
    "CONCURRENCY": 1,
    "DELAY_BETWEEN_ACCOUNTS": 3,
    "OTP_POLL_INTERVAL": 2,
    "OTP_MAX_ATTEMPTS": 30,
    "HEADLESS": True,
    "MINIMIZE_TASKBAR": False,
    "CAPTCHA_API_KEY": "",
    "FIREFOX_PATH": "",
    "PROXIES": [],
    "LOG_FILE": "success_accounts.txt",
}


# ============ STATE ============
bot_state = "OFFLINE"
stats = {"total": 0, "success": 0, "fail": 0}
workers = []
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
    tag = f"W{worker_id}" if worker_id > 0 else "BOT"
    console_line = f"[{timestamp}] {prefix} [{tag}] {msg}"
    ui_line = f"[{timestamp}] > {prefix} [{tag}] {msg}"

    print(console_line)
    with lock:
        ui_log_lines.append({"line": ui_line, "type": msg_type})
        if len(ui_log_lines) > MAX_UI_LOG_LINES:
            del ui_log_lines[:-MAX_UI_LOG_LINES]


def save_account(email, password, status):
    line = f"{datetime.now().isoformat()} | {email} | {password} | {status}\n"
    filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), SESSION_LOG_FILE)
    with lock:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(line)


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
    return proxy_value if len(proxy_value) <= limit else proxy_value[: limit - 3] + "..."


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


def wait_for_otp(email_address, worker_id=0):
    """Poll inbox liên tục mỗi 1s để bắt OTP nhanh nhất."""
    for _ in range(CONFIG["OTP_MAX_ATTEMPTS"]):
        if should_stop:
            return None
        time.sleep(1)
        messages = check_inbox(email_address)
        if messages:
            content = json.dumps(messages)
            match = re.search(r"\b\d{6}\b", content)
            if match:
                return match.group()
    return None


# ============ WORKER (1 acc = 1 worker) ============
async def register_one(worker_id):
    global stats
    from camoufox.async_api import AsyncCamoufox

    email_data = create_email()
    email = email_data["email"]
    registered = False
    claimed = False
    assigned_proxy = get_worker_proxy(worker_id)
    start_time = time.time()

    w = {
        "id": worker_id,
        "email": email,
        "step": "Starting",
        "step_num": 0,
        "status": "running",
        "ok": 0,
        "fail": 0,
        "start_time": start_time,
        "elapsed": 0,
        "proxy": short_proxy(assigned_proxy),
    }
    with lock:
        if worker_id <= len(workers):
            workers[worker_id - 1] = w
        else:
            workers.append(w)

    def ws(num, msg):
        w["step"] = msg
        w["step_num"] = num
        w["elapsed"] = int(time.time() - start_time)
        log(msg, "STEP", worker_id)

    async def automate(browser):
        nonlocal registered, claimed
        page = await browser.new_page()

        # STEP 1: Vào thẳng trang signup với callback claim.
        ws(1, "Vào trang signup...")
        await page.goto(CONFIG["SIGNUP_URL"], wait_until="domcontentloaded", timeout=30000)

        # STEP 2: Điền email.
        ws(2, "Điền email...")
        email_input = page.locator('input[type="email"]')
        await email_input.first.wait_for(state="visible", timeout=15000)
        await email_input.first.click()
        await asyncio.sleep(0.05)
        await email_input.first.fill(email)

        # STEP 3: Điền password.
        ws(3, "Điền password...")
        pw_inputs = page.locator('input[type="password"]')
        pw_count = await pw_inputs.count()
        for i in range(pw_count):
            await pw_inputs.nth(i).click()
            await asyncio.sleep(0.05)
            await pw_inputs.nth(i).fill(CONFIG["PASSWORD"])
        await asyncio.sleep(0.2)

        # STEP 4: Click Sign Up.
        ws(4, "Click Sign Up...")
        for btn_text in ["Sign Up", "Continue", "Register"]:
            btn = page.locator(f'button:has-text("{btn_text}")').first
            try:
                if await btn.is_visible(timeout=1000):
                    if not await btn.is_disabled():
                        await btn.click()
                        break
            except Exception:
                continue

        # STEP 5: Lấy OTP.
        ws(5, "Chờ OTP...")
        otp = wait_for_otp(email, worker_id)
        if not otp:
            ws(5, "Không nhận OTP!")
            w["status"] = "fail"
            w["fail"] = 1
            return False

        # STEP 6: Điền OTP.
        ws(6, f"Điền OTP {otp}...")

        for _ in range(10):
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
                    await asyncio.sleep(0.03)
                break

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
                    break
            else:
                await asyncio.sleep(0.5)
                continue
            break

        # STEP 7: Click Verify.
        ws(7, "Click Verify...")
        for btn_text in ["Verify", "Create Account", "Continue", "Submit"]:
            btn = page.locator(f'button:has-text("{btn_text}")').first
            try:
                if await btn.is_visible(timeout=1000):
                    if not await btn.is_disabled():
                        await btn.click()
                        break
            except Exception:
                continue

        ws(7, "Đợi xác nhận...")
        for _ in range(30):
            await asyncio.sleep(0.5)
            body_text = await page.inner_text("body")
            if "Verification Code" not in body_text and "Verify Email" not in body_text:
                break

        body_text = await page.inner_text("body")
        if "Verification Code" not in body_text and "Verify Email" not in body_text:
            registered = True
            log(f"REG OK: {email}", "SUCCESS", worker_id)

        # STEP 8: Claim credits.
        if registered:
            ws(8, "Claim credits...")
            current_url = page.url
            if "credit" not in current_url:
                await page.goto(CONFIG["CREDIT_URL"], wait_until="domcontentloaded", timeout=20000)

            claim_btn = page.locator('button:has-text("Claim Credits")').first
            try:
                await claim_btn.wait_for(state="visible", timeout=15000)
                await claim_btn.click()
                claimed = True
                log(f"CLAIM OK: {email}", "SUCCESS", worker_id)
            except Exception:
                try:
                    span_btn = page.locator('span:has-text("Claim Credits")').first
                    await span_btn.wait_for(state="visible", timeout=5000)
                    await span_btn.click()
                    claimed = True
                    log(f"CLAIM OK: {email}", "SUCCESS", worker_id)
                except Exception:
                    log("Không tìm thấy nút Claim!", "ERROR", worker_id)

        return claimed

    try:
        launch_args = {"headless": CONFIG["HEADLESS"]}
        proxy_config = parse_proxy_for_camoufox(assigned_proxy)
        if proxy_config:
            launch_args["proxy"] = proxy_config
        if CONFIG["FIREFOX_PATH"] and os.path.exists(CONFIG["FIREFOX_PATH"]):
            launch_args["executable_path"] = CONFIG["FIREFOX_PATH"]

        try:
            async with AsyncCamoufox(**launch_args) as browser:
                await automate(browser)
        except TypeError as exc:
            if len(launch_args) > 1:
                log(f"Camoufox không nhận option mở rộng ({exc}). Thử chạy cấu hình mặc định.", "WARN", worker_id)
                async with AsyncCamoufox(headless=CONFIG["HEADLESS"]) as browser:
                    await automate(browser)
            else:
                raise

    except Exception as e:
        log(f"Lỗi: {e}", "ERROR", worker_id)

    status = "REG+CLAIM_OK" if claimed else ("REG_OK" if registered else "FAIL")
    save_account(email, CONFIG["PASSWORD"], status)
    w["status"] = "done" if claimed else "fail"
    w["step"] = status
    w["elapsed"] = int(time.time() - start_time)
    if claimed:
        w["ok"] = 1
    else:
        w["fail"] = 1

    with lock:
        if claimed:
            stats["success"] += 1
        else:
            stats["fail"] += 1
        stats["total"] += 1

    return claimed


# ============ ASYNC RUNNER ============
async def run_batch(concurrency, total_accounts):
    global should_stop, is_paused, bot_state
    accounts_done = 0

    while not should_stop and (total_accounts == 0 or accounts_done < total_accounts):
        if is_paused:
            await asyncio.sleep(0.5)
            continue

        remaining = total_accounts - accounts_done if total_accounts > 0 else concurrency
        batch_size = min(concurrency, remaining) if total_accounts > 0 else concurrency

        log(f"=== BATCH (acc {accounts_done + 1}-{accounts_done + batch_size}/{total_accounts if total_accounts > 0 else '∞'}) ===", "INFO")

        tasks = []
        for i in range(batch_size):
            wid = i + 1
            if len(workers) < wid:
                workers.append(
                    {
                        "id": wid,
                        "email": "",
                        "step": "Idle",
                        "step_num": 0,
                        "status": "idle",
                        "ok": 0,
                        "fail": 0,
                        "start_time": time.time(),
                        "elapsed": 0,
                        "proxy": short_proxy(get_worker_proxy(wid)),
                    }
                )
            tasks.append(register_one(wid))

        await asyncio.gather(*tasks)
        accounts_done += batch_size

        if total_accounts > 0 and accounts_done >= total_accounts:
            log(f"Đã reg đủ {total_accounts} acc. Dừng.", "SUCCESS")
            break

        if not should_stop:
            await asyncio.sleep(CONFIG["DELAY_BETWEEN_ACCOUNTS"])

    bot_state = "OFFLINE"
    log(f"KẾT QUẢ: Tổng {stats['total']} | OK {stats['success']} | Fail {stats['fail']}", "SUCCESS")


def start_bot_thread(concurrency, total_accounts):
    global bot_state, should_stop, is_paused
    should_stop = False
    is_paused = False
    bot_state = "RUNNING"

    def run():
        asyncio.run(run_batch(concurrency, total_accounts))

    t = threading.Thread(target=run, daemon=True)
    t.start()


# ============ GUI DESKTOP (CustomTkinter Hacker Edition) ============
def start_gui():
    import customtkinter as ctk
    import tkinter as tk
    from tkinter import filedialog

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("green")

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
        return value if len(value) <= limit else value[: max(0, limit - 3)] + "..."

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
    app.title("HOTMAIL.REGISTER // Hacker Edition")
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
    label(title_group, " █ H O T M A I L . R E G I S T E R ░░░", 15, GREEN, "bold").pack(side="left")
    label(title_group, "v2.1 // ruyi-page // BiDi", 10, GREEN_DARK, "bold").pack(side="left", padx=(12, 0))

    lbl_system_status = label(header, "[ OFFLINE ]", 13, RED, "bold")
    lbl_system_status.grid(row=0, column=2, sticky="e", padx=14, pady=8)

    # ---- Big Stats Display ----
    stats_frame = make_frame(main, fg=PANEL, border=GREEN_DARK, radius=0)
    stats_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
    stats_frame.grid_columnconfigure((0, 1, 2), weight=1)

    stat_meta = [
        ("total", "// TỔNG", BLUE),
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

    row3 = ctk.CTkFrame(config_frame, fg_color=BG, corner_radius=0)
    row3.grid(row=2, column=0, sticky="ew", padx=10, pady=4)
    label(row3, "[proxy list]:", 11, GREEN, "bold", width=118, anchor="w").pack(side="left")
    label(row3, "// mỗi dòng 1 proxy -> mỗi worker dùng 1 proxy khác nhau (round-robin)", 10, GREEN_DARK, "bold").pack(side="left", padx=(4, 0))

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

    row4 = ctk.CTkFrame(config_frame, fg_color=BG, corner_radius=0)
    row4.grid(row=4, column=0, sticky="ew", padx=10, pady=(4, 10))
    row4.grid_columnconfigure(1, weight=1)

    label(row4, "[firefox path]:", 11, GREEN, "bold", width=118, anchor="w").grid(row=0, column=0, sticky="w")
    ent_firefox = entry(row4)
    ent_firefox.configure(justify="left")
    ent_firefox.grid(row=0, column=1, sticky="ew", padx=(4, 6))

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
        log("FIX TOKEN command đã được kích hoạt.", "WARN")

    def action_accounts():
        filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), SESSION_LOG_FILE)
        if not os.path.exists(filepath):
            with open(filepath, "a", encoding="utf-8"):
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

    # ---- Live Workers ----
    live_panel = make_frame(main, fg=BG, border=GREEN_DARK, radius=0)
    live_panel.grid(row=4, column=0, sticky="nsew", padx=10, pady=(0, 8))
    live_panel.grid_columnconfigure(0, weight=1)
    live_panel.grid_rowconfigure(2, weight=1)

    label(live_panel, "// LIVE WORKERS — chi tiết từng tab", 11, GREEN_DARK, "bold").grid(
        row=0, column=0, sticky="w", padx=10, pady=(8, 4)
    )

    header_row = ctk.CTkFrame(live_panel, fg_color=PANEL, corner_radius=0)
    header_row.grid(row=1, column=0, sticky="ew", padx=10)
    live_columns = [
        ("WORKER", 75),
        ("EMAIL", 230),
        ("STEP", 260),
        ("STATUS", 100),
        ("OK", 48),
        ("FAIL", 56),
        ("TIME", 72),
        ("PROXY", 170),
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
            for col, (key, width) in enumerate(
                [
                    ("worker", 75),
                    ("email", 230),
                    ("step", 260),
                    ("status", 100),
                    ("ok", 48),
                    ("fail", 56),
                    ("time", 72),
                    ("proxy", 170),
                ]
            ):
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
    label(log_title, "// NHẬT KÝ CHẠY", 11, GREEN_DARK, "bold").grid(row=0, column=0, sticky="w")

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
            widgets["step"].configure(text=trim(f"{worker.get('step_num', 0):02d}/08 :: {worker.get('step', '-')}", 36))
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

        app.after(600, update_gui)

    update_gui()
    app.mainloop()


# ============ MAIN ============
if __name__ == "__main__":
    print("=" * 50)
    print("   OPENART AUTO REG + CLAIM 20K CREDITS")
    print("   Camoufox | Song song | GUI Desktop")
    print("=" * 50)
    start_gui()
