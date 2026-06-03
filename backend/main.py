"""
main.py
基于 FastAPI 的后端服务入口（多租户 + JWT 认证版）。
- 业务接口全部要求 Bearer Token，并按 (tenant_id, user_id) 隔离数据与文件
- /api/auth/* 与 /api/admin/* 由 tenant_admin 路由提供（登录、注册、邀请、用户管理）
- 启动时建库 + 初始化默认租户与超级管理员；旧的 uploads / outputs / RAG 知识库会迁入默认租户
"""
from __future__ import annotations
import os
import time
import uuid
import shutil
from pathlib import Path
from threading import Lock
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from extractor import extract_file, get_xlsx_structure, get_docx_structure
from agent import extract_and_fill
from filler import fill_xlsx, fill_docx

from auth import get_current_user
from db import User, UserSetting, init_db, DEFAULT_TENANT_SLUG, get_db
from tenant_admin import router_auth, router_admin
import rag_mapper
from sqlalchemy.orm import Session

app = FastAPI(title="智能文档填表系统", version="2.0.0")

# 仅在显式开启时放开 CORS；生产建议把前端打包进同源以省去这一步
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ALLOW_ORIGINS", "*").split(","),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_ROOT = Path("uploads")
OUTPUT_ROOT = Path("../outputs")
FRONTEND_DIR = Path("../frontend")
UPLOAD_ROOT.mkdir(exist_ok=True)
OUTPUT_ROOT.mkdir(exist_ok=True)

# 多租户内存存储：每个 (tenant_id, user_id) 拥有独立的数据源
# key = (tenant_id, user_id) -> {"docs": {filename: text}, "paths": {filename: path}}
_USER_STATE: dict[tuple[int, int], dict] = {}
_state_lock = Lock()


def _user_key(user: User) -> tuple[int, int]:
    return (user.tenant_id, user.id)


def _get_user_state(user: User) -> dict:
    key = _user_key(user)
    with _state_lock:
        st = _USER_STATE.get(key)
        if st is None:
            st = {"docs": {}, "paths": {}}
            _USER_STATE[key] = st
    return st


def _user_upload_dir(user: User) -> Path:
    slug = user.tenant.slug if user.tenant else "t" + str(user.tenant_id)
    p = UPLOAD_ROOT / slug / f"u{user.id}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _user_output_dir(user: User) -> Path:
    slug = user.tenant.slug if user.tenant else "t" + str(user.tenant_id)
    p = OUTPUT_ROOT / slug / f"u{user.id}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _migrate_legacy_files():
    """
    把旧的 backend/uploads/*.* 与 outputs/*.* 直接归到默认租户的根目录下（不知道原始用户）。
    只在首次启动有匹配文件时迁移；每个文件最多移一次，幂等。
    迁移目标：uploads/<default-slug>/legacy/  与 outputs/<default-slug>/legacy/
    """
    legacy_upload_target = UPLOAD_ROOT / DEFAULT_TENANT_SLUG / "legacy"
    legacy_output_target = OUTPUT_ROOT / DEFAULT_TENANT_SLUG / "legacy"

    moved = 0
    for f in UPLOAD_ROOT.iterdir():
        if f.is_file():
            legacy_upload_target.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(f), str(legacy_upload_target / f.name))
                moved += 1
            except Exception as e:
                print(f"  迁移上传文件失败 {f.name}: {e}")
    for f in OUTPUT_ROOT.iterdir():
        if f.is_file():
            legacy_output_target.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(f), str(legacy_output_target / f.name))
                moved += 1
            except Exception as e:
                print(f"  迁移输出文件失败 {f.name}: {e}")
    if moved:
        print(f"[main] 已将 {moved} 个旧文件迁入默认租户 legacy 目录")


@app.on_event("startup")
def _startup():
    init_db()
    try:
        rag_mapper.migrate_legacy_to_default()
    except Exception as e:
        print(f"[main] RAG 迁移跳过: {e}")
    _migrate_legacy_files()


# 把认证 / 管理路由挂上
app.include_router(router_auth)
app.include_router(router_admin)


@app.get("/")
def root():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.post("/api/upload-sources")
async def upload_sources(
    files: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
):
    """批量上传数据源文档（按当前用户隔离）"""
    state = _get_user_state(user)
    SOURCE_DOCS, SOURCE_PATHS = state["docs"], state["paths"]
    target_dir = _user_upload_dir(user)

    results = []
    for file in files:
        suffix = Path(file.filename).suffix.lower()
        if suffix not in (".docx", ".xlsx", ".xls", ".md", ".txt", ".pdf"):
            results.append({"filename": file.filename, "status": "跳过", "reason": "不支持的格式"})
            continue

        safe_name = Path(file.filename).name  # 阻止路径穿越
        save_path = target_dir / safe_name
        with open(save_path, "wb") as f:
            f.write(await file.read())

        try:
            file_size = os.path.getsize(str(save_path))
            if suffix in (".xlsx", ".xls") and file_size > 1 * 1024 * 1024:
                SOURCE_DOCS[safe_name] = ""
                SOURCE_PATHS[safe_name] = str(save_path)
                results.append({
                    "filename": safe_name, "status": "成功", "chars": 0,
                    "note": "大文件，将在填表时按需过滤",
                })
            else:
                text = extract_file(str(save_path))
                SOURCE_DOCS[safe_name] = text
                SOURCE_PATHS[safe_name] = str(save_path)
                results.append({"filename": safe_name, "status": "成功", "chars": len(text)})
        except Exception as e:
            import traceback
            traceback.print_exc()
            results.append({"filename": safe_name, "status": "失败", "reason": str(e)})

    return {
        "uploaded": len([r for r in results if r["status"] == "成功"]),
        "total_sources": len(SOURCE_DOCS),
        "details": results,
    }


