import json
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

    def launch_browser(self):
        try:
            import asyncio
            try:
                # Dập tắt loop đang tồn tại trên thread này để sync_playwright hoạt động an toàn
                asyncio.set_event_loop(None)
            except Exception:
                pass
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
