"""Report 工作台登录鉴权：用户存储、会话 Cookie、注册/登录/登出/me 端点与权限依赖。

- 用户存 data/users.json（加盐 SHA256），会话存 data/auth_sessions.json（7 天过期，HttpOnly Cookie 下发）
- 内置管理员 admin / Rimag2026@123（首次启动自动建种子，role=admin，不可注册同名）
- 普通用户可注册登录（role=user）；前端按角色隐藏左侧边栏与管理功能
"""

import hashlib
import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/report-workbench/auth", tags=["report-workbench-auth"])

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_USERS_FILE = _DATA_DIR / "users.json"
_SESSIONS_FILE = _DATA_DIR / "auth_sessions.json"
_SESSION_TTL = 7 * 24 * 3600  # 会话 7 天过期
_COOKIE_NAME = "rw_token"

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_一-龥]{2,20}$")

_ADMIN_SEED = {
    "username": "admin",
    "password": "Rimag2026@123",
    "role": "admin",
    "display_name": "管理员",
}


class AuthCredential(BaseModel):
    username: str
    password: str
    display_name: str = ""


def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}::{password}".encode("utf-8")).hexdigest()


def _public_user(user: dict) -> dict:
    return {
        "username": user.get("username", ""),
        "role": user.get("role", "user"),
        "display_name": user.get("display_name", user.get("username", "")),
    }


def _load_users() -> list:
    if not _USERS_FILE.exists():
        return []
    try:
        with open(_USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"读取用户表失败: {e}")
        return []


def _write_users(users: list):
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def _ensure_seed() -> list:
    """确保内置管理员存在（首次启动建种子）"""
    users = _load_users()
    if not any(u.get("username") == _ADMIN_SEED["username"] for u in users):
        salt = secrets.token_hex(8)
        users.insert(
            0,
            {
                "username": _ADMIN_SEED["username"],
                "salt": salt,
                "password_hash": _hash_password(_ADMIN_SEED["password"], salt),
                "role": _ADMIN_SEED["role"],
                "display_name": _ADMIN_SEED["display_name"],
                "created_at": time.time(),
            },
        )
        _write_users(users)
        logger.info("已初始化内置管理员账号 admin")
    return users


def _load_sessions() -> dict:
    if not _SESSIONS_FILE.exists():
        return {}
    try:
        with open(_SESSIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_sessions(sessions: dict):
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)


def _create_session(user: dict) -> str:
    sessions = _load_sessions()
    now = time.time()
    sessions = {t: s for t, s in sessions.items() if s.get("expires", 0) > now}
    token = secrets.token_hex(32)
    sessions[token] = {
        "username": user.get("username", ""),
        "role": user.get("role", "user"),
        "display_name": user.get("display_name", user.get("username", "")),
        "expires": now + _SESSION_TTL,
    }
    _write_sessions(sessions)
    return token


def _drop_session(token: str):
    sessions = _load_sessions()
    if token in sessions:
        sessions.pop(token, None)
        _write_sessions(sessions)


def _session_user(request: Request) -> Optional[dict]:
    token = request.cookies.get(_COOKIE_NAME, "")
    if not token:
        return None
    s = _load_sessions().get(token)
    if not s or s.get("expires", 0) <= time.time():
        return None
    return s


def _auth_response(user: dict) -> JSONResponse:
    token = _create_session(user)
    resp = JSONResponse({"ok": True, "user": _public_user(user)})
    resp.set_cookie(_COOKIE_NAME, token, max_age=_SESSION_TTL, httponly=True, samesite="lax")
    return resp


# ==================== 权限依赖（供 router.py 挂载） ====================


async def require_user(request: Request) -> dict:
    """登录即可访问"""
    user = _session_user(request)
    if not user:
        raise HTTPException(401, "未登录或登录已过期，请重新登录")
    return user


async def require_admin(request: Request) -> dict:
    """仅管理员可访问"""
    user = await require_user(request)
    if user.get("role") != "admin":
        raise HTTPException(403, "该功能仅管理员可用")
    return user


# ==================== 端点 ====================


@router.post("/register")
async def register(payload: AuthCredential):
    username = payload.username.strip()
    password = payload.password or ""
    if not _USERNAME_RE.match(username):
        raise HTTPException(400, "用户名需为 2-20 位中英文、数字或下划线")
    if len(password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    users = _ensure_seed()
    if any(u.get("username") == username for u in users):
        raise HTTPException(409, "用户名已存在，请直接登录")
    salt = secrets.token_hex(8)
    user = {
        "username": username,
        "salt": salt,
        "password_hash": _hash_password(password, salt),
        "role": "user",
        "display_name": (payload.display_name.strip() or username)[:20],
        "created_at": time.time(),
    }
    users.append(user)
    _write_users(users)
    logger.info(f"新用户注册: {username}")
    return _auth_response(user)


@router.post("/login")
async def login(payload: AuthCredential):
    username = payload.username.strip()
    users = _ensure_seed()
    user = next((u for u in users if u.get("username") == username), None)
    if not user or _hash_password(payload.password or "", user.get("salt", "")) != user.get("password_hash"):
        raise HTTPException(401, "用户名或密码错误")
    return _auth_response(user)


@router.post("/logout")
async def logout(request: Request):
    token = request.cookies.get(_COOKIE_NAME, "")
    if token:
        _drop_session(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(_COOKIE_NAME)
    return resp


@router.get("/me")
async def me(request: Request):
    user = _session_user(request)
    if not user:
        raise HTTPException(401, "未登录或登录已过期")
    return {"ok": True, "user": user}


# 首次 import 即建种子账号，保证 admin 始终可用
_ensure_seed()
