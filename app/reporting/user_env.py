"""用户环境隔离层：每个登录用户在共享基线（routes.yml / templates.yml / prompts/*.md）之上
拥有私有覆写（data/user_envs/{username}.json），互不影响。

- routes：个人新增路由（完整 RouteInfo 字段 + prompt_md）与基线路由的字段/prompt 覆写
- templates：个人新增模板与基线模板的字段覆写、删除标记、启停覆写
- 生成/匹配链路通过 current_user() 上下文变量取当前用户，走合并视图；
  无用户上下文（CLI/评测/部署会话）时等同基线，行为不变
- 管理员面板通过 admin_snapshot() 只读查看各用户的覆写内容
"""

import contextvars
import json
import logging
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_ENV_DIR = _DATA_DIR / "user_envs"

# 当前请求用户上下文（router 端点入口 set，生成/匹配链路 get）
_current_user: contextvars.ContextVar[str] = contextvars.ContextVar("rw_user", default="")

_lock = threading.Lock()
# 内存缓存：{username: env_dict}，写操作后失效对应项
_cache: dict = {}


def set_current_user(username: str):
    _current_user.set(username or "")


def current_user() -> str:
    return _current_user.get()


def _env_file(username: str) -> Path:
    return _ENV_DIR / f"{username}.json"


def _empty_env() -> dict:
    return {"routes": {}, "templates": {}, "deleted_templates": []}


def load_env(username: str) -> dict:
    """读取用户环境（带内存缓存）；空用户名返回空环境（等同基线）"""
    if not username:
        return _empty_env()
    with _lock:
        if username in _cache:
            return _cache[username]
    env = _empty_env()
    p = _env_file(username)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                env["routes"] = data.get("routes") or {}
                env["templates"] = data.get("templates") or {}
                env["deleted_templates"] = data.get("deleted_templates") or []
        except Exception as e:
            logger.warning(f"读取用户环境失败 {username}: {e}")
    with _lock:
        _cache[username] = env
    return env


