"""启动 ARQ 文档解析 Worker。"""

import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
BACKEND_DIR = os.path.join(PROJECT_ROOT, "src", "backend")


def main() -> None:
    if not os.path.exists(BACKEND_DIR):
        print(f"找不到后端目录: {BACKEND_DIR}")
        sys.exit(1)

    command = [sys.executable, "-m", "arq", "agentchat.workers.arq_worker.WorkerSettings"]
    print(f"启动 ARQ 解析 Worker: {' '.join(command)}")
    print(f"工作目录: {BACKEND_DIR}")
    raise SystemExit(subprocess.call(command, cwd=BACKEND_DIR))


if __name__ == "__main__":
    main()
