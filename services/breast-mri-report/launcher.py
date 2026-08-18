# services/breast-mri-report/launcher.py
"""
乳腺 MRI 报告服务生命周期管理模块。

本模块供主项目启动脚本（如 server.py 或顶层 start 脚本）import 使用，
封装 Node 子进程的启动、就绪检测、停止和异常处理。

用法示例:
    from services.breast_mri_report.launcher import BreastMRILauncher

    launcher = BreastMRILauncher()
    launcher.start()
    # ... 主项目运行 ...
    launcher.stop()
"""

import subprocess
import sys
import socket
import time
import os
import json
import threading
import logging
import urllib.request
import urllib.error
from pathlib import Path

logger = logging.getLogger(__name__)

SERVICE_DIR = Path(__file__).resolve().parent
MAIN_PROJECT_ROOT = SERVICE_DIR.parent.parent

STARTUP_TIMEOUT_SEC = 15
READY_POLL_INTERVAL_SEC = 0.5


class BreastMRILauncher:
    """管理 breast-mri-report Node 服务的启动、就绪检测和停止。"""

    def __init__(self, host=None, port=None):
        try:
            sys.path.insert(0, str(MAIN_PROJECT_ROOT))
            from app.config import get_breast_mri_host, get_breast_mri_port
        except ImportError as e:
            raise RuntimeError(
                "无法加载主项目配置模块，请确保在项目根目录下运行"
            ) from e

        self._host = host or get_breast_mri_host()
        self._port = port or get_breast_mri_port()
        self._proc = None
        self._stdout_thread = None
        self._running = False

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    @property
    def host(self):
        return self._host

    @property
    def port(self):
        return self._port

    @property
    def base_url(self):
        return f"http://{self._host}:{self._port}"

    @property
    def is_running(self):
        if self._proc is None:
            return False
        return self._proc.poll() is None

    def start(self):
        """启动 Node 服务，阻塞直到就绪或超时。"""
        if self.is_running:
            logger.info("breast-mri-report 已在运行: %s", self.base_url)
            return

        self._check_node()

        if self._port_in_use(self._host, self._port):
            if self._verify_service():
                logger.info("breast-mri-report 已在运行（端口 %s 已占用）: %s", self._port, self.base_url)
                return
            else:
                raise RuntimeError(
                    f"BREAST_MRI_PORT_CONFLICT: 端口 {self._port} 已被其他程序占用，"
                    f"请释放该端口后重试。"
                )

        env = self._build_env()
        self._start_process(env)
        self._wait_ready()
        self._running = True
        logger.info("breast-mri-report 已就绪: %s", self.base_url)

    def stop(self, timeout=5):
        """停止 Node 服务。"""
        if self._proc is None:
            return
        logger.info("正在停止 breast-mri-report...")
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                logger.warning("breast-mri-report 未在 %ss 内退出，强制杀死", timeout)
                self._proc.kill()
                self._proc.wait()
        self._proc = None
        self._running = False
        logger.info("breast-mri-report 已停止")

    def health_check(self) -> bool:
        """快速检查 Node 服务是否可达。"""
        return self._port_in_use(self._host, self._port)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _check_node(self):
        try:
            result = subprocess.run(
                ["node", "--version"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                logger.info("Node.js 已安装: %s", result.stdout.strip())
                return
        except FileNotFoundError:
            pass
        except Exception:
            pass
        raise RuntimeError("未检测到 Node.js，请先安装 Node.js (>=18)")

    def _build_env(self):
        env = os.environ.copy()
        env["PORT"] = str(self._port)
        env["HOST"] = self._host

        # 从主项目 config.py 读取 LLM 配置
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
            if base_url:
                env["VLLM_BASE_URL"] = base_url
            if model:
                env["VLLM_MODEL"] = model
            if api_key:
                env["VLLM_API_KEY"] = api_key
        except ImportError:
            logger.warning("无法导入主项目 config.py，将使用现有环境变量")
        return env

    def _start_process(self, env):
        logger.info("正在启动 breast-mri-report Node 进程...")
        self._proc = subprocess.Popen(
            ["node", "scripts/serve.mjs"],
            cwd=str(SERVICE_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        def _reader():
            try:
                for line in self._proc.stdout:
                    logger.debug("[breast-mri] %s", line.rstrip())
            except Exception:
                pass

        self._stdout_thread = threading.Thread(target=_reader, daemon=True)
        self._stdout_thread.start()

    def _wait_ready(self):
        deadline = time.time() + STARTUP_TIMEOUT_SEC
        while time.time() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError(
                    f"breast-mri-report Node 进程异常退出，退出码: {self._proc.returncode}"
                )
            if self._port_in_use(self._host, self._port):
                return
            time.sleep(READY_POLL_INTERVAL_SEC)
        raise TimeoutError(
            f"breast-mri-report 启动超时（{STARTUP_TIMEOUT_SEC}s），请检查端口 {self._port}"
        )

    @staticmethod
    def _port_in_use(host, port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            return sock.connect_ex((host, port)) == 0

    def _verify_service(self):
        """验证已占用端口上运行的是否为 breast-mri-report 服务。"""
        try:
            url = f"http://{self._host}:{self._port}/api/health"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("status") == "ok" and data.get("service") == "breast-mri-report"
        except Exception:
            return False