# Docker 部署已废弃此入口：Gateway 与 Worker 现在由 Compose 分别管理。
# 仅保留本脚本用于不使用 Docker 时的本地开发与调试。
"""Launch the loopback-only Worker, wait for readiness, then run the Gateway."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import uvicorn
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
WORKER_HEALTH_URL = "http://127.0.0.1:8000/health"


def _start_worker() -> subprocess.Popen:
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.chat.server:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]
    return subprocess.Popen(command, cwd=str(PROJECT_ROOT), env=os.environ.copy())


def _wait_for_worker(worker: subprocess.Popen) -> None:
    print("等待 Worker 完成模型与向量资源初始化...", flush=True)
    with httpx.Client(timeout=2.0, trust_env=False) as client:
        while True:
            return_code = worker.poll()
            if return_code is not None:
                raise RuntimeError(f"Worker 在就绪前退出，退出码: {return_code}")
            try:
                response = client.get(WORKER_HEALTH_URL)
                if response.status_code == 200:
                    print("Worker 已就绪，正在启动 API Gateway...", flush=True)
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)


def _stop_worker(worker: subprocess.Popen) -> None:
    if worker.poll() is not None:
        return
    worker.terminate()
    try:
        worker.wait(timeout=10)
    except subprocess.TimeoutExpired:
        worker.kill()
        worker.wait(timeout=5)


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    worker = _start_worker()
    try:
        _wait_for_worker(worker)
        uvicorn.run(
            "app.gateway:app",
            host="0.0.0.0",
            port=8001,
            log_level="info",
        )
    finally:
        _stop_worker(worker)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"启动失败: {exc}", file=sys.stderr)
        raise