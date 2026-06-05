import time
from playwright.sync_api import sync_playwright

CDP_URL = 'http://127.0.0.1:57796'
SUFFIXES = ['@outlook.com', '@hotmail.com']

import argparse
parser = argparse.ArgumentParser()
parser.add_argument('--cdp-url', default=CDP_URL)
parser.add_argument('--suffix', default=','.join(SUFFIXES))
args = parser.parse_args()
CDP_URL = args.cdp_url
SUFFIXES = [s.strip() for s in args.suffix.split(',') if s.strip()] or SUFFIXES

p = sync_playwright().start()
try:
    b = p.chromium.connect_over_cdp(CDP_URL)
    done = False
    for ctx in b.contexts:
        for page in ctx.pages:
            try:
                url = page.url
            except Exception:
                url = ''
            if 'signup.live.com' in url or 'signup.live.com' in (page.title() or ''):
                print('Found signup page:', url)
                selectors = [
                    'input[name="loginfmt"]',
                    'input[type="email"]',
                    'input[placeholder*="Email"]',
                    'input'
                ]
                sel = None
                for s in selectors:
                    try:
                        if page.query_selector(s):
                            sel = s
                            break
                    except Exception:
                        continue
                if not sel:
                    print('No suitable input selector found; aborting for this page')
                    continue
                try:
                    current = page.input_value(sel)
                except Exception:
                    current = ''
                print('Current value:', current)
                if '@' not in current:
                    newval = None
                    for suffix in SUFFIXES:
                        candidate = current + suffix
                        try:
                            page.fill(sel, candidate)
                            newval = candidate
                            break
                        except Exception as e:
                            last_err = e
                    if newval is None:
                        print('Fill failed:', last_err)
                        continue
                else:
                    newval = current
                time.sleep(0.25)
                try:
                    page.click('button[type="submit"]', timeout=5000)
                except Exception:
                    try:
                        page.press(sel, 'Enter')
                    except Exception:
                        pass
                print('Filled and submitted:', newval)
                done = True
                time.sleep(1)
    if not done:
        print('Signup page not found in existing contexts/pages')
    try:
        b.close()
    except Exception:
        pass
finally:
    try:
        p.stop()
    except Exception:
        pass
