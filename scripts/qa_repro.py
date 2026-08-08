"""
一致性复现：对比前端问法 vs 测试问法在同一接口下的输出稳定性
"""
import sys, io, json, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from loguru import logger
logger.remove()
from src.config import settings
from src.rag_pipeline import RAGPipeline

QUESTIONS = [
    ("前端问法", "一天推荐路线"),
    ("测试问法", "逛一天的话，推荐什么路线？"),
]

def main():
    pipeline = RAGPipeline(local_mode=True, memory_mode=settings.qdrant_memory_mode, project_id="jiabohui")
    out = []
    for label, q in QUESTIONS:
        for trial in (1, 2, 3):
            result = pipeline.query(question=q, top_k=10, rerank=True)
            out.append({
                "label": label, "trial": trial, "question": q,
                "query_type": result.get("query_type"),
                "answer": result.get("answer", ""),
            })
            print(f"\n===== [{label}] 第{trial}次 | {q} | type={result.get('query_type')}")
            print(result.get("answer", "")[:260])
            time.sleep(1)
    with open("precheck/out/repro.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main()