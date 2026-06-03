"""
db.py
多租户数据模型与数据库初始化。
- Tenant：租户（组织），数据隔离的最外层边界
- User：用户，归属于唯一租户，拥有角色
- Invitation：邀请码，由租户管理员或超级管理员签发，用于注册新成员

角色（Role）：
- super_admin：系统级管理员，可创建租户、跨租户查看与签发邀请
- tenant_admin：租户管理员，仅在所属租户内管理成员与签发邀请
- member：普通成员，仅能使用自己上传的数据与生成的输出
"""
from __future__ import annotations
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, UniqueConstraint, create_engine
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, Session

ROLE_SUPER_ADMIN = "super_admin"
ROLE_TENANT_ADMIN = "tenant_admin"
ROLE_MEMBER = "member"
ALL_ROLES = (ROLE_SUPER_ADMIN, ROLE_TENANT_ADMIN, ROLE_MEMBER)

DEFAULT_TENANT_SLUG = "default"
DEFAULT_TENANT_NAME = "默认租户"

DB_FILE = os.path.join(os.path.dirname(__file__), "auth.db")
DB_URL = f"sqlite:///{DB_FILE}"

engine = create_engine(
    DB_URL,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(Integer, primary_key=True)
    slug = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    invitations = relationship("Invitation", back_populates="tenant", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    username = Column(String(64), nullable=False, index=True)
    email = Column(String(128), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(32), nullable=False, default=ROLE_MEMBER)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    tenant = relationship("Tenant", back_populates="users")

    __table_args__ = (
        UniqueConstraint("tenant_id", "username", name="uq_tenant_username"),
    )


class Invitation(Base):
    __tablename__ = "invitations"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    code = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(128), nullable=True)
    role = Column(String(32), nullable=False, default=ROLE_MEMBER)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    used_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    tenant = relationship("Tenant", back_populates="invitations")

    @property
    def is_valid(self) -> bool:
        return self.used_at is None and datetime.utcnow() < self.expires_at


class UserSetting(Base):
    """每个用户独立的 LLM 配置：模型、base_url、api_key。每用户最多一条。"""
    __tablename__ = "user_settings"
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    model = Column(String(128), nullable=True)
    base_url = Column(String(512), nullable=True)
    api_key = Column(String(512), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


def get_db():
    """FastAPI 依赖：每次请求一个独立 Session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def gen_invitation_code() -> str:
    return secrets.token_urlsafe(16)


def init_db():
    """建表 + 初始化默认租户与超级管理员账号"""
    Base.metadata.create_all(engine)

    from auth import hash_password

    db = SessionLocal()
    try:
        default_tenant = db.query(Tenant).filter_by(slug=DEFAULT_TENANT_SLUG).first()
        if not default_tenant:
            default_tenant = Tenant(slug=DEFAULT_TENANT_SLUG, name=DEFAULT_TENANT_NAME)
            db.add(default_tenant)
            db.commit()
            db.refresh(default_tenant)
            print(f"[db] 已创建默认租户：{DEFAULT_TENANT_SLUG}")

        super_admin = db.query(User).filter_by(role=ROLE_SUPER_ADMIN).first()
        if not super_admin:
            initial_password = os.environ.get("INITIAL_ADMIN_PASSWORD", "admin123")
            admin_email = os.environ.get("INITIAL_ADMIN_EMAIL", "admin@example.com")
            admin_username = os.environ.get("INITIAL_ADMIN_USERNAME", "admin")
            super_admin = User(
                tenant_id=default_tenant.id,
                username=admin_username,
                email=admin_email,
                password_hash=hash_password(initial_password),
                role=ROLE_SUPER_ADMIN,
                is_active=True,
            )
            db.add(super_admin)
            db.commit()
            print("=" * 60)
            print("[db] 已创建初始超级管理员账号")
            print(f"     用户名: {admin_username}")
            print(f"     邮箱:   {admin_email}")
            print(f"     密码:   {initial_password}")
            print("     请登录后立即修改密码（POST /api/auth/change-password）")
            print("=" * 60)
    finally:
        db.close()


def make_invitation(
    db: Session,
    tenant_id: int,
    created_by: int,
    role: str = ROLE_MEMBER,
    email: Optional[str] = None,
    ttl_hours: int = 72,
) -> Invitation:
    if role not in ALL_ROLES:
        raise ValueError("无效角色")
    inv = Invitation(
        tenant_id=tenant_id,
        code=gen_invitation_code(),
        email=email,
        role=role,
        created_by=created_by,
        expires_at=datetime.utcnow() + timedelta(hours=ttl_hours),
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv
