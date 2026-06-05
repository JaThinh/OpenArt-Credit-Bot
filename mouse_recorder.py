import time
import threading
from pynput import mouse

# Biến toàn cục lưu trữ tọa độ được ghi nhớ
recorded_position = {"x": 0, "y": 0, "has_clicked": False}
mouse_controller = mouse.Controller()

def on_click(x, y, button, pressed):
    """Hàm lắng nghe sự kiện click chuột của người dùng"""
    if button == mouse.Button.left and pressed:
        recorded_position["x"] = x
        recorded_position["y"] = y
        recorded_position["has_clicked"] = True
        print(f"\n[RECORDER] Đã ghi nhớ tọa độ mục tiêu: X={x}, Y={y}")
        return False # Dừng việc lắng nghe sau khi đã lấy được 1 tọa độ click đầu tiên

def get_manual_coordinate(timeout_seconds=30):
    """Hàm bật chế độ chờ người dùng click để lấy tọa độ"""
    recorded_position["has_clicked"] = False
    print(f"[RECORDER] Vui lòng CLICK CHUỘT TRÁI vào vị trí nút trên trình duyệt để bot ghi nhớ...")

    # Chạy bộ lắng nghe chuột
    listener = mouse.Listener(on_click=on_click)
    listener.start()

    start_time = time.time()
    while not recorded_position["has_clicked"]:
        if time.time() - start_time > timeout_seconds:
            listener.stop()
            print("[RECORDER] Hết thời gian chờ người dùng click lấy tọa độ.")
            return None
        time.sleep(0.2)

    return (recorded_position["x"], recorded_position["y"])

def perform_press_and_hold(x, y, duration_ms=6000):
    """Hàm tự động di chuyển đến tọa độ đã lưu và nhấn giữ"""
    print(f"[RECORDER] Đang di chuyển chuột đến (X={x}, Y={y}) và nhấn giữ trong {duration_ms/1000} giây...")

    # Di chuyển chuột đến vị trí
    mouse_controller.position = (x, y)
    time.sleep(0.5)

    # Nhấn giữ chuột trái
    mouse_controller.press(mouse.Button.left)

    # Đợi hết thời gian giữ (đổi từ ms sang giây)
    time.sleep(duration_ms / 1000.0)

    # Thả chuột trái
    mouse_controller.release(mouse.Button.left)
    print("[RECORDER] Đã hoàn thành thao tác giữ và thả chuột!")