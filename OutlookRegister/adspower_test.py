r"""
adspower_test.py

Script kiểm thử kết nối tới AdsPower local API và kết nối Playwright qua WebSocket.
Chạy trên máy có AdsPower đang chạy cục bộ và profile đã tồn tại.

Cách dùng (PowerShell):

    cd .\OutlookRegister
    .\.venv\Scripts\Activate.ps1
    python adspower_test.py

"""
import argparse
import json
import os
import sys
import time
import requests

try:
    from playwright.sync_api import sync_playwright
except Exception as e:
    print(f"Playwright import error: {e}")
    sys.exit(2)

from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config():
    if not CONFIG_PATH.exists():
        print(f"Không tìm thấy {CONFIG_PATH}")
        return None
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Lỗi đọc config: {e}")
        return None


DEFAULT_API_KEY_HEADERS = ["Authorization", "x-api-key", "api_key", "api-key"]


def request_json(url, api_key=None, api_key_header="Authorization", timeout=15):
    headers = {}
    if api_key:
        auth_value = api_key
        if api_key_header.lower() == "authorization" and not auth_value.lower().startswith("bearer "):
            auth_value = f"Bearer {auth_value}"
        headers[api_key_header] = auth_value
    r = requests.get(url, timeout=timeout, headers=headers)
    r.raise_for_status()
    return r.json()


def probe_ads_power_endpoints(api_base, api_key=None, api_key_header="Authorization"):
    base = api_base.rstrip('/')
    candidates = [
        f"{base}/api/v1/profile/list",
        f"{base}/api/v1/browser/profile/list",
        f"{base}/api/v2/profile/list",
    ]
    for url in candidates:
        try:
            data = request_json(url, api_key=api_key, api_key_header=api_key_header)
            print(f"Đọc danh sách profile thành công từ: {url}")
            return url, data
        except Exception as e:
            print(f"Thử list failed: {url} -> {e}")
    return None, None


def build_start_urls(api_base, profile_id):
    base = api_base.rstrip('/')
    return [
        f"{base}/api/v1/browser/start?user_id={profile_id}",
        f"{base}/api/v1/browser/start?id={profile_id}",
        f"{base}/api/v1/profile/start?user_id={profile_id}",
        f"{base}/api/v1/profile/start?id={profile_id}",
        f"{base}/api/v2/browser/start?user_id={profile_id}",
        f"{base}/api/v2/profile/start?user_id={profile_id}",
        f"{base}/api/v2/profile/start?id={profile_id}",
    ]


def build_api_key_headers(api_key_header):
    headers = []
    if api_key_header:
        headers.append(api_key_header)
    for candidate in DEFAULT_API_KEY_HEADERS:
        if candidate not in headers:
            headers.append(candidate)
    return headers


def start_adspower_profile(api_base, profile_id, api_key=None, api_key_header="Authorization", timeout=15):
    headers_to_try = build_api_key_headers(api_key_header) if api_key else [None]
    last_error = None

    for url in build_start_urls(api_base, profile_id):
        for header_name in headers_to_try:
            headers = {}
            if api_key and header_name:
                auth_value = api_key
                if header_name.lower() == "authorization" and not auth_value.lower().startswith("bearer "):
                    auth_value = f"Bearer {auth_value}"
                headers[header_name] = auth_value
            if header_name:
                print(f"Gọi AdsPower start: {url} with header {header_name}")
            else:
                print(f"Gọi AdsPower start: {url} without auth")
            try:
                r = requests.get(url, timeout=timeout, headers=headers)
                r.raise_for_status()
                data = r.json()
                if data.get('code') == 0:
                    return data, url, header_name
                last_error = RuntimeError(f"AdsPower returned {data.get('code')}: {data.get('msg')}")
                print(f"Start response: {data}")
            except Exception as e:
                last_error = e
                print(f"Start failed: {e}")
    raise RuntimeError(f"Không tìm thấy endpoint start phù hợp: {last_error}")


