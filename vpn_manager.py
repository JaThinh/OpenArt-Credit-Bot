"""Thread-safe ExpressVPN manager using subprocess only."""
import os
import random
import subprocess
import threading
import time

import requests

try:
    from config_loader import Config
except ImportError:
    class Config:
        """Small fallback config used when legacy config_loader.py is absent."""

        def __init__(self):
            config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
            data = {}
            if os.path.exists(config_path):
                try:
                    import json

                    with open(config_path, "r", encoding="utf-8") as handle:
                        data = json.load(handle)
                except Exception:
                    data = {}

            self.change_ip_mode = str(data.get("change_ip_mode", data.get("CHANGE_IP_MODE", "off"))).lower()
            try:
                self.num_fail_to_change_ip = max(
                    1,
                    int(data.get("num_fail_to_change_ip", data.get("NUM_FAIL_TO_CHANGE_IP", 3)) or 3),
                )
            except (TypeError, ValueError):
                self.num_fail_to_change_ip = 3

try:
    from logger_setup import get_logger
except ImportError:
    class _FallbackLogger:
        def info(self, message):
            print(f"[INFO] {message}")

        def warning(self, message):
            print(f"[WARN] {message}")

        def error(self, message):
            print(f"[ERROR] {message}")

        def success(self, message):
            print(f"[SUCCESS] {message}")

    def get_logger():
        return _FallbackLogger()


logger = get_logger()


class VpnManager:
    """Control ExpressVPN directly through its local executable."""

    def __init__(self):
        self.vpn_path = r"C:\Program Files\ExpressVPN\expressvpn-client.exe"
        self._lock = threading.Lock()
        self._changing = False
        self._config = Config()
        self._fail_count = 0
        self._fail_lock = threading.Lock()

    @property
    def is_changing(self):
        return self._changing

    def increment_fail(self):
        with self._fail_lock:
            self._fail_count += 1
            threshold = self._config.num_fail_to_change_ip
            if self._fail_count >= threshold:
                self._fail_count = 0
                return True
            return False

    def reset_fail(self):
        with self._fail_lock:
            self._fail_count = 0

    def get_public_ip(self):
        endpoints = (
            "https://api.ipify.org",
            "https://ifconfig.me/ip",
        )
        for url in endpoints:
            try:
                response = requests.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=10,
                )
                if response.status_code == 200:
                    ip = response.text.strip()
                    if ip:
                        return ip
            except Exception:
                continue
        return ""

    def change_ip(self):
        if self._changing:
            logger.info("Thread khac dang doi IP, doi...")
            while self._changing:
                time.sleep(2)
            return True

        with self._lock:
            if self._changing:
                return True
            self._changing = True

        try:
            return self._do_change_ip()
        finally:
            with self._lock:
                self._changing = False

    def _do_change_ip(self):
        if self._config.change_ip_mode == "off":
            logger.info("Doi IP da tat trong config")
            return False
        return self._do_change_ip_evpn()

    def _do_change_ip_evpn(self):
        """Goi truc tiep ExpressVPN CLI de disconnect/connect lay IP moi."""
        try:
            if not os.path.exists(self.vpn_path):
                logger.error(f"Khong tim thay ExpressVPN executable: {self.vpn_path}")
                return False

            print("[VPN] Dang gui lenh ngat ket noi IP cu...")
            logger.info("Dang gui lenh ngat ket noi IP cu...")
            subprocess.run(
                [self.vpn_path, "disconnect"],
                capture_output=True,
                text=True,
                timeout=12,
                check=False,
            )
            time.sleep(3)

            regions = ["singapore", "hongkong", "japan", "thailand", "taiwan"]
            selected_region = random.choice(regions)
            print(f"[VPN] Dang gui lenh ket noi den quoc gia: {selected_region}...")
            logger.info(f"Dang gui lenh ket noi ExpressVPN den quoc gia: {selected_region}")
            result = subprocess.run(
                [self.vpn_path, "connect", selected_region],
                capture_output=True,
                text=True,
                timeout=25,
                check=False,
            )

            if result.returncode == 0:
                print("[VPN] ExpressVPN da doi sang dia chi IP moi thanh cong!")
                logger.success(f"ExpressVPN da doi sang IP moi thanh cong: {selected_region}")
                return True

            print("[VPN] Ket noi vung cu the that bai. Thu ket noi Smart Location mac dinh...")
            logger.warning(
                "ExpressVPN connect theo vung that bai: "
                f"{self._format_process_output(result)}"
            )
            retry = subprocess.run(
                [self.vpn_path, "connect"],
                capture_output=True,
                text=True,
                timeout=25,
                check=False,
            )
            if retry.returncode == 0:
                logger.success("ExpressVPN da doi IP thanh cong bang Smart Location")
                return True

            logger.error(
                "ExpressVPN Smart Location that bai: "
                f"{self._format_process_output(retry)}"
            )
            return False
        except Exception as exc:
            print(f"[VPN] Loi dieu khien ExpressVPN CLI qua lenh he thong: {exc}")
            logger.error(f"Loi dieu khien ExpressVPN CLI qua lenh he thong: {exc}")
            return False

    @staticmethod
    def _format_process_output(result):
        output = (result.stdout or "").strip()
        error = (result.stderr or "").strip()
        parts = [part for part in (output, error) if part]
        return " | ".join(parts) or f"exit={result.returncode}"
