"""服务器真实提问诊断：不 mock，走完整真实检索 + 真实 LLM
在 /data/codes/rag_chat 下执行:
    python scripts/_diag_real.py
"""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.rag_pipeline import RAGPipeline

print("=" * 60)
print("服务器真实提问诊断（不 mock，完整链路）")
print("=" * 60)

p = RAGPipeline(local_mode=True, project_id="jiabohui")

q = "大唐妖探电影什么时候上映？"
print(f"\n提问: {q}")
print("-" * 40)

t0 = time.time()
events = list(p.query_stream(q))
t1 = time.time()

meta = events[0]
tokens = "".join(e for e in events[1:] if isinstance(e, str))

print(f"\n[meta] from_kb={meta.get('from_kb')} | query_type={meta.get('query_type')} "
      f"| chunks={len(meta.get('chunks') or [])} | search_enabled={meta.get('search_enabled')}")
print(f"[耗时] {t1-t0:.1f}s")
print(f"\n[回答前 300 字]")
print(tokens[:300])
print("-" * 40)
print("判断:")
print("  chunks>0 且 search_enabled=True  → 走了 RAG（带知识库上下文），未降级!")
print("  chunks=0 且 search_enabled=True  → 走了降级/闲聊分支，联网应生效")
print("  chunks=0 且 search_enabled=False → enable_search 未生效")
