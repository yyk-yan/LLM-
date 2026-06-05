"""
rag_mapper.py
基于向量检索的字段映射知识库（多租户版）
- 所有读写都按 tenant_id 维度隔离，互不干扰
- 不为每个租户开独立 collection，避免 collection 数量爆炸；统一在一个 collection 里用 metadata 过滤
- ChromaDB 的 ID 仍需要全局唯一，因此改为 "{tenant_id}::{source}||{target}"
"""
from __future__ import annotations
import os
from typing import Optional
import chromadb
from sentence_transformers import SentenceTransformer

DB_PATH = os.path.join(os.path.dirname(__file__), "field_mapping_db")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "minilm")

DEFAULT_TENANT_ID = 1

_client = None
_collection = None
_model = None


def _get_components():
    global _client, _collection, _model
    if _model is None:
        print("  正在加载向量模型...")
        _model = SentenceTransformer(MODEL_PATH)
        _client = chromadb.PersistentClient(path=DB_PATH)
        _collection = _client.get_or_create_collection(
            name="field_mappings",
            metadata={"hnsw:space": "cosine"}
        )
        print(f"  向量模型加载完成，知识库已有 {_collection.count()} 条记录")
    return _collection, _model


def _doc_id(tenant_id: int, source_field: str, target_field: str) -> str:
    return f"{tenant_id}::{source_field}||{target_field}"


def add_mapping(source_field: str, target_field: str, tenant_id: int, score: float = 1.0):
    """
    记录一次成功的字段映射
    tenant_id 必填：所有写入都被打上租户标签，租户间永不串扰
    """
    if tenant_id is None:
        raise ValueError("add_mapping 必须传入 tenant_id")
    collection, model = _get_components()

    embedding = model.encode(source_field).tolist()
    doc_id = _doc_id(tenant_id, source_field, target_field)

    try:
        collection.upsert(
            embeddings=[embedding],
            documents=[source_field],
            metadatas=[{
                "tenant_id": int(tenant_id),
                "target": target_field,
                "score": score,
                "count": 1,
            }],
            ids=[doc_id],
        )
    except Exception as e:
        print(f"  写入知识库失败: {e}")


def retrieve_mapping(query_field: str, tenant_id: int, top_k: int = 3, threshold: float = 0.92) -> list:
    """
    检索历史相似映射（仅在该租户范围内）
    修复：阈值提高到0.92，只返回相似度最高的一条，避免多条记录互相干扰导致乱映射
    """
    if tenant_id is None:
        raise ValueError("retrieve_mapping 必须传入 tenant_id")
    collection, model = _get_components()

    if collection.count() == 0:
        return []

    embedding = model.encode(query_field).tolist()
    try:
        results = collection.query(
            query_embeddings=[embedding],
            n_results=min(top_k, collection.count()),
            where={"tenant_id": int(tenant_id)},
        )
    except Exception as e:
        print(f"  检索知识库失败: {e}")
        return []

    if not results.get("metadatas") or not results["metadatas"][0]:
        return []

    # 修复：只取相似度最高的一条，避免同一source对应多个target时乱命中
    filtered = []
    for meta, distance in zip(results["metadatas"][0], results["distances"][0]):
        similarity = 1 - distance
        if similarity >= threshold:
            filtered.append({
                "target": meta["target"],
                "similarity": round(similarity, 3),
            })
            break  # 只取最相似的一条
    return filtered


def get_stats(tenant_id: Optional[int] = None) -> dict:
    """查看知识库统计信息（按租户）"""
    collection, _ = _get_components()
    total = collection.count()
    info = {"total_mappings": total, "db_path": DB_PATH}
    if tenant_id is not None:
        try:
            data = collection.get(where={"tenant_id": int(tenant_id)})
            info["tenant_id"] = int(tenant_id)
            info["tenant_mappings"] = len(data.get("ids", []))
        except Exception:
            info["tenant_mappings"] = 0
    return info


def clear_db():
    """清空整个知识库（谨慎使用）"""
    global _client, _collection, _model
    if _client:
        _client.delete_collection("field_mappings")
        _collection = None
        _model = None
    print("知识库已清空")


def migrate_legacy_to_default():
    """
    把没有 tenant_id 的历史记录标记为默认租户。
    服务启动时调用一次即可，幂等。
    """
    try:
        collection, _ = _get_components()
    except Exception as e:
        print(f"  跳过 RAG 迁移：{e}")
        return 0
    try:
        data = collection.get(include=["metadatas", "embeddings", "documents"])
    except Exception as e:
        print(f"  RAG 迁移读取失败：{e}")
        return 0

    ids = data.get("ids", [])
    metas = data.get("metadatas", [])
    docs = data.get("documents", [])
    embs = data.get("embeddings", [])
    if not ids:
        return 0

    migrated = 0
    for i, meta in enumerate(metas):
        if meta and "tenant_id" in meta:
            continue
        new_meta = dict(meta or {})
        new_meta["tenant_id"] = DEFAULT_TENANT_ID
        target = new_meta.get("target", "")
        source = docs[i] if i < len(docs) else ids[i]
        new_id = _doc_id(DEFAULT_TENANT_ID, source, target)
        try:
            collection.delete(ids=[ids[i]])
            collection.upsert(
                ids=[new_id],
                embeddings=[embs[i]] if i < len(embs) else None,
                documents=[source],
                metadatas=[new_meta],
            )
            migrated += 1
        except Exception as e:
            print(f"  迁移条目失败 {ids[i]}: {e}")
    if migrated:
        print(f"[rag] 已将 {migrated} 条历史映射归入默认租户")
    return migrated