import json
import requests
from playwright.sync_api import sync_playwright
from .base_controller import BaseBrowserController
from .base_controller import OUTLOOK_NAVIGATION_TIMEOUT_MS
from .base_controller import build_browser_launch_args
try:
    from utils import CONFIG_PATH, get_random_user_agent
except ImportError:
    from ..utils import CONFIG_PATH, get_random_user_agent


class PlaywrightController(BaseBrowserController):

    def __init__(self):
        super().__init__()
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.browser_path = data["playwright"]["browser_path"]
        # Optional anti-detect config for AdsPower / Gologin integration. If enabled, the bot will connect to an already-running profile browser instance instead of launching a new one.
        self.antidetect_config = data.get("playwright", {}).get("antidetect", {})

    def launch_browser(self):
        try:
            import asyncio
            try:
                # Dập tắt loop đang tồn tại trên thread này để sync_playwright hoạt động an toàn
                asyncio.set_event_loop(None)
            except Exception:
                pass
            # If anti-detect is enabled in config, use the external profile connection
            if isinstance(self.antidetect_config, dict) and self.antidetect_config.get("enabled"):
                profile_id = self.antidetect_config.get("profile_id") or self.antidetect_config.get("user_id")
                provider = (self.antidetect_config.get("provider") or "adspower").lower()
                try:
                    return self.launch_browser_via_antidetect(profile_id=profile_id, provider=provider)
                except Exception as e:
                    print(f"[Antidetect] Failed to launch via anti-detect provider: {e}")
                    # fallback to normal playwright launch
            p = sync_playwright().start()

            # Phân tích định dạng proxy động (ip:port:username:password hoặc http://...)
            launch_args = build_browser_launch_args(self.proxy)
            launch_options = {
                "executable_path": self.browser_path if self.browser_path else None,
                "headless": self.headless,
            }
            launch_options.update(launch_args)

            # Tự động phát hiện loại trình duyệt dựa trên đường dẫn
            browser_path_lower = (self.browser_path or "").lower()
            if "chrome" in browser_path_lower or "google" in browser_path_lower:
                print("[Browser] Khoi chay bang Google Chrome...")
                b = p.chromium.launch(**launch_options)
            else:
                print("[Browser] Khoi chay bang Mozilla Firefox...")
                b = p.firefox.launch(**launch_options)

            return p, b

        except Exception as e:
            print(f"Failed to launch browser: {e}")
            return False, False

    def launch_browser_via_antidetect(self, profile_id=None, provider="adspower"):
        """
        Kết nối Playwright điều khiển môi trường trình duyệt sạch (Anti-detect Context)
        provider: 'adspower' or 'gologin' (only adspower implemented by default)
        profile_id: profile identifier string for the anti-detect tool
        Trả về (playwright_instance, browser_instance)
        """
        import time
        from playwright.sync_api import sync_playwright

        provider = (provider or "adspower").lower()

        if provider == "adspower":
            api_base = self.antidetect_config.get("local_api") or "http://local.adspower.net:50325"
            api_key = self.antidetect_config.get("api_key")
            api_key_header = self.antidetect_config.get("api_key_header") or "Authorization"
            if not profile_id:
                raise RuntimeError("Missing profile_id for AdsPower connection")

            header_candidates = []
            if api_key:
                header_candidates.append(api_key_header)
                for candidate in ["Authorization", "x-api-key", "api_key", "api-key"]:
                    if candidate not in header_candidates:
                        header_candidates.append(candidate)
            else:
                header_candidates.append(None)

            start_urls = [
                f"{api_base.rstrip('/')}/api/v1/browser/start?user_id={profile_id}",
                f"{api_base.rstrip('/')}/api/v1/browser/start?id={profile_id}",
                f"{api_base.rstrip('/')}/api/v1/profile/start?user_id={profile_id}",
                f"{api_base.rstrip('/')}/api/v1/profile/start?id={profile_id}",
                f"{api_base.rstrip('/')}/api/v2/browser/start?user_id={profile_id}",
                f"{api_base.rstrip('/')}/api/v2/profile/start?user_id={profile_id}",
                f"{api_base.rstrip('/')}/api/v2/profile/start?id={profile_id}",
            ]

            data = None
            used_url = None
            used_header = None
            last_error = None
            for adspower_api_url in start_urls:
                for header_name in header_candidates:
                    headers = {}
                    if api_key and header_name:
                        auth_value = api_key
                        if header_name.lower() == "authorization" and not auth_value.lower().startswith("bearer "):
                            auth_value = f"Bearer {auth_value}"
                        headers[header_name] = auth_value
                    try:
                        resp = requests.get(adspower_api_url, timeout=15, headers=headers)
                        resp.raise_for_status()
                        data = resp.json()
                        used_url = adspower_api_url
                        used_header = header_name
                        if data.get("code") == 0:
                            break
                        msg = data.get('msg') or ''
                        if isinstance(msg, str) and 'Too many request per second' in msg:
                            raise RuntimeError(f"AdsPower rate limited: {adspower_api_url} header={header_name} msg={msg}")
                        last_error = RuntimeError(f"AdsPower returned {data.get('code')}: {msg}")
                    except Exception as e:
                        last_error = e
                        print(f"[Antidetect] Start failed: {adspower_api_url} header={header_name} -> {e}")
                    time.sleep(0.25)
                if data is not None and data.get("code") == 0:
                    break

            if not data:
                raise RuntimeError(f"Không thể khởi động profile Anti-detect: {last_error}")

            if data.get("code") != 0:
                raise RuntimeError(f"Không thể khởi động profile Anti-detect: {data.get('msg')}")

            data_payload = data.get("data") or {}
            ws_payload = data_payload.get("ws") if isinstance(data_payload, dict) else None
            if isinstance(ws_payload, dict):
                ws_endpoint = ws_payload.get("puppeteer") or ws_payload.get("playwright") or ws_payload.get("ws")
            else:
                ws_endpoint = ws_payload

            if not ws_endpoint:
                raise RuntimeError("AdsPower did not return a websocket endpoint")

            print(f"[Antidetect] AdsPower launched via {used_url} header={used_header}")

            p = sync_playwright().start()
            # Connect over Chrome DevTools Protocol to the external profile-controlled browser
            b = p.chromium.connect_over_cdp(ws_endpoint)
            self.browser_type = "chromium"
            # Small pause to let the profile fully initialize
            time.sleep(0.5)
            return p, b

        else:
            raise NotImplementedError(f"Antidetect provider '{provider}' is not implemented")

    def get_thread_page(self):
        browser = self.get_thread_browser()
        ua = get_random_user_agent()
        print(f"[Stealth] Su dung User-Agent: {ua[:60]}...")
        context = browser.new_context(
            user_agent=ua,
            ignore_https_errors=True,
            viewport={"width": 1280, "height": 720}
        )
        context.set_default_navigation_timeout(OUTLOOK_NAVIGATION_TIMEOUT_MS)
        context.set_default_timeout(10000)
        return context.new_page()

    def handle_captcha(self, page):

        page.wait_for_event("request", lambda req: req.url.startswith("blob:https://iframe.hsprotect.net/"), timeout=22000)
        page.wait_for_timeout(800)

        for _ in range(0, self.max_captcha_retries + 1):

            page.keyboard.press('Enter')
            page.wait_for_timeout(11500)
            page.keyboard.press('Enter')

            try:
                page.wait_for_event("request", lambda req: req.url.startswith("https://browser.events.data.microsoft.com"), timeout=8000)
                try:
                    page.wait_for_event("request", lambda req: req.url.startswith("https://collector-pxzc5j78di.hsprotect.net/assets/js/bundle"), timeout=1700)
                    page.wait_for_timeout(2000)
                    continue

                except:
                    # Kiem tra xem co phai bi loi Rate Limit IP khong
                    if page.get_by_text('异常活动').count() or page.get_by_text('维护').count() > 0:
                        print("[Error: Rate limit] - IP nay dang ky qua nhanh, can doi IP/Proxy.")
                        return False
                    break

            except:
                # raise TimeoutError
                page.wait_for_timeout(5000)
                page.keyboard.press('Enter')
                page.wait_for_event("request", lambda req: req.url.startswith("https://browser.events.data.microsoft.com"), timeout=10000)

                try:
                    page.wait_for_event("request", lambda req: req.url.startswith("https://collector-pxzc5j78di.hsprotect.net/assets/js/bundle"), timeout=4000)
                except:
                    break
                page.wait_for_timeout(500)
        else:
            return False

        return True


    def clean_up(self, page=None, type="all_browser"):

        if type == "done_browser" and page:
            context = page.context
            context.close()

        elif type == "all_browser":
            for p, b in self.active_resources:
                try:
                    b.close()
                except Exception: pass
                try:
                    p.stop()
                except Exception: pass
