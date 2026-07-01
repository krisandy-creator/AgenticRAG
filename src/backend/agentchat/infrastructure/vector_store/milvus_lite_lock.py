"""Milvus Lite 跨进程文件锁。

Windows 上 Milvus Lite 同一 db 文件同一时刻只允许一个进程打开；
API 与 ARQ Worker 并存时需通过文件锁串行访问，并在操作后释放连接。
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def milvus_lite_lock(lock_path: Path, *, timeout: float = 60.0):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "a+b")
    deadline = time.monotonic() + max(timeout, 1.0)
    acquired = False

    try:
        while time.monotonic() < deadline:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                time.sleep(0.2)

        if not acquired:
            raise TimeoutError(
                f"Milvus Lite 文件锁获取超时（{timeout}s）：{lock_path}。"
                "可能有多个 Python 进程同时访问 Milvus Lite；"
                "Windows 上请确保操作完成后释放连接，或改用 standalone Milvus。"
            )
        yield
    finally:
        if acquired:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        lock_file.close()
