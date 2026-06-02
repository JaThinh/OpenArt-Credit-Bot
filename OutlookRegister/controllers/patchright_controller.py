import random
from patchright.sync_api import sync_playwright
from .base_controller import BaseBrowserController
from .base_controller import build_browser_launch_args
try:
    from utils import get_random_user_agent
except ImportError:
    from ..utils import get_random_user_agent


class PatchrightController(BaseBrowserController):

    def launch_browser(self):
        try:
            import asyncio
            try:
                # Dập tắt loop đang tồn tại trên thread này để sync_playwright hoạt động an toàn
                asyncio.set_event_loop(None)
            except Exception:
                pass
            p = sync_playwright().start()

            launch_args = build_browser_launch_args(self.proxy, bypass="localhost")

            b = p.chromium.launch(
                headless=self.headless,
                args=['--lang=zh-CN'],
                **launch_args
            )

            return p, b

        except Exception as e:
            print(f"启动浏览器失败: {e}")
            return False, False

    def handle_captcha(self, page):

        frame1 = page.frame_locator('iframe[title="验证质询"]')
        frame2 = frame1.frame_locator('iframe[style*="display: block"]')


        for _ in range(0, self.max_captcha_retries + 1):

            page.wait_for_timeout(200)
            loc = frame2.locator('[aria-label="可访问性挑战"]')
            box = loc.bounding_box()
            x = box['x'] + box['width'] / 2 + random.randint(-10, 10)
            y = box['y'] + box['height'] / 2 + random.randint(-10, 10)
            page.mouse.click(x, y)

            loc2 = frame2.locator('[aria-label="再次按下"]')
            box2 = loc2.bounding_box()
            x = box2['x'] + box2['width'] / 2 + random.randint(-20, 20)
            y = box2['y'] + box2['height'] / 2 + random.randint(-13, 13)
            page.mouse.click(x, y)

            try:

                page.locator('.draw').wait_for(state="detached")
                try:

                    # 简单的认为加载8秒后成功，暂不考虑请求.
                    page.locator('[role="status"][aria-label="正在加载..."]').wait_for(timeout=5000)
                    page.wait_for_timeout(8000)
                    if page.get_by_text('一些异常活动').count() or page.get_by_text('此站点正在维护，暂时无法使用，请稍后重试。').count() > 0:
                        print("[Error: Rate limit] - 正常通过验证码，但当前IP注册频率过快。")
                        return False
                    elif frame2.locator('[aria-label="可访问性挑战"]').count() > 0:
                        continue
                    break

                except:

                    if page.get_by_text('取消').count() > 0:
                        break
                    frame1.get_by_text("请再试一次").wait_for(timeout=15000)
                    continue

            except:
                if page.get_by_text('取消').count() > 0:
                     break
                return False
        else:
            return False

        return True

    def get_thread_page(self):
        browser = self.get_thread_browser()
        ua = get_random_user_agent()
        print(f"[Stealth] Sử dụng User-Agent: {ua[:60]}...")
        context = browser.new_context(
            user_agent=ua,
            ignore_https_errors=True,
            viewport={"width": 1280, "height": 720}
        )
        return context.new_page()

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
