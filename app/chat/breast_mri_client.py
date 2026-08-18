# app/chat/breast_mri_client.py
"""
乳腺 MRI 报告工作台 Python 客户端。

封装对 breast-mri-report Node 服务的 HTTP 调用，提供：
- 健康检查（服务是否存活）
- 模型状态检查（LLM 是否连通）
- Agent 对话（结构化报告提取）

所有方法在 Node 服务不可用时返回降级结果，不抛出异常，
确保乳腺 MRI 作为可选内部服务，不影响主项目其他功能。
"""

import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

from config import get_breast_mri_base_url

DEFAULT_TIMEOUT = 10.0
AGENT_TIMEOUT = 90.0


class BreastMRIClient:
    """breast-mri-report Node 服务的 Python 客户端。"""

    def __init__(self, base_url: str = None):
        if base_url is None:
            base_url = get_breast_mri_base_url()
        self._base_url = base_url.rstrip("/")

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    async def health(self) -> Dict[str, Any]:
        """检查服务是否存活。"""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._base_url}/api/health",
                    timeout=DEFAULT_TIMEOUT,
                )
                return resp.json()
        except Exception as exc:
            logger.debug("breast-mri-report 健康检查失败: %s", exc)
            return {
                "status": "unavailable",
                "service": "breast-mri-report",
                "message": "乳腺 MRI 报告服务不可用",
            }

    async def model_status(self) -> Dict[str, Any]:
        """检查 LLM 模型连通性。"""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._base_url}/api/model-status",
                    timeout=DEFAULT_TIMEOUT,
                )
                return resp.json()
        except Exception as exc:
            logger.debug("breast-mri-report 模型状态检查失败: %s", exc)
            return {"online": False, "error": str(exc)}

    async def agent_turn(
        self,
        message: str,
        current_case: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """发送一轮 Agent 对话，返回结构化提取结果。"""
        payload = {
            "message": message,
            "currentCase": current_case or {},
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{self._base_url}/api/agent/turn",
                    json=payload,
                    timeout=AGENT_TIMEOUT,
                )
                return resp.json()
        except httpx.ConnectError:
            logger.warning("breast-mri-report Agent 调用失败：服务不可用")
            return {
                "error": "乳腺 MRI 报告服务不可用，请检查服务是否已启动",
                "fallbackAvailable": False,
            }
        except httpx.TimeoutException:
            logger.warning("breast-mri-report Agent 调用超时")
            return {
                "error": "乳腺 MRI 报告服务响应超时",
                "fallbackAvailable": False,
            }
        except Exception as exc:
            logger.error("breast-mri-report Agent 调用异常: %s", exc)
            return {
                "error": str(exc),
                "fallbackAvailable": False,
            }

    async def is_healthy(self) -> bool:
        """快速检查服务是否可达（返回 bool）。"""
        result = await self.health()
        return result.get("status") == "ok"


# 模块级单例，方便复用
_client: Optional[BreastMRIClient] = None


def get_breast_mri_client() -> BreastMRIClient:
    """获取模块级 BreastMRIClient 单例。"""
    global _client
    if _client is None:
        _client = BreastMRIClient()
    return _client