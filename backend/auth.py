"""
auth.py
JWT 认证 + 权限依赖。
- hash_password / verify_password：bcrypt 哈希（直接使用 bcrypt 库，避开 passlib 与新版 bcrypt 的兼容问题）
- create_access_token / decode_token：JWT 签发与校验
- get_current_user：FastAPI 依赖，校验请求头里的 Bearer token，返回 User
- require_role：角色守卫工厂
"""
import os
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from db import (
    User, Tenant, get_db,
    ROLE_SUPER_ADMIN, ROLE_TENANT_ADMIN, ROLE_MEMBER,
)

JWT_SECRET = os.environ.get("JWT_SECRET", "change-me-in-production")
JWT_ALG = "HS256"
JWT_TTL_HOURS = int(os.environ.get("JWT_TTL_HOURS", "12"))

# bcrypt 输入硬上限 72 字节，超出会抛错；先按字节截断再哈希，行为可预测
_BCRYPT_MAX_BYTES = 72


def _normalize_password(plain: str) -> bytes:
    data = plain.encode("utf-8")
    if len(data) > _BCRYPT_MAX_BYTES:
        data = data[:_BCRYPT_MAX_BYTES]
    return data


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_normalize_password(plain), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_normalize_password(plain), hashed.encode("utf-8"))
    except Exception:
        return False


def create_access_token(user: User) -> str:
    now = datetime.utcnow()
    payload = {
        "sub": str(user.id),
        "tid": user.tenant_id,
        "role": user.role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=JWT_TTL_HOURS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录")
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的访问令牌")


def get_current_user(
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少访问令牌")
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_token(token)
    user_id = int(payload.get("sub", 0))
    user = (
        db.query(User)
        .options(joinedload(User.tenant))
        .filter_by(id=user_id)
        .first()
    )
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不存在或已停用")
    return user


def require_role(*roles: str):
    """依赖工厂：限制只有指定角色可访问"""
    def _checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        return user
    return _checker


def can_manage_tenant(actor: User, tenant_id: int) -> bool:
    """超级管理员可管理所有租户；租户管理员只能管理自己的租户"""
    if actor.role == ROLE_SUPER_ADMIN:
        return True
    if actor.role == ROLE_TENANT_ADMIN and actor.tenant_id == tenant_id:
        return True
    return False
