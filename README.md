# OpenArt Credit Bot

Bộ công cụ Python chạy trên Windows để tự động hóa các luồng đăng ký OpenArt, nhận credit theo chiến dịch cấu hình sẵn, tạo tài khoản Outlook/Hotmail và kiểm tra tình trạng môi trường cục bộ. Project có giao diện GUI bằng CustomTkinter, chạy đa luồng/đa worker, hỗ trợ proxy, Camoufox/Playwright và một số tiện ích kiểm thử.

> Chỉ sử dụng trong phạm vi bạn có quyền kiểm thử hoặc tự động hóa. Các luồng đăng ký hàng loạt, dùng proxy, lấy OTP/token hoặc nhận khuyến mãi có thể vi phạm điều khoản của dịch vụ bên thứ ba nếu dùng sai mục đích.

## Tính năng chính

- OpenArt bot: đăng ký tài khoản, nhận OTP từ Temp-Mail hoặc Outlook/Hotmail, xác minh email và truy cập trang claim credit.
- Worker pool: chạy nhiều worker song song theo `CONCURRENCY`, có pause/stop và thống kê trên GUI.
- Input account file: đọc `accounts.txt` nếu muốn chạy bằng danh sách email/password có sẵn.
- Proxy: lấy proxy từ `config.json`, phân bổ theo worker, hỗ trợ nhiều định dạng proxy phổ biến.
- Browser automation: OpenArt dùng Camoufox async; Outlook dùng Playwright hoặc Patchright nếu được cài.
- Outlook creator: tạo tài khoản `@outlook.com` hoặc `@hotmail.com`, ghi kết quả và có tùy chọn OAuth2 token.
- Health check: kiểm tra syntax Python, package, config và import module mà không mở trình duyệt hay gọi flow thật.
- VPN manager: module tùy chọn điều khiển ExpressVPN CLI để đổi IP khi được tích hợp vào flow khác.

## Cấu trúc project

```text
OpenArt-Credit-Bot/
├── bot.py                         # GUI và engine chính cho OpenArt
├── start-bot.py                   # Launcher GUI chọn OpenArt hoặc Outlook
├── start-bot.bat                  # Script Windows tạo venv, cài dependencies và mở menu chạy bot
├── config.example.json            # Cấu hình mẫu sạch cho OpenArt
├── accounts.txt                   # File local bị ignore, dùng cho account input tùy chọn
├── success_accounts.txt           # File output account OpenArt thành công
├── requirements.txt               # Dependencies Python chính
├── requirements-optional.txt      # Dependencies tùy chọn
├── health_check.py                # Kiểm tra môi trường offline
├── vpn_manager.py                 # Điều khiển ExpressVPN CLI tùy chọn
├── useragent.txt                  # Danh sách User-Agent
└── OutlookRegister/
    ├── bot_outlook.py             # GUI Outlook/Hotmail creator
    ├── main.py                    # CLI Outlook creator đa luồng
    ├── config.example.json        # Cấu hình mẫu sạch cho Outlook creator
    ├── utils.py                   # Sinh email/password, đọc User-Agent
    ├── get_token.py               # OAuth2 token helper
    └── controllers/               # Controller Playwright/Patchright
```

## Yêu cầu

- Windows 10/11.
- Python 3.10 trở lên.
- Kết nối mạng ổn định để cài package và tải browser runtime.
- Firefox hoặc browser runtime của Playwright/Camoufox.
- ExpressVPN Desktop/CLI nếu muốn dùng `vpn_manager.py`.

Dependencies chính trong `requirements.txt`:

```text
customtkinter
camoufox
Faker
httpx
playwright
requests
temp-mail
```

Nếu `OutlookRegister/config.json` đặt `choose_browser` là `patchright`, cần cài thêm package `patchright` bằng `python -m pip install -r requirements-optional.txt` hoặc `python -m pip install patchright`. Nếu không cài, một số luồng CLI có fallback về Playwright, nhưng GUI/flow Patchright có thể không chạy như mong muốn. File `OutlookRegister/config.json` là cấu hình local bị ignore; dùng `OutlookRegister/config.example.json` làm mẫu khi cần tạo mới.

## Cài đặt nhanh

Cách đơn giản nhất trên Windows là chạy:

```bat
start-bot.bat
```

Script này sẽ:

- Tạo môi trường ảo `.venv` nếu chưa có.
- Cài package từ `requirements.txt`.
- Cài browser runtime của Playwright cho Firefox và Chromium.
- Tải tài nguyên Camoufox khi chọn chạy OpenArt bot.
- Hiển thị menu chọn OpenArt bot hoặc Outlook creator.

