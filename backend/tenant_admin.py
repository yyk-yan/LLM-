"""
tenant_admin.py
租户与用户的认证 / 注册 / 管理路由。所有路由挂在 /api/auth 与 /api/admin 下。
权限规则：
- /api/auth/login           任何人
- /api/auth/register        任何人，但必须带有效的邀请码
- /api/auth/me              当前登录用户
- /api/auth/change-password 当前登录用户
- /api/admin/tenants            仅 super_admin
- /api/admin/invitations        super_admin 或 tenant_admin
- /api/admin/users              super_admin 或 tenant_admin（仅自己的租户）
"""
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from auth import (
    create_access_token, get_current_user, hash_password, require_role,
    verify_password, can_manage_tenant,
)
from db import (
    Invitation, Tenant, User, UserSetting, get_db, make_invitation,
    ROLE_MEMBER, ROLE_SUPER_ADMIN, ROLE_TENANT_ADMIN, ALL_ROLES,
)

router_auth = APIRouter(prefix="/api/auth", tags=["auth"])
router_admin = APIRouter(prefix="/api/admin", tags=["admin"])


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")


class LoginIn(BaseModel):
    username_or_email: str
    password: str


class RegisterIn(BaseModel):
    invitation_code: str
    username: str = Field(min_length=2, max_length=64)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6, max_length=128)


class CreateTenantIn(BaseModel):
    slug: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    admin_email: EmailStr  # 必填：创建租户时必须同时签发租户管理员邀请码


class CreateInvitationIn(BaseModel):
    tenant_id: Optional[int] = None  # tenant_admin 必须传自己租户；super_admin 可指定任意租户
    role: str = ROLE_MEMBER
    email: Optional[EmailStr] = None
    ttl_hours: int = Field(default=72, ge=1, le=24 * 30)


def _user_view(u: User) -> dict:
    return {
        "id": u.id,
        "tenant_id": u.tenant_id,
        "tenant_slug": u.tenant.slug if u.tenant else None,
        "tenant_name": u.tenant.name if u.tenant else None,
        "username": u.username,
        "email": u.email,
        "role": u.role,
        "is_active": u.is_active,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


def _invitation_view(inv: Invitation) -> dict:
    return {
        "id": inv.id,
        "code": inv.code,
        "tenant_id": inv.tenant_id,
        "tenant_slug": inv.tenant.slug if inv.tenant else None,
        "role": inv.role,
        "email": inv.email,
        "expires_at": inv.expires_at.isoformat() if inv.expires_at else None,
        "used_at": inv.used_at.isoformat() if inv.used_at else None,
        "is_valid": inv.is_valid,
    }


# ---------------- 认证相关 ----------------

@router_auth.post("/login")
def login(payload: LoginIn, db: Session = Depends(get_db)):
    q = db.query(User).filter(
        (User.username == payload.username_or_email) | (User.email == payload.username_or_email)
    )
    user = q.first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已被停用")
    token = create_access_token(user)
    return {"access_token": token, "token_type": "bearer", "user": _user_view(user)}


@router_auth.post("/register")
def register(payload: RegisterIn, db: Session = Depends(get_db)):
    inv = db.query(Invitation).filter_by(code=payload.invitation_code).first()
    if not inv or not inv.is_valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="邀请码无效或已过期")
    if inv.email and inv.email.lower() != payload.email.lower():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="邀请码与邮箱不匹配")

    if db.query(User).filter_by(email=payload.email).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该邮箱已注册")
    if db.query(User).filter_by(tenant_id=inv.tenant_id, username=payload.username).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该租户内用户名已被占用")

    user = User(
        tenant_id=inv.tenant_id,
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=inv.role,
        is_active=True,
    )
    db.add(user)
    inv.used_at = datetime.utcnow()
    db.flush()
    inv.used_by = user.id
    db.commit()
    db.refresh(user)
    token = create_access_token(user)
    return {"access_token": token, "token_type": "bearer", "user": _user_view(user)}


@router_auth.get("/me")
def me(user: User = Depends(get_current_user)):
    return _user_view(user)


@router_auth.post("/change-password")
def change_password(
    payload: ChangePasswordIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.old_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="原密码错误")
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"status": "ok"}


# ---------------- 个人 LLM 设置 ----------------

class LlmSettingIn(BaseModel):
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None  # 空字符串表示删除该字段；None 表示不变


def _setting_view(s: Optional[UserSetting]) -> dict:
    if not s:
        return {"model": "", "base_url": "", "api_key_set": False}
    return {
        "model": s.model or "",
        "base_url": s.base_url or "",
        # 不回显明文，只告诉前端是否已设置
        "api_key_set": bool(s.api_key),
    }


