import sys
import os
import customtkinter as ctk

# Đảm bảo console chạy UTF-8 trên Windows để tránh lỗi UnicodeEncodeError
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# Thêm thư mục OutlookRegister vào sys.path để import đúng cách
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT_DIR, "OutlookRegister"))
sys.path.insert(0, ROOT_DIR)

import subprocess

def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    BG = "#0f0206"
    PANEL = "#250914"
    GREEN = "#ff758f"
    GREEN_SOFT = "#ffccd5"
    GREEN_DARK = "#ff477e"
    GREEN_DIM = "#590d22"
    TEXT = "#ffe5ec"

    # Tạo ứng dụng chính
    app = ctk.CTk()
    app.title("✿ SAKURA BOT POOL ✿")
    app.geometry("420x460")
    app.minsize(380, 420)
    app.configure(fg_color=BG)  # Nền tối Sakura cực dễ thương

    container = ctk.CTkFrame(app, fg_color=BG, border_width=0, corner_radius=0)
    container.pack(fill="both", expand=True, padx=25, pady=25)

    # 1. Header Bar (Tiêu đề Launcher)
    header = ctk.CTkFrame(container, fg_color=PANEL, height=80, corner_radius=15, border_width=1, border_color=GREEN_DARK)
    header.pack(fill="x", pady=(0, 20))

    lbl_logo = ctk.CTkLabel(header, text="✿ SAKURA BOT POOL ✿", font=("Segoe UI", 16, "bold"), text_color=GREEN)
    lbl_logo.pack(pady=(12, 2))
    lbl_subtitle = ctk.CTkLabel(header, text="Hệ thống tự động hóa All-in-One", font=("Segoe UI", 11), text_color=GREEN_SOFT)
    lbl_subtitle.pack(pady=(0, 12))

    # Khung chứa thông báo hướng dẫn
    info_panel = ctk.CTkFrame(container, fg_color="transparent")
    info_panel.pack(fill="x", pady=(0, 15))
    lbl_info = ctk.CTkLabel(
        info_panel,
        text="Khởi chạy các Bot độc lập trên các tiến trình riêng biệt\ngiúp tối ưu hóa tài nguyên phần cứng và kháng xung đột.",
        font=("Segoe UI", 11, "italic"),
        text_color=TEXT,
        justify="center"
    )
    lbl_info.pack()

    # Hàm khởi chạy Bot ở tiến trình độc lập
    def launch_bot(bot_type):
        try:
            if bot_type == "openart":
                script_path = os.path.join(ROOT_DIR, "bot.py")
                subprocess.Popen([sys.executable, script_path])
            else:
                script_path = os.path.join(ROOT_DIR, "OutlookRegister", "bot_outlook.py")
                # Thêm PYTHONPATH để Outlook Bot import đúng các utils cùng cấp của nó
                env = os.environ.copy()
                env["PYTHONPATH"] = os.path.join(ROOT_DIR, "OutlookRegister") + os.pathsep + env.get("PYTHONPATH", "")
                subprocess.Popen([sys.executable, script_path], cwd=os.path.join(ROOT_DIR, "OutlookRegister"), env=env)
        except Exception as e:
            print(f"Lỗi khởi chạy bot: {e}")

    # 2 Nút khởi chạy lớn cực đẹp
    btn_openart = ctk.CTkButton(
        container,
        text="🚀  KHỞI CHẠY OPENART BOT  🚀",
        font=("Segoe UI", 13, "bold"),
        height=55,
        fg_color=GREEN_DIM,
        hover_color=GREEN_DARK,
        border_color=GREEN,
        border_width=1,
        text_color=GREEN_SOFT,
        corner_radius=10,
        command=lambda: launch_bot("openart")
    )
    btn_openart.pack(fill="x", pady=8)

    btn_outlook = ctk.CTkButton(
        container,
        text="📧  KHỞI CHẠY OUTLOOK CREATOR  📧",
        font=("Segoe UI", 13, "bold"),
        height=55,
        fg_color="#0d1f2d",
        hover_color="#1d3557",
        border_color="#457b9d",
        border_width=1,
        text_color="#a8dadc",
        corner_radius=10,
        command=lambda: launch_bot("outlook")
    )
    btn_outlook.pack(fill="x", pady=8)

    app.mainloop()

if __name__ == "__main__":
    main()