def save_env(username: str, env: dict):
    """写回用户环境并失效缓存"""
    if not username:
        return
    _ENV_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        _cache.pop(username, None)
        _env_file(username).write_text(json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8")


# ===== 路由合并视图 =====


def merged_routes(username: str, base_config: dict) -> dict:
    """基线 routes.yml + 个人覆写 → 合并配置（个人新增路由追加在末尾）"""
    env = load_env(username)
    overrides = env.get("routes") or {}
    merged = []
    for r in base_config.get("routes") or []:
        ov = overrides.get(r.get("id"))
        if ov:
            r = {**r, **{k: v for k, v in ov.items() if k != "prompt_md"}}
        merged.append(r)
    base_ids = {r.get("id") for r in merged}
    for rid, ov in overrides.items():
        if rid not in base_ids and isinstance(ov, dict) and ov.get("name"):
            merged.append({k: v for k, v in ov.items() if k != "prompt_md"})
    return {"routes": merged}


def get_prompt_override(username: str, route_id: str) -> Optional[str]:
    """个人路由提示词覆写（None=无覆写，走基线 md 文件）"""
    ov = (load_env(username).get("routes") or {}).get(route_id) or {}
    md = ov.get("prompt_md")
    return md if isinstance(md, str) and md.strip() else None


def upsert_route(username: str, route: dict, prompt_md: Optional[str]):
    """写入个人路由覆写/新增（route 含 id 与元信息字段）"""
    env = load_env(username)
    rid = str(route.get("id") or "").strip()
    entry = {k: v for k, v in route.items() if k != "prompt_md"}
    if prompt_md is not None:
        entry["prompt_md"] = prompt_md
    env.setdefault("routes", {})[rid] = entry
    save_env(username, env)


def toggle_route(username: str, route_id: str, base_enabled: bool) -> bool:
    """个人启停覆写：返回切换后的 enabled"""
    env = load_env(username)
    entry = env.setdefault("routes", {}).setdefault(route_id, {})
    cur = entry.get("enabled", base_enabled)
    entry["enabled"] = not bool(cur)
    save_env(username, env)
    return bool(entry["enabled"])


# ===== 模板合并视图 =====


def merged_templates(username: str, base_templates: list) -> list:
    """基线 templates.yml + 个人覆写 → 合并模板列表（个人新增追加末尾，个人删除生效）"""
    env = load_env(username)
    overrides = env.get("templates") or {}
    deleted = set(env.get("deleted_templates") or [])
    merged = []
    for t in base_templates:
        tid = t.get("id")
        if tid in deleted:
            continue
        ov = overrides.get(tid)
        merged.append({**t, **ov} if ov else t)
    base_ids = {t.get("id") for t in merged}
    for tid, ov in overrides.items():
        if tid not in base_ids and tid not in deleted and isinstance(ov, dict) and ov.get("name"):
            merged.append(ov)
    return merged


def upsert_template(username: str, tpl: dict):
    """写入个人模板覆写/新增"""
    env = load_env(username)
    tid = str(tpl.get("id") or "").strip()
    env.setdefault("templates", {})[tid] = tpl
    deleted = env.get("deleted_templates") or []
    if tid in deleted:
        deleted.remove(tid)
    save_env(username, env)


def delete_template(username: str, tid: str, is_base: bool):
    """删除模板：基线模板对个人打删除标记；个人新增模板直接移除覆写"""
    env = load_env(username)
    env.setdefault("templates", {}).pop(tid, None)
    if is_base:
        deleted = env.setdefault("deleted_templates", [])
        if tid not in deleted:
            deleted.append(tid)
    save_env(username, env)


def toggle_template(username: str, tid: str, base_enabled: bool) -> Optional[bool]:
    """个人启停覆写；返回切换后的 enabled（模板不存在返回 None）"""
    env = load_env(username)
    entry = env.setdefault("templates", {}).setdefault(tid, {})
    cur = entry.get("enabled", base_enabled)
    entry["enabled"] = not bool(cur)
    save_env(username, env)
    return bool(entry["enabled"])


def bump_usage(username: str, tid: str):
    """个人引用计数 +1"""
    env = load_env(username)
    entry = env.setdefault("templates", {}).setdefault(tid, {})
    entry["usage_count"] = int(entry.get("usage_count") or 0) + 1
    save_env(username, env)


# ===== 管理员视图 =====


def list_env_users() -> list:
    """所有拥有个人环境的用户名"""
    if not _ENV_DIR.exists():
        return []
    return sorted(p.stem for p in _ENV_DIR.glob("*.json"))


def admin_snapshot(username: str, scope: str = "changes") -> dict:
    """管理员查看某用户的路由/模板环境。

    scope="changes"：仅个人改动（覆写/新增/删除标记）；
    scope="all"：该用户生成时实际生效的全量合并视图（基线+个人），带来源标记。
    路由项含生效的专科 Prompt 全文，供前端预览。
    """
    from . import route_admin, template_admin

    base_config = route_admin.load_routes_raw()
    base_routes = base_config.get("routes") or []
    base_ids = {r.get("id") for r in base_routes}
    base_templates = template_admin.load_templates()
    base_tids = {t.get("id") for t in base_templates}
    env = load_env(username)
    my_routes = env.get("routes") or {}
    my_tpls = env.get("templates") or {}
    deleted = env.get("deleted_templates") or []

    routes = []
    for r in (merged_routes(username, base_config).get("routes") or []):
        rid = r.get("id")
        has_ov = rid in my_routes
        if scope == "changes" and not has_ov:
            continue
        base_r = next((b for b in base_routes if b.get("id") == rid), None)
        override = get_prompt_override(username, rid)
        prompt_md = override if override is not None else (route_admin.read_prompt(base_r) if base_r else "")
        routes.append({
            "id": rid,
            "name": r.get("name", ""),
            "enabled": bool(r.get("enabled", True)),
            "modalities": r.get("modalities") or [],
            "body_parts": r.get("body_parts") or [],
            "quick_tags": r.get("quick_tags") or [],
            "is_custom": rid not in base_ids,
            "has_override": has_ov,
            "prompt_md": prompt_md or "",
            "override_fields": {k: v for k, v in (my_routes.get(rid) or {}).items() if k != "prompt_md"} if has_ov else {},
        })

    templates = []
    for t in merged_templates(username, base_templates):
        tid = t.get("id")
        has_ov = tid in my_tpls
        if scope == "changes" and not has_ov:
            continue
        templates.append({**t, "is_custom": tid not in base_tids, "has_override": has_ov})

    return {
        "username": username,
        "scope": scope,
        "routes": routes,
        "templates": templates,
        "deleted_templates": deleted,
    }
