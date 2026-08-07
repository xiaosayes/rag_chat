"""服务器加载路径诊断：确认实际加载的模块文件 + 完整真实链路
在 /data/codes/rag_chat 下执行:
    python scripts/_diag_loadpath.py
"""
import sys, time, inspect
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

print("=" * 60)
print("服务器加载路径 + 真实链路诊断")
print("=" * 60)

# 1. 实际加载的模块文件路径（关键！排除多副本/pyc 干扰）
import src.rag_pipeline as rp
import src.llm as llm_mod
print("\n[1] 实际加载的模块文件")
print("  rag_pipeline.py:", rp.__file__)
print("  llm.py:         ", llm_mod.__file__)
print("  RELEVANCE_THRESHOLD:", rp.RAGPipeline.RELEVANCE_THRESHOLD)
print("  新note存在:", "不要因参考信息缺失而拒绝回答" in llm_mod._SEARCH_GUIDE_NOTE)

# 2. 检查是否有重复副本
print("\n[2] 项目内 rag_pipeline.py / llm.py 副本")
for f in Path(".").rglob("rag_pipeline.py"):
    print("  ", f)
for f in Path(".").rglob("llm.py"):
    if "site-packages" not in str(f):
        print("  ", f)

# 3. 真实完整链路（不 mock）+ 打印 meta
from src.rag_pipeline import RAGPipeline
p = RAGPipeline(local_mode=True, project_id="jiabohui")
q = "大唐妖探电影什么时候上映？"
print(f"\n[3] 真实链路: {q}")
t0 = time.time()
events = list(p.query_stream(q))
meta = events[0]
tokens = "".join(e for e in events[1:] if isinstance(e, str))
print(f"  from_kb={meta.get('from_kb')} query_type={meta.get('query_type')} "
      f"chunks={len(meta.get('chunks') or [])} search_enabled={meta.get('search_enabled')}")
print(f"  耗时 {time.time()-t0:.1f}s")
print(f"  回答前 200 字: {tokens[:200]}")
print()
print("判断:")
print("  [1] 路径不在 /data/codes/rag_chat/src/ → 加载了错误副本(根因!)")
print("  [3] chunks>0 → 走了RAG(检索分高)；chunks=0 → 走了降级/无结果分支")
print("  [3] search_enabled=False → enable_search 未生效")
