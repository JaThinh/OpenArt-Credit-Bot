"""Offline project health check for local setup and imports.

This script does not open browsers, call external APIs, or run account flows.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import pathlib
import py_compile
import sys


ROOT = pathlib.Path(__file__).resolve().parent
OUTLOOK_DIR = ROOT / "OutlookRegister"

PACKAGE_IMPORTS = {
    "camoufox": "camoufox",
    "customtkinter": "customtkinter",
    "Faker": "faker",
    "httpx": "httpx",
    "playwright": "playwright",
    "requests": "requests",
    "temp-mail": "tempmail",
}

ROOT_CONFIG_KEYS = {
    "MAIL_API_BASE",
    "MAIL_DOMAIN",
    "SIGNUP_URL",
    "CREDIT_URL",
    "PASSWORD",
    "LOOP_COUNT",
    "CONCURRENCY",
    "OTP_POLL_INTERVAL",
    "OTP_MAX_ATTEMPTS",
    "HEADLESS",
    "PROXIES",
}

OUTLOOK_CONFIG_KEYS = {
    "choose_browser",
    "email_suffix",
    "bot_protection_wait",
    "max_captcha_retries",
    "concurrent_flows",
    "max_tasks",
    "oauth2",
    "playwright",
}


def ok(message: str) -> None:
    print(f"[OK] {message}")


def warn(message: str) -> None:
    print(f"[WARN] {message}")


def fail(message: str) -> None:
    print(f"[FAIL] {message}")


def load_json(path: pathlib.Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise TypeError(f"{path.name} must contain a JSON object")
    return data


def check_compile() -> bool:
    ignored = {".git", ".venv", "venv", "env", "__pycache__"}
    success = True
    for path in ROOT.rglob("*.py"):
        if ignored.intersection(path.parts):
            continue
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            fail(f"compile failed for {path.relative_to(ROOT)}: {exc.msg}")
            success = False
    return success


def check_packages() -> bool:
    success = True
    for display_name, import_name in PACKAGE_IMPORTS.items():
        if importlib.util.find_spec(import_name):
            ok(f"package available: {display_name}")
        else:
            fail(f"missing package: {display_name}")
            success = False
    return success


def check_configs() -> tuple[bool, dict, dict]:
    success = True
    root_config = load_json(ROOT / "config.json")
    outlook_config = load_json(OUTLOOK_DIR / "config.json")

    missing_root = sorted(ROOT_CONFIG_KEYS - set(root_config))
    missing_outlook = sorted(OUTLOOK_CONFIG_KEYS - set(outlook_config))
    if missing_root:
        fail(f"config.json missing keys: {', '.join(missing_root)}")
        success = False
    else:
        ok("config.json keys look complete")

    if missing_outlook:
        fail(f"OutlookRegister/config.json missing keys: {', '.join(missing_outlook)}")
        success = False
    else:
        ok("OutlookRegister/config.json keys look complete")

    proxies = root_config.get("PROXIES", [])
    if proxies and not isinstance(proxies, list):
        fail("config.json PROXIES must be a list")
        success = False
    else:
        ok(f"proxy list parsed ({len(proxies) if isinstance(proxies, list) else 0} entries)")

    mail_base = str(root_config.get("MAIL_API_BASE", "")).lower()
    if "api.temp-mail.io" in mail_base and "api.internal.temp-mail.io" not in mail_base and not str(root_config.get("TEMP_MAIL_API_KEY", "")).strip():
        warn("TEMP_MAIL_API_KEY is empty; OpenArt mail API calls will use local fallback")

    return success, root_config, outlook_config


def check_project_imports() -> bool:
    success = True
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(OUTLOOK_DIR))

    for module_name in (
        "bot",
        "vpn_manager",
        "network_service",
        "playwright_input_utils",
        "playwright_fault_tolerance",
        "playwright_session_manager",
        "bot_outlook",
    ):
        try:
            importlib.import_module(module_name)
            ok(f"module importable: {module_name}")
        except Exception as exc:
            fail(f"cannot import {module_name}: {type(exc).__name__}: {exc}")
            success = False

    return success


def check_optional(outlook_config: dict) -> bool:
    browser = str(outlook_config.get("choose_browser", "playwright")).lower()
    if browser == "patchright":
        if importlib.util.find_spec("patchright"):
            ok("optional patchright package available")
        else:
            warn("patchright is not installed; Outlook CLI will fallback to playwright")
        return True

    if not importlib.util.find_spec("patchright"):
        warn("patchright is not installed; this is fine while choose_browser=playwright")
    return True


def main() -> int:
    print("== OpenArt-Credit-Bot health check ==")
    success = True

    if check_compile():
        ok("Python files compile")
    else:
        fail("Python compile check failed")
        success = False

    success = check_packages() and success

    try:
        config_ok, _root_config, outlook_config = check_configs()
        success = config_ok and success
    except Exception as exc:
        fail(f"config check failed: {type(exc).__name__}: {exc}")
        outlook_config = {}
        success = False

    success = check_project_imports() and success
    success = check_optional(outlook_config) and success

    if success:
        ok("health check passed")
        return 0

    fail("health check found errors")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
