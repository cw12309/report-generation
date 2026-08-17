# d:\pythonProject\report_gen_structure\start.py
"""
乳腺 MRI 报告服务启动器
用法：python start.py [--port PORT] [--host HOST] [--open]
"""

import subprocess
import sys
import socket
import time
import os
import webbrowser
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 4173
DEFAULT_HOST = "127.0.0.1"
MAX_PORT_ATTEMPTS = 50


def check_node():
    try:
        result = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=5
        )
        version = result.stdout.strip()
        if result.returncode == 0:
            print(f"[OK] Node.js 已安装: {version}")
            return True
    except FileNotFoundError:
        pass
    except Exception as e:
        pass
    print("[ERROR] 未检测到 Node.js，请先安装 Node.js (>=18)")
    return False


def find_next_port(start_port):
    for offset in range(MAX_PORT_ATTEMPTS):
        port = start_port + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((DEFAULT_HOST, port)) != 0:
                return port
    print(f"[ERROR] 端口 {start_port}-{start_port+MAX_PORT_ATTEMPTS} 均被占用")
    return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description="乳腺 MRI 报告服务启动器")
    parser.add_argument("--port", "-p", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)))
    parser.add_argument("--host", type=str, default=os.environ.get("HOST", DEFAULT_HOST))
    parser.add_argument("--open", "-o", action="store_true", help="启动后自动打开浏览器")
    args = parser.parse_args()

    os.chdir(PROJECT_DIR)

    if not check_node():
        sys.exit(1)

    port = find_next_port(args.port)
    if port is None:
        sys.exit(1)

    if port != args.port:
        print(f"[INFO] 端口 {args.port} 已占用，自动使用端口 {port}")

    url = f"http://{args.host}:{port}"

    # 检查是否已运行
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        if sock.connect_ex((args.host, port)) == 0:
            print(f"[INFO] 服务已在运行: {url}")
            if args.open:
                webbrowser.open(url)
            return

    env = os.environ.copy()
    env["PORT"] = str(port)
    env["HOST"] = args.host

    print(f"[START] 正在启动服务...")
    proc = subprocess.Popen(
        ["node", "scripts/serve.mjs"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    # 等待服务就绪
    print("[WAIT] 等待服务就绪", end="", flush=True)
    for _ in range(30):
        time.sleep(0.5)
        print(".", end="", flush=True)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            if sock.connect_ex((args.host, port)) == 0:
                print(f"\n[READY] 服务已启动: {url}")
                if args.open:
                    webbrowser.open(url)
                print("[INFO] 按 Ctrl+C 停止服务")
                try:
                    # 持续输出服务日志
                    while True:
                        line = proc.stdout.readline()
                        if not line and proc.poll() is not None:
                            break
                        if line:
                            print(f"[SERVER] {line.rstrip()}")
                except KeyboardInterrupt:
                    print("\n[STOP] 正在停止服务...")
                    proc.terminate()
                    proc.wait(timeout=5)
                    print("[STOP] 服务已停止")
                return

    print(f"\n[WARN] 服务启动超时，但进程仍在运行")
    print(f"[INFO] 请手动访问: {url}")
    if args.open:
        webbrowser.open(url)


if __name__ == "__main__":
    main()