@router_auth.get("/llm-settings")
def get_llm_settings(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    s = db.query(UserSetting).filter_by(user_id=user.id).first()
    return _setting_view(s)


@router_auth.put("/llm-settings")
def put_llm_settings(
    payload: LlmSettingIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    s = db.query(UserSetting).filter_by(user_id=user.id).first()
    if not s:
        s = UserSetting(user_id=user.id)
        db.add(s)
    if payload.model is not None:
        s.model = payload.model.strip() or None
    if payload.base_url is not None:
        s.base_url = payload.base_url.strip() or None
    if payload.api_key is not None:
        # 空字符串明确表示删除已存的 key；其余情况下原样保存
        s.api_key = payload.api_key.strip() or None
    db.commit()
    db.refresh(s)
    return _setting_view(s)


# ---------------- 租户管理（super_admin） ----------------

@router_admin.get("/tenants")
def list_tenants(
    user: User = Depends(require_role(ROLE_SUPER_ADMIN)),
    db: Session = Depends(get_db),
):
    rows = db.query(Tenant).order_by(Tenant.id).all()
    return {
        "items": [
            {
                "id": t.id, "slug": t.slug, "name": t.name,
                "user_count": len(t.users),
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in rows
        ]
    }


@router_admin.post("/tenants")
def create_tenant(
    payload: CreateTenantIn,
    user: User = Depends(require_role(ROLE_SUPER_ADMIN)),
    db: Session = Depends(get_db),
):
    if not _SLUG_RE.match(payload.slug):
        raise HTTPException(status_code=400, detail="slug 必须是小写字母/数字/下划线/短横线，2-64 字符")
    if db.query(Tenant).filter_by(slug=payload.slug).first():
        raise HTTPException(status_code=400, detail="租户标识已存在")
    tenant = Tenant(slug=payload.slug, name=payload.name)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    invite = make_invitation(
        db, tenant_id=tenant.id, created_by=user.id,
        role=ROLE_TENANT_ADMIN, email=payload.admin_email,
    )
    return {
        "tenant": {"id": tenant.id, "slug": tenant.slug, "name": tenant.name},
        "admin_invitation": _invitation_view(invite),
    }


# ---------------- 邀请码（tenant_admin / super_admin） ----------------

@router_admin.post("/invitations")
def create_invitation(
    payload: CreateInvitationIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.role not in (ROLE_SUPER_ADMIN, ROLE_TENANT_ADMIN):
        raise HTTPException(status_code=403, detail="权限不足")

    target_tenant_id = payload.tenant_id or user.tenant_id
    if not can_manage_tenant(user, target_tenant_id):
        raise HTTPException(status_code=403, detail="无权为该租户签发邀请码")

    if payload.role not in ALL_ROLES:
        raise HTTPException(status_code=400, detail="无效角色")
    # tenant_admin 不得签发 super_admin 邀请
    if user.role == ROLE_TENANT_ADMIN and payload.role == ROLE_SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="租户管理员不可签发系统管理员邀请")

    if not db.query(Tenant).filter_by(id=target_tenant_id).first():
        raise HTTPException(status_code=404, detail="租户不存在")

    inv = make_invitation(
        db, tenant_id=target_tenant_id, created_by=user.id,
        role=payload.role, email=payload.email, ttl_hours=payload.ttl_hours,
    )
    return _invitation_view(inv)


@router_admin.get("/invitations")
def list_invitations(
    tenant_id: Optional[int] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.role not in (ROLE_SUPER_ADMIN, ROLE_TENANT_ADMIN):
        raise HTTPException(status_code=403, detail="权限不足")

    q = db.query(Invitation)
    if user.role == ROLE_TENANT_ADMIN:
        q = q.filter_by(tenant_id=user.tenant_id)
    elif tenant_id is not None:
        q = q.filter_by(tenant_id=tenant_id)
    return {"items": [_invitation_view(i) for i in q.order_by(Invitation.id.desc()).all()]}


# ---------------- 用户管理 ----------------

@router_admin.get("/users")
def list_users(
    tenant_id: Optional[int] = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.role not in (ROLE_SUPER_ADMIN, ROLE_TENANT_ADMIN):
        raise HTTPException(status_code=403, detail="权限不足")
    q = db.query(User)
    if user.role == ROLE_TENANT_ADMIN:
        q = q.filter_by(tenant_id=user.tenant_id)
    elif tenant_id is not None:
        q = q.filter_by(tenant_id=tenant_id)
    return {"items": [_user_view(u) for u in q.order_by(User.id).all()]}


class UpdateUserIn(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None


@router_admin.patch("/users/{user_id}")
def update_user(
    user_id: int,
    payload: UpdateUserIn,
    actor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if actor.role not in (ROLE_SUPER_ADMIN, ROLE_TENANT_ADMIN):
        raise HTTPException(status_code=403, detail="权限不足")
    target = db.query(User).filter_by(id=user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not can_manage_tenant(actor, target.tenant_id):
        raise HTTPException(status_code=403, detail="无权管理该用户")

    if payload.role is not None:
        if payload.role not in ALL_ROLES:
            raise HTTPException(status_code=400, detail="无效角色")
        if actor.role == ROLE_TENANT_ADMIN and payload.role == ROLE_SUPER_ADMIN:
            raise HTTPException(status_code=403, detail="租户管理员不可分配系统管理员角色")
        target.role = payload.role
    if payload.is_active is not None:
        if target.id == actor.id and payload.is_active is False:
            raise HTTPException(status_code=400, detail="不可停用自己")
        target.is_active = payload.is_active
    db.commit()
    db.refresh(target)
    return _user_view(target)
