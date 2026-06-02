"""
Orchestration Bridge - Wrapper để tích hợp Async Orchestrator vào Sync Code

Module này cung cấp bridge để gọi AutonomousLoadOrchestrator (async)
từ code sync (như bot_outlook.py hiện tại).
"""

import asyncio
import logging
import threading
from pathlib import Path
from advanced_orchestrator import AutonomousLoadOrchestrator

logging.basicConfig(level=logging.INFO)

class OrchestrationBridge:
    """
    Bridge để gọi async orchestrator từ sync context
    Tự động quản lý event loop và thread-safety
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, config=None):
        """Singleton pattern để tránh tạo nhiều event loop"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, config=None):
        if self._initialized:
            return

        self.config = config or {
            "max_exception_retries": 3,
            "bot_protection_wait": 5.0,
            "headless": False,
            "sandbox_base_dir": "./sandboxes"
        }

        self.orchestrator = AutonomousLoadOrchestrator(self.config)
        self._loop = None
        self._initialized = True

    def _get_or_create_loop(self):
        """
        Lấy event loop hiện tại hoặc tạo mới (thread-safe)
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("Event loop is closed")
            return loop
        except RuntimeError:
            # Tạo event loop mới cho thread này
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop

    def execute_session_sync(self, worker_id, target_url, record_data, proxy_pool=None, timeout_sec=120):
        """
        Gọi async session từ sync code (BLOCKING - chờ kết quả)

        Args:
            worker_id (int): ID worker
            target_url (str): URL mục tiêu
            record_data (dict): {"email": "...", "password": "..."}
            proxy_pool (list): Danh sách dict proxy với keys: server, username, password
            timeout_sec (int): Timeout tối đa (giây)

        Returns:
            bool: True nếu session thành công, False nếu thất bại

        Example:
            >>> bridge = OrchestrationBridge()
            >>> success = bridge.execute_session_sync(
            ...     worker_id=1,
            ...     target_url="https://outlook.live.com/...",
            ...     record_data={"email": "test@hotmail.com", "password": "pass123"},
            ...     proxy_pool=[{"server": "http://...", "username": "...", "password": "..."}]
            ... )
        """
        loop = self._get_or_create_loop()

        try:
            # Chạy async function và chờ kết quả
            result = loop.run_until_complete(
                asyncio.wait_for(
                    self.orchestrator.execute_robust_session(
                        worker_id=worker_id,
                        target_url=target_url,
                        record_data=record_data,
                        proxy_pool=proxy_pool
                    ),
                    timeout=timeout_sec
                )
            )
            return result

        except asyncio.TimeoutError:
            logging.error(f"[W{worker_id}] Session timeout sau {timeout_sec}s")
            return False
        except Exception as e:
            logging.error(f"[W{worker_id}] Lỗi khi thực thi session: {e}")
            return False

    def reset_circuit_breaker(self):
        """Đặt lại circuit breaker nếu bị kích hoạt"""
        self.orchestrator.reset_circuit_breaker()
        logging.info("Circuit breaker đã được reset")

    def cleanup_sandbox(self, sandbox_path):
        """Xóa sandbox cụ thể"""
        self.orchestrator._cleanup_old_sandbox(sandbox_path)

    def cleanup_all_sandboxes(self):
        """Xóa tất cả sandbox (gọi khi shutdown)"""
        self.orchestrator.cleanup_all_sandboxes()
        logging.info("Đã cleanup tất cả sandbox")

    def get_status(self):
        """Lấy trạng thái hiện tại của orchestrator"""
        return {
            "circuit_open": self.orchestrator.circuit_open,
            "failure_counter": self.orchestrator.failure_counter,
            "sandbox_dir": str(self.orchestrator.sandbox_base_path)
        }


# ============ USAGE EXAMPLES ============

def example_standalone():
    """Ví dụ: Sử dụng OrchestrationBridge standalone"""
    bridge = OrchestrationBridge({
        "max_exception_retries": 3,
        "bot_protection_wait": 5.0,
        "headless": False,
        "sandbox_base_dir": "./sandboxes"
    })

    proxy_pool = [
        {
            "server": "http://proxy1.com:8080",
            "username": "user1",
            "password": "pass1"
        }
    ]

    record = {
        "email": "test@example.com",
        "password": "secure_password"
    }

    success = bridge.execute_session_sync(
        worker_id=1,
        target_url="https://login.live.com/oauth20_authorize.srf?...",
        record_data=record,
        proxy_pool=proxy_pool,
        timeout_sec=120
    )

    print(f"Kết quả: {'Thành công' if success else 'Thất bại'}")

    # Cleanup
    bridge.cleanup_all_sandboxes()


def example_with_bot_outlook():
    """
    Ví dụ: Tích hợp vào bot_outlook.py

    Thêm code này vào PlaywrightWorkerController.register():
    """

    # Khởi tạo bridge (singleton)
    bridge = OrchestrationBridge()

    # Dữ liệu
    email = "test@hotmail.com"
    password = "SecurePass123"
    worker_id = 1
    proxy = {"server": "http://proxy.com:8080", "username": "user", "password": "pass"}

    # Chuẩn bị proxy pool
    proxy_pool = []
    if proxy:
        proxy_pool = [proxy]

    # Gọi orchestrator
    record_data = {"email": email, "password": password}
    success = bridge.execute_session_sync(
        worker_id=worker_id,
        target_url="https://login.live.com/oauth20_authorize.srf?...",
        record_data=record_data,
        proxy_pool=proxy_pool if proxy_pool else None,
        timeout_sec=120
    )

    if success:
        print(f"[+] Đăng ký {email} thành công")
        # Lưu vào file, xử lý tiếp...
    else:
        print(f"[-] Đăng ký {email} thất bại")

    # Cleanup khi hoàn tất tất cả workers
    bridge.cleanup_all_sandboxes()


def example_with_threading():
    """
    Ví dụ: Sử dụng trong multi-threading (như bot_outlook.py hiện tại)

    OrchestrationBridge tự động xử lý thread-safety
    """
    import threading

    bridge = OrchestrationBridge()

    def worker_thread(worker_id):
        """Mỗi worker thread gọi orchestrator"""
        email = f"worker{worker_id}@hotmail.com"
        password = "Password123"

        record_data = {"email": email, "password": password}
        success = bridge.execute_session_sync(
            worker_id=worker_id,
            target_url="https://outlook.live.com/...",
            record_data=record_data,
            proxy_pool=None,
            timeout_sec=120
        )

        print(f"[W{worker_id}] Result: {success}")

        # Kiểm tra circuit breaker
        status = bridge.get_status()
        if status["circuit_open"]:
            print(f"[W{worker_id}] Circuit breaker mở, ngừng worker")
            return

    # Chạy 3 workers song song
    threads = []
    for i in range(1, 4):
        t = threading.Thread(target=worker_thread, args=(i,))
        threads.append(t)
        t.start()

    # Chờ tất cả hoàn tất
    for t in threads:
        t.join()

    # Cleanup
    bridge.cleanup_all_sandboxes()


if __name__ == "__main__":
    # Chạy ví dụ
    print("=== Ví dụ Standalone ===")
    example_standalone()

    print("\n=== Ví dụ Tích Hợp ===")
    example_with_bot_outlook()

    print("\n=== Ví dụ Multi-Threading ===")
    example_with_threading()
