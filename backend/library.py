"""
library.py
租户级文档库：同一租户内所有成员共享一个文档库，可上传 / 浏览 / 下载文件。
- 任何登录用户都可上传 / 浏览 / 下载本租户的文档库
- 删除：仅上传者本人 / 该租户管理员 / 超级管理员
- 不限定文件类型（用户主动上传 = 信任）；单文件大小由 MAX_LIBRARY_FILE_BYTES 限制
- 文件实体路径：backend/library/<tenant_slug>/<file_id>_<safe_filename>
- 元数据存 SQLite library_files 表
"""
from __future__ import annotations
import os
import secrets
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, joinedload

from auth import can_manage_tenant, get_current_user
from db import LibraryFile, User, get_db, ROLE_SUPER_ADMIN, ROLE_TENANT_ADMIN

router_library = APIRouter(prefix="/api/library", tags=["library"])

LIBRARY_ROOT = Path(__file__).parent / "library"
LIBRARY_ROOT.mkdir(exist_ok=True)

MAX_LIBRARY_FILE_BYTES = int(os.environ.get("MAX_LIBRARY_FILE_BYTES", str(100 * 1024 * 1024)))  # 默认 100MB


def _tenant_dir(tenant_slug: str) -> Path:
    p = LIBRARY_ROOT / tenant_slug
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_filename(name: str) -> str:
    """只取 basename + 去除非法字符，避免路径穿越和 Windows 保留字符"""
    base = Path(name).name
    bad = '<>:"/\\|?*\0'
    cleaned = "".join("_" if c in bad else c for c in base).strip()
    return cleaned or "file"


def _file_view(f: LibraryFile, current_user_id: int) -> dict:
    return {
        "id": f.id,
        "file_id": f.file_id,
        "filename": f.filename,
        "size_bytes": f.size_bytes,
        "size_kb": round(f.size_bytes / 1024, 1) if f.size_bytes else 0,
        "uploader_id": f.uploader_id,
        "uploader_name": f.uploader.username if f.uploader else "(已注销)",
        "description": f.description or "",
        "created_at": f.created_at.isoformat() if f.created_at else None,
        "can_delete": _can_delete(f, current_user_id),
    }


def _can_delete(f: LibraryFile, current_user_id: int) -> bool:
    """前端用：是否对该用户显示删除按钮。后端实际权限再次校验。"""
    return f.uploader_id == current_user_id  # 上传者本人；管理员的判断在路由里做


@router_library.get("")
def list_library(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """列出当前租户的所有文档库文件"""
    rows = (
        db.query(LibraryFile)
        .options(joinedload(LibraryFile.uploader))
        .filter_by(tenant_id=user.tenant_id)
        .order_by(LibraryFile.id.desc())
        .all()
    )
    return {
        "items": [_file_view(r, user.id) for r in rows],
        "is_admin": user.role in (ROLE_SUPER_ADMIN, ROLE_TENANT_ADMIN),
    }


@router_library.post("/upload")
async def upload_library(
    file: UploadFile = File(...),
    description: Optional[str] = Form(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """上传文件到本租户文档库"""
    raw = await file.read()
    size = len(raw)
    if size == 0:
        raise HTTPException(status_code=400, detail="文件为空")
    if size > MAX_LIBRARY_FILE_BYTES:
        mb = MAX_LIBRARY_FILE_BYTES // (1024 * 1024)
        raise HTTPException(status_code=400, detail=f"文件超过单文件大小上限（{mb} MB）")

    safe_name = _safe_filename(file.filename or "file")
    file_id = secrets.token_urlsafe(12)
    tenant_slug = user.tenant.slug if user.tenant else f"t{user.tenant_id}"
    target = _tenant_dir(tenant_slug) / f"{file_id}_{safe_name}"
    with open(target, "wb") as f:
        f.write(raw)

    record = LibraryFile(
        tenant_id=user.tenant_id,
        uploader_id=user.id,
        file_id=file_id,
        filename=safe_name,
        size_bytes=size,
        content_type=file.content_type,
        description=(description or "").strip()[:512] or None,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    # 显式加载 uploader 关系以便序列化
    db.refresh(record, attribute_names=["uploader"])
    return _file_view(record, user.id)


@router_library.get("/download/{file_id}")
def download_library(
    file_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """下载本租户文档库的文件"""
    record = db.query(LibraryFile).filter_by(file_id=file_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="文件不存在")
    if record.tenant_id != user.tenant_id and user.role != ROLE_SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="无权访问其他租户的文件")

    tenant_slug = record.tenant.slug if record.tenant else f"t{record.tenant_id}"
    disk_path = _tenant_dir(tenant_slug) / f"{record.file_id}_{record.filename}"
    if not disk_path.exists():
        raise HTTPException(status_code=404, detail="文件实体不存在（可能已被清理）")

    return FileResponse(
        path=str(disk_path),
        filename=record.filename,
        media_type=record.content_type or "application/octet-stream",
    )


@router_library.delete("/{file_id}")
def delete_library(
    file_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除文件：上传者本人 / 该租户管理员 / 超级管理员"""
    record = db.query(LibraryFile).filter_by(file_id=file_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="文件不存在")

    is_uploader = record.uploader_id == user.id
    is_tenant_admin = user.role == ROLE_TENANT_ADMIN and user.tenant_id == record.tenant_id
    is_super = user.role == ROLE_SUPER_ADMIN
    if not (is_uploader or is_tenant_admin or is_super):
        raise HTTPException(status_code=403, detail="只有上传者本人或管理员可以删除")

    tenant_slug = record.tenant.slug if record.tenant else f"t{record.tenant_id}"
    disk_path = _tenant_dir(tenant_slug) / f"{record.file_id}_{record.filename}"
    if disk_path.exists():
        try:
            disk_path.unlink()
        except Exception as e:
            # 文件删不掉就报错，不要孤立元数据
            raise HTTPException(status_code=500, detail=f"删除磁盘文件失败：{e}")

    db.delete(record)
    db.commit()
    return {"status": "ok"}
