"""短期记忆管理器 (Short-Term Memory / Working Memory)

负责维护当前会话的对话历史和摘要，解决"刚才说了什么"的问题。

设计决策：
- 存储：内存 dict（重启丢失）
- 历史长度：最近 N 轮（默认 6 轮）
- 会话标识：session_id 字符串，由调用方传入
- 定位：纯对话历史管理，实体追踪已拆分至 EntityTracker
- 智能摘要：淘汰旧轮次时自动压缩为摘要句，防止关键信息丢失
- 线程安全：RLock 保护
"""

import logging
import threading
import time
from collections import OrderedDict
from typing import Callable

logger = logging.getLogger(__name__)

from prompt import load_prompt

SUMMARIZE_PROMPT = load_prompt("memory_prompt/summarize")


class ShortTermMemory:
    """短期记忆 —— 纯对话历史与摘要管理"""

    def __init__(self, max_rounds: int = 6, summarize_fn: Callable[[list[dict]], str] = None):
        self._sessions: dict[str, OrderedDict] = {}
        self._counters: dict[str, int] = {}
        self._summaries: dict[str, list[str]] = {}
        self._lock = threading.RLock()
        self.max_rounds = max_rounds
        self._summarize_fn = summarize_fn

    # ── 对话管理 ──────────────────────────────────────────

    def add_turn(self, session_id: str, user_msg: str, assistant_msg: str):
        """添加一轮对话"""
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = OrderedDict()
                self._counters[session_id] = 0
                self._summaries[session_id] = []

            self._counters[session_id] += 1
            turn_num = self._counters[session_id]
            turn_key = f"turn_{turn_num}"

            turns = self._sessions[session_id]
            turns[turn_key] = {
                "user": user_msg,
                "assistant": assistant_msg,
                "timestamp": time.time(),
            }

            while len(turns) > self.max_rounds:
                key, evicted_turn = turns.popitem(last=False)
                self._evict_and_summarize(session_id, evicted_turn)

    def _evict_and_summarize(self, session_id: str, turn: dict):
        """淘汰旧轮次并生成摘要"""
        summary = self._summarize_turn(turn)
        if summary:
            self._summaries[session_id].append(summary)
            logger.debug("摘要已生成: %s", summary[:60])

    def _summarize_turn(self, turn: dict) -> str:
        """生成单轮摘要，优先使用 LLM，回退到规则"""
        if self._summarize_fn is not None:
            try:
                messages = [
                    {"role": "system", "content": SUMMARIZE_PROMPT},
                    {"role": "user", "content": f"用户: {turn['user']}\n助手: {turn['assistant']}"},
                ]
                return self._summarize_fn(messages)
            except Exception:
                logger.debug("LLM 摘要失败，回退到规则摘要")
        return self._rule_based_summary(turn)

    @staticmethod
    def _rule_based_summary(turn: dict) -> str:
        """规则摘要：截断保留关键信息"""
        user = turn["user"][:80]
        assistant = turn["assistant"][:80]
        if len(turn["user"]) > 80:
            user += "..."
        if len(turn["assistant"]) > 80:
            assistant += "..."
        return f"用户: {user} | AI: {assistant}"

    # ── 读取接口 ──────────────────────────────────────────

    def get_history(self, session_id: str) -> list[dict]:
        """获取会话的完整对话历史"""
        with self._lock:
            turns = self._sessions.get(session_id)
            if not turns:
                return []
            messages = []
            for turn in turns.values():
                messages.append({"role": "user", "content": turn["user"]})
                messages.append({"role": "assistant", "content": turn["assistant"]})
            return messages

    def get_last_turn(self, session_id: str) -> dict | None:
        """获取最近一轮对话"""
        with self._lock:
            turns = self._sessions.get(session_id)
            if not turns:
                return None
            last_key = next(reversed(turns))
            return dict(turns[last_key])

    def get_summaries(self, session_id: str) -> list[str]:
        """获取已淘汰轮次的摘要列表"""
        with self._lock:
            return list(self._summaries.get(session_id, []))

    def build_messages(self, session_id: str, system_prompt: str, current_user_msg: str) -> list[dict]:
        """构建完整的 messages 列表（System + 摘要 + 历史 + 当前输入）"""
        messages = [{"role": "system", "content": system_prompt}]
        summaries = self._summaries.get(session_id, [])
        if summaries:
            summary_text = "## 历史对话摘要（已淘汰轮次的压缩）\n" + "\n".join(
                f"- {s}" for s in summaries
            )
            messages.append({"role": "system", "content": summary_text})
        history = self.get_history(session_id)
        messages.extend(history)
        messages.append({"role": "user", "content": current_user_msg})
        return messages

    # ── 生命周期 ──────────────────────────────────────────

    def clear(self, session_id: str):
        """清空会话的所有数据"""
        with self._lock:
            self._sessions.pop(session_id, None)
            self._counters.pop(session_id, None)
            self._summaries.pop(session_id, None)

    def active_sessions(self) -> int:
        """活跃会话数"""
        with self._lock:
            return len(self._sessions)

    def session_info(self, session_id: str) -> dict:
        """获取会话统计信息"""
        with self._lock:
            turns = self._sessions.get(session_id)
            counter = self._counters.get(session_id, 0)
            summaries = self._summaries.get(session_id, [])
            return {
                "session_id": session_id,
                "current_turns": len(turns) if turns else 0,
                "turns": len(turns) if turns else 0,
                "total_turns": counter,
                "max_rounds": self.max_rounds,
                "summary_count": len(summaries),
            }

    def cleanup_expired(self, max_age_seconds: int = 3600):
        """清理过期会话"""
        now = time.time()
        with self._lock:
            expired = []
            for sid, turns in self._sessions.items():
                if not turns:
                    expired.append(sid)
                    continue
                last_turn = next(reversed(turns.values()))
                if now - last_turn.get("timestamp", 0) > max_age_seconds:
                    expired.append(sid)
            for sid in expired:
                self._sessions.pop(sid, None)
                self._counters.pop(sid, None)
                self._summaries.pop(sid, None)
            return len(expired)