## Cài đặt thủ công

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
copy config.example.json config.json
copy OutlookRegister\config.example.json OutlookRegister\config.json
python -m playwright install firefox chromium
python -m camoufox fetch
```

Không commit hai file cấu hình local `config.json` và `OutlookRegister/config.json` sau khi tạo.

Chạy launcher chung:

```powershell
python start-bot.py
```

Chạy trực tiếp OpenArt bot:

```powershell
python bot.py
```

Chạy trực tiếp Outlook GUI:

```powershell
python .\OutlookRegister\bot_outlook.py
```

Chạy Outlook CLI đa luồng:

```powershell
cd OutlookRegister
python main.py
```

## Cấu hình OpenArt

File cấu hình chính nằm ở `config.json` tại thư mục gốc. File này là cấu hình local bị ignore; repo có `config.example.json` làm mẫu sạch. Sau khi clone, tạo file local bằng `copy config.example.json config.json` rồi điền URL, domain, password, proxy hoặc token của môi trường riêng.

| Khóa | Ý nghĩa |
| --- | --- |
| `MAIL_API_BASE` | Base URL của mail API/temp mail service. |
| `MAIL_DOMAIN` | Domain email dùng khi bot tự sinh email. |
| `SIGNUP_URL` | URL đăng ký OpenArt. |
| `CREDIT_URL` | URL nhận credit sau khi đăng ký/xác minh. |
| `PASSWORD` | Password mặc định khi bot tự sinh email. |
| `LOOP_COUNT` | Tổng số account cần chạy; `0` thường dùng cho chế độ không giới hạn hoặc theo file input. |
| `CONCURRENCY` | Số worker chạy song song. |
| `DELAY_BETWEEN_ACCOUNTS` | Thời gian nghỉ giữa các account trong cùng worker. |
| `OTP_POLL_INTERVAL` | Khoảng cách giữa các lần kiểm tra OTP. |
| `OTP_MAX_ATTEMPTS` | Số lần kiểm tra OTP tối đa. |
| `HEADLESS` | `true` để chạy ẩn browser, `false` để hiện browser. |
| `MINIMIZE_TASKBAR` | Tùy chọn thu nhỏ cửa sổ/taskbar nếu GUI hỗ trợ. |
| `CAPTCHA_API_KEY` | API key captcha nếu flow cần tích hợp. |
| `FIREFOX_PATH` | Đường dẫn Firefox tùy chọn; nếu trống bot tự dò. |
| `PROXIES` | Danh sách proxy phân bổ cho worker. |
| `LOG_FILE` | File ghi account thành công, mặc định `success_accounts.txt`. |
| `TEMP_MAIL_API_KEY` | API key cho Temp-Mail nếu dùng endpoint yêu cầu key. |
| `MAILVIP_API_TOKEN` | Token mailvip.net tùy chọn; cũng có thể đặt bằng biến môi trường cùng tên. |

Proxy có thể dùng các dạng như:

```text
host:port
host:port:username:password
http://host:port
http://username:password@host:port
```

## File accounts.txt

Nếu `accounts.txt` có dữ liệu, OpenArt bot sẽ ưu tiên đọc account từ file thay vì tự sinh email. Mỗi dòng một account:

```text
email@example.com|password
```

Với email Outlook/Hotmail, bot có thể dò thêm thông tin token/client để lấy OTP qua API nếu dòng có đủ trường:

```text
email@hotmail.com|password|refresh_token|client_id
```

Dòng trống và dòng bắt đầu bằng `#` sẽ được bỏ qua.

## Kết quả OpenArt

Khi account đăng ký hoặc claim thành công, bot ghi vào:

- `success_accounts.txt`: log tổng theo `LOG_FILE`.
- `accounts_YYYYMMDD_HHMMSS.txt`: log riêng cho từng phiên chạy.

Các file này có thể chứa email, password hoặc token. Không commit các file output/account thật lên repository.

## Cấu hình Outlook creator

File cấu hình riêng nằm ở `OutlookRegister/config.json`. File này là dữ liệu local bị ignore; repo có `OutlookRegister/config.example.json` làm mẫu sạch, và GUI có thể tự tạo `config.json` khi chạy.

| Khóa | Ý nghĩa |
| --- | --- |
| `choose_browser` | `playwright` hoặc `patchright`. |
| `email_suffix` | `@outlook.com` hoặc `@hotmail.com`. |
| `proxy` | Proxy đơn cho Outlook creator. |
| `use_parent_proxies` | Nếu `true`, đọc proxy từ `../config.json`. |
| `bot_protection_wait` | Thời gian chờ giữa các thao tác. |
| `max_captcha_retries` | Số lần thử lại khi gặp captcha/lỗi tương tự. |
| `concurrent_flows` | Số flow Outlook chạy song song. |
| `max_tasks` | Tổng số account cần tạo. |
| `headless` | Chạy browser ẩn hay hiển thị. |
| `timeout_secs` | Timeout điều hướng Outlook. |
| `proxies` | Danh sách proxy riêng của Outlook creator. |
| `oauth2` | Cấu hình lấy token OAuth2 nếu bật. |
| `playwright.browser_path` | Đường dẫn browser tùy chọn. |

Kết quả Outlook thường nằm trong:

- `OutlookRegister/thanhcong.txt`
- `OutlookRegister/outlook_success.txt`
- `OutlookRegister/Results/`

## Kiểm tra môi trường

Chạy health check offline:

```powershell
python health_check.py
```

