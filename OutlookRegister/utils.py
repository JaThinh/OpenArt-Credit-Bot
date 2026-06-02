import random
import string
import secrets
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(BASE_DIR, os.pardir))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
ROOT_CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")
RESULTS_DIR = os.path.join(BASE_DIR, "Results")
USER_AGENT_PATHS = (
    os.path.join(ROOT_DIR, "useragent.txt"),
    os.path.join(BASE_DIR, "useragent.txt"),
)

def random_email(length=None):
    if length is None:
        length = random.randint(12, 14)

    first_char = random.choice(string.ascii_lowercase)

    other_chars = []
    for _ in range(length - 1):
        if random.random() < 0.07:
            other_chars.append(random.choice(string.digits))
        else:
            other_chars.append(random.choice(string.ascii_lowercase))

    return first_char + ''.join(other_chars)

def generate_strong_password(length=None):
    if length is None:
        length = random.randint(11, 15)

    chars = string.ascii_letters + string.digits + "!@#$%^&*"

    while True:
        password = ''.join(secrets.choice(chars) for _ in range(length))

        if (any(c.islower() for c in password)
                and any(c.isupper() for c in password)
                and any(c.isdigit() for c in password)
                and any(c in "!@#$%^&*" for c in password)):
            return password

def get_random_user_agent():
    # Thử đọc từ thư mục cha hoặc thư mục hiện tại
    for path in USER_AGENT_PATHS:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    lines = [line.strip() for line in f if line.strip()]
                if lines:
                    return random.choice(lines)
            except Exception:
                pass
    # Mặc định nếu không tìm thấy tệp
    return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

async def type_text_slowly(page, selector, text_value, min_delay_ms=60, max_delay_ms=140):
    """Focus a selector and type text one character at a time."""
    if min_delay_ms > max_delay_ms:
        raise ValueError("min_delay_ms must be <= max_delay_ms")

    await page.focus(selector)
    for char in str(text_value):
        await page.keyboard.type(char, delay=random.randint(min_delay_ms, max_delay_ms))