@app.get("/api/sources")
def list_sources(user: User = Depends(get_current_user)):
    state = _get_user_state(user)
    return {
        "count": len(state["docs"]),
        "files": [{"filename": k, "chars": len(v)} for k, v in state["docs"].items()],
    }


@app.delete("/api/sources")
def clear_sources(user: User = Depends(get_current_user)):
    state = _get_user_state(user)
    state["docs"].clear()
    state["paths"].clear()
    # 同时清理该用户的上传目录
    upload_dir = _user_upload_dir(user)
    for f in upload_dir.iterdir():
        if f.is_file():
            try:
                f.unlink()
            except Exception:
                pass
    return {"status": "已清空"}


@app.post("/api/fill-template")
async def fill_template(
    template: UploadFile = File(...),
    requirement: Optional[str] = Form(default="智能填表，根据数据源内容填写模板中的所有字段"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    state = _get_user_state(user)
    SOURCE_DOCS, SOURCE_PATHS = state["docs"], state["paths"]
    if not SOURCE_DOCS:
        raise HTTPException(status_code=400, detail="请先上传数据源文档")

    # 取当前用户的 LLM 配置；缺项由 agent.py 内部回退到环境变量
    setting = db.query(UserSetting).filter_by(user_id=user.id).first()
    llm_cfg = {
        "model": setting.model if setting else None,
        "base_url": setting.base_url if setting else None,
        "api_key": setting.api_key if setting else None,
    }

    start_time = time.time()
    upload_dir = _user_upload_dir(user)
    output_dir = _user_output_dir(user)

    template_filename = Path(template.filename).name
    template_path = upload_dir / f"template_{uuid.uuid4().hex[:8]}_{template_filename}"
    with open(template_path, "wb") as f:
        f.write(await template.read())

    ext = Path(template_filename).suffix.lower()
    if ext not in (".xlsx", ".xls", ".docx"):
        raise HTTPException(status_code=400, detail="模板文件必须是 .xlsx 或 .docx 格式")

    try:
        if ext in (".xlsx", ".xls"):
            template_structure = get_xlsx_structure(str(template_path))
        else:
            raw = get_docx_structure(str(template_path))
            paragraphs = raw.get("paragraphs", [])
            tables = raw.get("tables", [])
            template_structure = {}
            for t in tables:
                idx = t["index"]
                desc = paragraphs[idx + 1] if idx + 1 < len(paragraphs) else ""
                template_structure[f"table_{idx}"] = {
                    "headers": t["headers"],
                    "description": desc,
                    "row_count": t.get("row_count", 0),
                }

        fill_data, rag_hits = extract_and_fill(
            source_texts=SOURCE_DOCS,
            source_paths=SOURCE_PATHS,
            template_path=str(template_path),
            template_structure=template_structure,
            user_requirement=requirement,
            tenant_id=user.tenant_id,
            llm_cfg=llm_cfg,
        )

        output_filename = f"filled_{uuid.uuid4().hex[:8]}_{template_filename}"
        output_path = output_dir / output_filename

        if ext in (".xlsx", ".xls"):
            fill_xlsx(str(template_path), str(output_path), fill_data)
        else:
            fill_docx(str(template_path), str(output_path), fill_data)

        elapsed = time.time() - start_time

        return {
            "status": "成功",
            "output_file": output_filename,
            "elapsed_seconds": round(elapsed, 2),
            "download_url": f"/api/download/{output_filename}",
            "rag_hits": rag_hits,
            "rag_hit_count": len(rag_hits),
            "fill_data_preview": {
                k: v[:2] if isinstance(v, list) else v
                for k, v in fill_data.items()
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"填表失败: {str(e)}")
    finally:
        if template_path.exists():
            try:
                template_path.unlink()
            except Exception:
                pass


@app.get("/api/download/{filename}")
def download_file(filename: str, user: User = Depends(get_current_user)):
    """下载填写完成的文件（仅允许下载自己目录中的）"""
    safe_name = Path(filename).name
    output_dir = _user_output_dir(user)
    file_path = output_dir / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(
        path=str(file_path),
        filename=safe_name,
        media_type="application/octet-stream",
    )


@app.get("/api/outputs")
def list_outputs(user: User = Depends(get_current_user)):
    output_dir = _user_output_dir(user)
    files = []
    for f in output_dir.iterdir():
        if f.is_file():
            files.append({"filename": f.name, "size_kb": round(f.stat().st_size / 1024, 1)})
    return {"files": files}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