Script sẽ kiểm tra:

- Các file Python có compile được không.
- Package bắt buộc có import được không.
- `config.json`/`config.example.json` và `OutlookRegister/config.json`/`OutlookRegister/config.example.json` có đủ key chính không.
- Các module helper ít side effect có import được không; module GUI chính chỉ được kiểm tra bằng compile.
- Tình trạng package tùy chọn `patchright`.

## Kiểm tra tự động

Repo có GitHub Actions tại `.github/workflows/health-check.yml`. Mỗi lần push hoặc mở pull request, workflow sẽ cài dependency, chạy flake8 nhóm lỗi nghiêm trọng, compile các file chính và chạy `python health_check.py`.

Để chặn commit nhầm secret trên máy local:

```powershell
python -m pip install pre-commit detect-secrets
python -m pre_commit install
```

Baseline secret hiện tại nằm ở `.secrets.baseline`. Khi thêm file mới, chạy lại:

```powershell
python -m detect_secrets scan > .secrets.baseline
python -m pre_commit run --all-files
```

Nếu muốn dùng biến môi trường thay vì điền trực tiếp vào JSON local, copy `.env.example` thành `.env` rồi điền giá trị riêng. File `.env` bị ignore và không được commit.

## VPN manager

`vpn_manager.py` điều khiển ExpressVPN qua executable mặc định:

```text
C:\Program Files\ExpressVPN\expressvpn-client.exe
```

Module này đọc thêm các key kiểu `change_ip_mode` hoặc `num_fail_to_change_ip` nếu có trong config. Nếu không dùng ExpressVPN hoặc không tích hợp gọi `VpnManager`, có thể bỏ qua.

## Lưu ý bảo mật

- `config.json`, `accounts.txt`, `success_accounts.txt`, `OutlookRegister/config.json`, `OutlookRegister/thanhcong.txt` và thư mục `OutlookRegister/Results/` có thể chứa dữ liệu nhạy cảm.
- Không đưa API key, proxy trả phí, email/password hoặc refresh token lên git/public repo.
- `.gitignore` bỏ qua account/output/cache runtime và cấu hình local. Không stage proxy/key/account thật khi cập nhật repo.
- Nên dùng file config mẫu riêng nếu muốn chia sẻ project cho người khác.
- Nếu secret từng nằm trong git history, hãy coi như đã lộ và rotate password/token/API/proxy liên quan. Xóa khỏi HEAD không xóa được lịch sử đã public.

## Anti-detect và Proxy (Khuyến nghị)

- **Không dùng** proxy miễn phí, shared proxy, hoặc rotating/mobile proxies chất lượng thấp.
- Ưu tiên dùng **Static Residential Proxy (ISP)** hoặc **Dedicated Mobile Proxy (4G/5G)** từ nhà mạng lớn để giảm rủi ro bị block bởi hệ thống `risk/verify`.
- Để tích hợp công cụ anti-detect (AdsPower/Gologin), chỉnh `OutlookRegister/config.json` thêm phần `playwright.antidetect` và bật `enabled: true`. Ví dụ:

```json
"playwright": {
  "browser_path": "",
  "antidetect": {
    "enabled": true,
    "provider": "adspower",
    "profile_id": "your_profile_id",
    "local_api": "http://local.adspower.net:50325",
    "api_key": "your_api_key_if_enabled",
    "api_key_header": "Authorization"
  }
}
```

Hoặc gọi trực tiếp `adspower_test.py` không cần nhập tương tác:

```powershell
cd D:\OpenArt-Credit-Bot\OutlookRegister
python adspower_test.py --profile-id k1d2udx3 --local-api http://local.adspower.net:50325 --api-key your_api_key
```

- Hiện tại code hỗ trợ kết nối tới AdsPower qua API cục bộ (ws puppeteer endpoint). Sau khi bật, bot sẽ cố gắng kết nối profile và điều khiển cửa sổ trình duyệt sạch thay vì khởi chạy Playwright local.
- Nếu AdsPower hiển thị `http://local.adspower.net:50325`, hãy dùng `local_api` đó thay vì `127.0.0.1:5015`.
- Nếu AdsPower bật `API verification`, hãy điền `api_key` vào config và có thể phải dùng `api_key_header` phù hợp với header yêu cầu (mặc định `Authorization`).

## Xử lý lỗi thường gặp

- Thiếu package: chạy lại `python -m pip install -r requirements.txt` trong `.venv`.
- Thiếu browser runtime: chạy `python -m playwright install firefox chromium`.
- Camoufox lỗi tài nguyên: chạy `python -m camoufox fetch`.
- Outlook không mở được form email: kiểm tra proxy, `timeout_secs`, browser runtime và `choose_browser`.
- Không nhận OTP: kiểm tra mail API, `TEMP_MAIL_API_KEY`, domain, `OTP_POLL_INTERVAL`, `OTP_MAX_ATTEMPTS` và format `accounts.txt`.
- Proxy chết/chậm: giảm `CONCURRENCY`, tăng timeout hoặc thay danh sách proxy.
