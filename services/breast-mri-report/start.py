# services/breast-mri-report/start.py
"""
乳腺 MRI 报告服务启动器
用法：python start.py [--port PORT] [--host HOST] [--open]

配置来源：启动时自动从主项目 config.py 读取 Chat LLM 的 Base URL / Model / API Key，
通过环境变量注入 Node 进程，不依赖硬编码默认值。
"""

import subprocess
import sys
import socket
import time
import os
import webbrowser
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
MAX_PORT_ATTEMPTS = 50


def _load_main_config():
    """从主项目 config.py 读取 LLM 配置，返回 (base_url, model, api_key)。"""
    MAIN_PROJECT_ROOT = PROJECT_DIR.parent.parent
    sys.path.insert(0, str(MAIN_PROJECT_ROOT))
    try:
        from app.config import (
            get_breast_mri_llm_base_url,
            get_breast_mri_llm_model,
            get_breast_mri_llm_api_key,
        )
        base_url = get_breast_mri_llm_base_url()
        model = get_breast_mri_llm_model()
        api_key = get_breast_mri_llm_api_key()
        if not base_url:
            print("[WARN] 主项目未配置 LLM Base URL，乳腺 MRI 工作台的 Agent 功能将不可用")
        if not model:
            print("[WARN] 主项目未配置 LLM Model，乳腺 MRI 工作台的 Agent 功能将不可用")
        return base_url, model, api_key
    except ImportError as exc:
        print(f"[WARN] 无法导入主项目 config.py: {exc}")
        print("[WARN] 将使用环境变量 VLLM_BASE_URL / VLLM_MODEL（如果已设置）")
        return "", "", ""


def _load_breast_mri_config():
    """从主项目 config.py 读取 Breast MRI host/port 配置。"""
    MAIN_PROJECT_ROOT = PROJECT_DIR.parent.parent
    sys.path.insert(0, str(MAIN_PROJECT_ROOT))
    try:
        from app.config import get_breast_mri_host, get_breast_mri_port
        return get_breast_mri_host(), get_breast_mri_port()
    except ImportError:
        print("[ERROR] 无法加载主项目 Breast MRI 配置，请确保在项目根目录下运行")
        return None, None


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
    except Exception:
        pass
    print("[ERROR] 未检测到 Node.js，请先安装 Node.js (>=18)")
    return False


def find_next_port(host, start_port):
    for offset in range(MAX_PORT_ATTEMPTS):
        port = start_port + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) != 0:
                return port
    print(f"[ERROR] 端口 {start_port}-{start_port + MAX_PORT_ATTEMPTS} 均被占用")
    return None


def main():
    default_host, default_port = _load_breast_mri_config()
    if default_host is None or default_port is None:
        sys.exit(1)

    import argparse
    parser = argparse.ArgumentParser(description="乳腺 MRI 报告服务启动器")
    parser.add_argument("--port", "-p", type=int, default=int(os.environ.get("PORT", default_port)))
    parser.add_argument("--host", type=str, default=os.environ.get("HOST", default_host))
    parser.add_argument("--open", "-o", action="store_true", help="启动后自动打开浏览器")
    args = parser.parse_args()

    os.chdir(PROJECT_DIR)

    if not check_node():
        sys.exit(1)

    port = find_next_port(args.host, args.port)
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

    # 从主项目 config.py 读取 LLM 配置，注入 Node 环境变量
    base_url, model, api_key = _load_main_config()
    if base_url:
        env["VLLM_BASE_URL"] = base_url
    if model:
        env["VLLM_MODEL"] = model
    if api_key:
        env["VLLM_API_KEY"] = api_key

    print(f"[CONFIG] VLLM_BASE_URL = {env.get('VLLM_BASE_URL', '(未设置)')}")
    print(f"[CONFIG] VLLM_MODEL    = {env.get('VLLM_MODEL', '(未设置)')}")

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