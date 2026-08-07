"""独占诊断：停掉 app.py 后运行（语义检索正常，模拟 app.py 内部真实行为）
在 /data/codes/rag_chat 下执行（先停 app.py）:
    pkill -f "app.py --project jiabohui"
    python scripts/_diag_exclusive.py
"""
import sys, time, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

print("=" * 60)
print("独占诊断（语义检索正常 = app.py 内部真实行为）")
print("=" * 60)

from src.rag_pipeline import RAGPipeline
from src.config import settings

p = RAGPipeline(local_mode=True, project_id="jiabohui")
p._ensure_knowledge_base()
q = "大唐妖探电影什么时候上映？"

# 1. 语义检索是否正常（无 qdrant 并发错误 = app.py 已停）
res = p.hybrid_retriever.retrieve(q, top_k=settings.retriever_top_k)
print(f"\n[1] 混合检索: {len(res)} 条")
rr = p.reranker.rerank(query=q, candidates=res)
print("  重排后分数:")
for c, s in rr[:5]:
    print(f"    {round(s,4)} | {c.artifact_name}")
mx = max(s for _, s in rr)
print(f"  max_score={round(mx,4)} >=0.45? {mx>=0.45} -> {'走RAG(拒答根因!)' if mx>=0.45 else '触发降级'}")

# 2. 完整链路回答
print(f"\n[2] 完整链路回答:")
t0 = time.time()
events = list(p.query_stream(q))
meta = events[0]
tokens = "".join(e for e in events[1:] if isinstance(e, str))
print(f"  chunks={len(meta.get('chunks') or [])} search_enabled={meta.get('search_enabled')} 耗时{time.time()-t0:.1f}s")
print(f"  回答前 150 字: {tokens[:150]}")

print("\n" + "=" * 60)
print("判断:")
print("  [1] max>=0.45 且 [2] chunks>0 → app.py 内部走 RAG(语义正常) → 拒答根因!")
print("  [1] max<0.45  且 [2] chunks=0 → 降级正常 → 问题在 app.py 进程/缓存")
print("=" * 60)