def main():
    cfg = load_config()
    if not cfg and not args.profile_id:
        sys.exit(1)

    antidetect = {}
    if cfg:
        antidetect = cfg.get('playwright', {}).get('antidetect', {}) or {}

    if not antidetect and not args.profile_id:
        print('Config antidetect không tồn tại hoặc rỗng. Vui lòng chỉnh OutlookRegister/config.json hoặc dùng --profile-id.')
        sys.exit(1)

    api_base = args.local_api or antidetect.get('local_api') or 'http://local.adspower.net:50325'
    profile_id = args.profile_id or antidetect.get('profile_id') or antidetect.get('user_id')
    api_key = args.api_key or antidetect.get('api_key')
    api_key_header = args.api_key_header or antidetect.get('api_key_header') or 'Authorization'

    if not profile_id:
        print('Không tìm thấy "profile_id" trong config và không có --profile-id. Vui lòng dùng --profile-id để chạy không tương tác.')
        sys.exit(1)

    if " " in profile_id:
        parts = profile_id.split()
        if len(parts) > 1:
            print(f'Cảnh báo: profile_id chứa nhiều token, chỉ dùng token đầu tiên: {parts[0]}')
        profile_id = parts[0]

    probe_url, probe_data = probe_ads_power_endpoints(api_base, api_key=api_key, api_key_header=api_key_header)
    if probe_url and probe_data:
        try:
            print('AdsPower list response:')
            print(json.dumps(probe_data, indent=2, ensure_ascii=False))
        except Exception:
            print(probe_data)

    try:
        resp, used_url, used_header = start_adspower_profile(api_base, profile_id, api_key=api_key, api_key_header=api_key_header)
    except Exception as e:
        print(f"Không gọi được AdsPower API: {e}")
        sys.exit(2)

    print(f'Endpoint start đã dùng: {used_url}')
    print(f'API key header đã dùng: {used_header}')

    print('AdsPower API trả về:')
    try:
        print(json.dumps(resp, indent=2, ensure_ascii=False))
    except Exception:
        print(resp)

    code = resp.get('code')
    if code != 0:
        print(f"AdsPower không trả về code 0 (thất bại): {resp.get('msg')}")
        sys.exit(3)

    # Lấy websocket endpoint (nhiều phiên bản có key khác nhau)
    ws_endpoint = None
    data = resp.get('data') or {}
    if isinstance(data, dict):
        ws = data.get('ws') or {}
        if isinstance(ws, dict):
            ws_endpoint = ws.get('puppeteer') or ws.get('playwright') or ws.get('ws')

    if not ws_endpoint:
        print('Không tìm thấy websocket endpoint trong response AdsPower.')
        sys.exit(4)

    print(f'Thử connect Playwright tới: {ws_endpoint}')

    try:
        p = sync_playwright().start()
        # AdsPower may return a WebSocket devtools URL like ws://127.0.0.1:57796/devtools/browser/...
        # Playwright can connect via CDP over HTTP. Convert ws://host:port/... -> http://host:port
        cdp_url = None
        if isinstance(ws_endpoint, str) and ws_endpoint.startswith('ws://'):
            try:
                hostpart = ws_endpoint.split('://', 1)[1].split('/', 1)[0]
                cdp_url = f"http://{hostpart}"
            except Exception:
                cdp_url = None
        # If ws_endpoint is already an http(s) CDP URL, use it directly
        if not cdp_url and isinstance(ws_endpoint, str) and ws_endpoint.startswith('http'):
            cdp_url = ws_endpoint

        if cdp_url:
            print(f'Connecting to CDP endpoint: {cdp_url}')
            b = p.chromium.connect_over_cdp(cdp_url)
        else:
            # Fallback: try connecting over WS (older codepaths)
            b = p.chromium.connect_over_cdp(ws_endpoint)
        print('Kết nối Playwright thành công!')
        # Thử mở trang kiểm tra
        context = b.contexts[0] if getattr(b, 'contexts', None) else None
        if context:
            pages = context.pages
            print(f'Context có {len(pages)} trang đang mở')
        # Đóng kết nối
        try:
            b.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass
        print('Đã ngắt kết nối Playwright.')
        sys.exit(0)
    except Exception as e:
        print(f'Không thể kết nối Playwright over WS: {e}')
        try:
            p.stop()
        except Exception:
            pass
        sys.exit(5)


def parse_args():
    parser = argparse.ArgumentParser(description='Kiểm thử kết nối AdsPower Local API và Playwright WS.')
    parser.add_argument('--profile-id', help='AdsPower profile_id hoặc user_id')
    parser.add_argument('--local-api', help='Local API URL của AdsPower (vd: http://local.adspower.net:50325)')
    parser.add_argument('--api-key', help='API key nếu AdsPower bật API verification')
    parser.add_argument('--api-key-header', default='api-key', help='Header key cho API key (mặc định api-key)')
    return parser.parse_args()

if __name__ == '__main__':
    args = parse_args()
    main()
