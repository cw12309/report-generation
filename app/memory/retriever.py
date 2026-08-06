"""记忆检索注入器 (优化版)

基于语义相关性的按需检索，从 STM（当前会话历史）中检索最相关的片段注入 Prompt。

性能优化：使用增量索引，每次只 Embedding 新增的消息，避免重复计算。

使用方式：
    retriever = MemoryRetriever(get_embedding)
    retriever.index_stm(stm.get_history(session_id))  # 增量索引历史
    results = retriever.search(query, top_k_stm=3)
    # results = {"stm": ["[user] 脑出血", ...]}
"""

import logging
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)


class MemoryRetriever:
    """轻量级内存向量检索器（增量索引版）

    复用现有 Embedding 接口，为 STM 对话历史建立向量索引，
    根据用户 Query 检索最相关的当前会话片段。

    增量策略：
    - STM: 根据已索引消息数判断新增项，仅 Embedding 新消息
    """

    def __init__(self, get_embedding_fn: Callable[[str], list[float]]):
        self._embed = get_embedding_fn
        self._stm_vectors: list[np.ndarray] = []
        self._stm_texts: list[str] = []

        # 去重：防止重复索引相同内容
        self._stm_set: set[str] = set()

    # ── STM 增量索引 ──────────────────────────────────────

    def index_stm(self, history: Optional[list[dict]] = None):
        """将 STM 对话历史转换为文本列表并向量化（仅追加新消息）。

        Args:
            history: stm.get_history(session_id) 的返回值，
                [{"role": "user", "content": "..."}, ...]
        """
        if not history:
            return

        # 根据已索引消息数判断新增项（history 按时间顺序排列）
        current_len = len(self._stm_texts)
        new_msgs = history[current_len:]

        if not new_msgs:
            return

        count = 0
        for msg in new_msgs:
            role = msg.get("role", "")
            content = msg.get("content", "").strip()
            if not content:
                continue

            # 截断过长内容，避免 Embedding 输入超限
            if len(content) > 500:
                content = content[:500]

            text = f"[{role}] {content}"
            if text not in self._stm_set:
                self._stm_set.add(text)
                self._stm_texts.append(text)
                self._stm_vectors.append(self._embed_text(text))
                count += 1

        if count > 0:
            logger.debug("STM 增量索引: 新增 %d 条，总计 %d 条", count, len(self._stm_texts))

    # ── 检索 ──────────────────────────────────────────────

    def search(self, query: str, top_k_stm: int = 3) -> dict:
        """检索与 Query 最相关的 STM 片段。

        Args:
            query: 用户当前输入（增强后的查询）
            top_k_stm: 返回的 STM 历史消息数量

        Returns:
            {"stm": ["[user] 脑出血", ...]}
        """
        query_vec = self._embed_text(query)

        stm_results = self._rank(query_vec, self._stm_vectors, self._stm_texts, top_k_stm)

        return {"stm": stm_results}

    def search_relevant(self, query: str, top_k_stm: int = 3) -> dict:
        """search() 的别名，语义更明确"""
        return self.search(query, top_k_stm=top_k_stm)

    # ── 内部方法 ──────────────────────────────────────────

    def _embed_text(self, text: str) -> np.ndarray:
        try:
            vec = self._embed(text)
            return np.array(vec, dtype=np.float32)
        except Exception:
            logger.warning("Embedding 失败，返回零向量: %s...", text[:50])
            return np.zeros(1024, dtype=np.float32)

    @staticmethod
    def _rank(
        query_vec: np.ndarray,
        vectors: list[np.ndarray],
        texts: list[str],
        top_k: int,
    ) -> list[str]:
        if not vectors:
            return []

        scored = []
        for vec, text in zip(vectors, texts):
            sim = MemoryRetriever._cosine_sim(query_vec, vec)
            scored.append((sim, text))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [text for sim, text in scored[:top_k] if sim > 0.3]

    @staticmethod
    def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denom < 1e-8:
            return 0.0
        return float(np.dot(a, b) / denom)
