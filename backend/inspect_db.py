"""
inspect_db.py
开发调试脚本：查看 RAG 知识库中的字段映射记录（带租户标签）
"""
import os
import chromadb

DB_PATH = os.path.join(os.path.dirname(__file__), "field_mapping_db")

client = chromadb.PersistentClient(path=DB_PATH)
try:
    col = client.get_collection("field_mappings")
except Exception:
    print("知识库尚未创建")
    raise SystemExit(0)

print(f"知识库总记录数: {col.count()}\n")
data = col.get(include=["documents", "metadatas"])
for doc, meta in zip(data["documents"], data["metadatas"]):
    tid = (meta or {}).get("tenant_id", "-")
    target = (meta or {}).get("target", "")
    score = (meta or {}).get("score", "")
    print(f"[t{tid:>3}] 源字段: {doc:<14} -> 目标字段: {target:<10}  (score={score})")
