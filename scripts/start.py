import glob
import os
import platform
import signal
import socket
import subprocess
import sys
import time


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
BASE_DIR = os.path.join(PROJECT_ROOT, "src")

BACKEND_DIR = os.path.join(BASE_DIR, "backend")
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
BACKEND_PORT = 7860
FRONTEND_PORT = 8090

IS_WINDOWS = platform.system() == "Windows"
processes: list[subprocess.Popen] = []


def install_dependencies():
    """安装项目根目录下的 requirements.txt。"""
    print(f"项目根目录：{PROJECT_ROOT}")
    print("[Step 1] 检查依赖文件...")

    req_files = glob.glob(os.path.join(PROJECT_ROOT, "request*.txt"))
    if not req_files:
        req_files = glob.glob(os.path.join(PROJECT_ROOT, "requirements.txt"))

    if not req_files:
        print(f"警告：未在 {PROJECT_ROOT} 找到 requirements.txt，跳过依赖安装。")
        return

    req_file = req_files[0]
    print(f"发现依赖文件：{req_file}")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", req_file], check=True)
    except subprocess.CalledProcessError:
        print("依赖安装失败，启动终止。")
        sys.exit(1)


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def print_windows_port_usage(port: int):
    if not IS_WINDOWS:
        return
    print(f"端口 {port} 当前占用：")
    result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, check=False)
    for line in result.stdout.splitlines():
        if f":{port}" in line and "LISTENING" in line.upper():
            print(line)


def ensure_ports_free():
    busy_ports = [
        (name, port)
        for name, port in (("后端", BACKEND_PORT), ("前端", FRONTEND_PORT))
        if is_port_in_use(port)
    ]
    if not busy_ports:
        return

    for name, port in busy_ports:
        print(f"错误：{name}端口 {port} 已被占用，请先停止旧服务后再启动。")
        print_windows_port_usage(port)
    sys.exit(1)


def popen_service(args: list[str], cwd: str, shell: bool = False):
    kwargs = {"cwd": cwd, "shell": shell}
    if not IS_WINDOWS:
        kwargs["start_new_session"] = True
    return subprocess.Popen(args, **kwargs)


def ensure_process_running(process: subprocess.Popen, name: str, delay: float = 2.0):
    time.sleep(delay)
    if process.poll() is not None:
        raise RuntimeError(f"{name}启动失败，进程已退出。")


def terminate_process_tree(process: subprocess.Popen):
    if process.poll() is not None:
        return

    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return

    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        process.terminate()


def start_services():
    """并发启动后端、解析 Worker 和前端。"""
    try:
        ensure_ports_free()

        print(f"[Step 2] 启动后端 (cwd: {BACKEND_DIR})...")
        if not os.path.exists(BACKEND_DIR):
            print(f"错误：找不到后端目录 {BACKEND_DIR}")
            return

        backend_process = popen_service(
            ["uvicorn", "agentchat.main:app", "--port", str(BACKEND_PORT)],
            cwd=BACKEND_DIR,
        )
        processes.append(backend_process)
        ensure_process_running(backend_process, "后端")

        print(f"[Step 2b] 启动文档解析 Worker (cwd: {BACKEND_DIR})...")
        worker_process = popen_service(
            [sys.executable, "-m", "arq", "agentchat.workers.arq_worker.WorkerSettings"],
            cwd=BACKEND_DIR,
        )
        processes.append(worker_process)
        ensure_process_running(worker_process, "文档解析 Worker", delay=1.0)

        print(f"[Step 3] 启动前端 (cwd: {FRONTEND_DIR})...")
        if not os.path.exists(FRONTEND_DIR):
            print(f"错误：找不到前端目录 {FRONTEND_DIR}")
            return

        frontend_process = popen_service(
            ["npm", "run", "dev"],
            cwd=FRONTEND_DIR,
            shell=IS_WINDOWS,
        )
        processes.append(frontend_process)
        ensure_process_running(frontend_process, "前端")

        print("\n服务已启动，日志将混合显示在下方。")
        print("按 Ctrl+C 停止全部服务。\n")

        while True:
            time.sleep(1)
            if backend_process.poll() is not None:
                print("后端服务已退出。")
                break
            if frontend_process.poll() is not None:
                print("前端服务已退出。")
                break

    except KeyboardInterrupt:
        print("\n收到停止信号。")
    except RuntimeError as exc:
        print(f"错误：{exc}")
    finally:
        cleanup()


def cleanup():
    print("正在关闭后台服务...")
    for process in reversed(processes):
        terminate_process_tree(process)
    print("已退出。")


if __name__ == "__main__":
    if not os.path.exists(BASE_DIR):
        print(f"错误：找不到 src 目录 {BASE_DIR}")
        sys.exit(1)

    ensure_ports_free()
    install_dependencies()
    start_services()
