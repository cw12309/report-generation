"""报告路由解析器"""

import logging
import re
from pathlib import Path
from typing import Optional

import yaml

from .schemas import RouteInfo

logger = logging.getLogger(__name__)

# 模态标准化映射
MODALITY_ALIASES = {
    "ct": "CT",
    "增强ct": "CT",
    "头颅ct": "CT",
    "mri": "MRI",
    "mr": "MRI",
    "磁共振": "MRI",
    "超声": "US",
    "x线": "XR",
    "dr": "XR",
}

# 部位标准化映射
BODY_PART_ALIASES = {
    "头颅": "颅脑",
    "脑": "颅脑",
    "颈椎": "颈椎",
    "胸椎": "胸椎",
    "腰椎": "腰椎",
    "腰骶椎": "腰椎",
    "乳腺": "乳腺",
    "乳房": "乳腺",
    "心脏": "心脏",
}


class RouteResolver:
    """报告路由解析器"""

    def __init__(self, config_path: Optional[Path] = None):
        if config_path is None:
            config_path = Path(__file__).parent / "prompts" / "routes.yml"
        
        self.config_path = config_path
        self.routes: list[RouteInfo] = []
        self._load_routes()

    def reload(self):
        """重新加载路由配置（路由管理保存后调用）"""
        self.routes = []
        self._load_routes()

    def _load_routes(self):
        """加载路由配置"""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            
            for route_data in config.get("routes", []):
                route = RouteInfo(**route_data)
                if route.enabled:
                    self.routes.append(route)
            
            logger.info(f"加载了 {len(self.routes)} 个报告路由")
        except Exception as e:
            logger.error(f"加载路由配置失败: {e}", exc_info=True)
            raise

    def resolve(
        self,
        input_text: str = "",
        route_id: Optional[str] = None,
        modality: Optional[str] = None,
        body_part: Optional[str] = None,
    ) -> Optional[RouteInfo]:
        """解析报告路由
        
        Args:
            input_text: 用户输入文本
            route_id: 显式指定的路由ID
            modality: 检查类型
            body_part: 检查部位
            
        Returns:
            匹配的路由信息，无法匹配时返回None
        """
        # 1. 显式指定路由ID
        if route_id:
            for route in self.routes:
                if route.id == route_id:
                    logger.info(f"使用显式指定的路由: {route.name}")
                    return route

        # 2. 从输入文本中提取检查类型和部位
        if not modality or not body_part:
            extracted_modality, extracted_body_part = self._extract_from_text(input_text)
            modality = modality or extracted_modality
            body_part = body_part or extracted_body_part

        # 3. 标准化
        modality = self._normalize_modality(modality) if modality else None
        body_part = self._normalize_body_part(body_part) if body_part else None

        logger.info(f"解析路由: modality={modality}, body_part={body_part}")

        # 4. 匹配路由
        # 优先匹配检查类型+部位
        if modality and body_part:
            for route in self.routes:
                if route.id == "generic":
                    continue
                if self._match_route(route, modality, body_part):
                    logger.info(f"匹配到路由: {route.name}")
                    return route

        # 5. 只匹配部位
        if body_part:
            for route in self.routes:
                if route.id == "generic":
                    continue
                if body_part in route.body_parts or any(
                    alias in body_part or body_part in alias
                    for alias in route.aliases
                ):
                    logger.info(f"通过部位匹配到路由: {route.name}")
                    return route

        # 6. 回退到通用模板
        for route in self.routes:
            if route.id == "generic":
                logger.warning("无法精确匹配，使用通用模板")
                return route

        return None

    def _extract_from_text(self, text: str) -> tuple[Optional[str], Optional[str]]:
        """从文本中提取检查类型和部位"""
        modality = None
        body_part = None

        text_lower = text.lower()

        # 提取检查类型
        if "mri" in text_lower or "mr" in text_lower or "磁共振" in text:
            modality = "MRI"
        elif "ct" in text_lower:
            modality = "CT"
        elif "超声" in text or "us" in text_lower:
            modality = "US"

        # 提取部位（更宽松的匹配）
        if "腰椎" in text or "腰骶椎" in text or "腰" in text and ("椎" in text or "4" in text or "5" in text or "L" in text):
            body_part = "腰椎"
        elif "颈椎" in text or "颈" in text and ("椎" in text or "C" in text):
            body_part = "颈椎"
        elif "胸椎" in text or "胸" in text and ("椎" in text or "T" in text):
            body_part = "胸椎"
        elif "颅脑" in text or "头颅" in text or "脑" in text and ("梗" in text or "出血" in text or "占位" in text):
            body_part = "颅脑"
        elif ("乳腺" in text or "乳房" in text or "左乳" in text or "右乳" in text
              or "双乳" in text or "乳晕" in text or "bi-rads" in text_lower
              or "birads" in text_lower):
            body_part = "乳腺"
        elif "心脏" in text or "心肌" in text:
            body_part = "心脏"

        # 新增路由（硬编码规则未覆盖）：动态扫描各路由 body_parts/aliases，长关键词优先
        matched_modalities: list[str] = []
        if not body_part:
            for route in self.routes:
                if route.id == "generic":
                    continue
                cands = sorted(
                    {c for c in list(route.body_parts) + list(route.aliases) if c},
                    key=len,
                    reverse=True,
                )
                if any(c in text for c in cands):
                    body_part = route.body_parts[0] if route.body_parts else cands[0]
                    matched_modalities = list(route.modalities)
                    break

        # 如果没有明确指定modality，按命中路由的检查类型兜底（默认MRI）
        if not modality and body_part:
            modality = matched_modalities[0] if matched_modalities else "MRI"

        return modality, body_part

    def _normalize_modality(self, modality: str) -> str:
        """标准化检查类型"""
        modality_lower = modality.lower()
        return MODALITY_ALIASES.get(modality_lower, modality.upper())

    def _normalize_body_part(self, body_part: str) -> str:
        """标准化检查部位"""
        return BODY_PART_ALIASES.get(body_part, body_part)

    def _match_route(self, route: RouteInfo, modality: str, body_part: str) -> bool:
        """匹配路由"""
        # 匹配检查类型
        modality_match = False
        if not route.modalities:
            modality_match = True
        else:
            for route_modality in route.modalities:
                if modality.upper() == route_modality.upper():
                    modality_match = True
                    break

        if not modality_match:
            return False

        # 匹配部位
        body_part_match = False
        if not route.body_parts:
            body_part_match = True
        else:
            for route_body_part in route.body_parts:
                if body_part == route_body_part or body_part in route_body_part:
                    body_part_match = True
                    break

        return body_part_match

    def get_route_by_id(self, route_id: str) -> Optional[RouteInfo]:
        """通过ID获取路由"""
        for route in self.routes:
            if route.id == route_id:
                return route
        return None

    def list_routes(self) -> list[RouteInfo]:
        """列出所有已启用路由（含通用路由；前端模板直选自行过滤 generic）"""
        return list(self.routes)
