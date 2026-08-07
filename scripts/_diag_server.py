"""服务器诊断脚本：定位 enable_search 未生效的环节
用法（在 /data/codes/rag_chat 下执行）:
    python scripts/_diag_server.py
"""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

print("=" * 60)
print("服务器诊断: 大唐妖探 enable_search 链路")
print("=" * 60)

# 1. settings 实际加载值（关键：确认 .env 是否被 pydantic 读到）
try:
    from src.config import settings
    print("\n[1] settings 实际加载值")
    print("  llm_enable_search:", settings.llm_enable_search)
    print("  llm_model_name:  ", settings.llm_model_name)
    print("  .env 路径:       ", Path(".env").resolve())
    print("  .env 存在:       ", Path(".env").exists())
except Exception as e:
    print(f"\n[1] settings 加载失败: {e}")

# 2. 直接读 .env 文件内容（排除 pydantic 映射问题）
try:
    env = Path(".env").read_text(encoding="utf-8") if Path(".env").exists() else ""
    for line in env.splitlines():
        if "SEARCH" in line.upper() or "MODEL" in line.upper():
            print(f"[2] .env 行: {line.strip()}")
except Exception as e:
    print(f"[2] 读 .env 失败: {e}")

# 3. 直接调用 API + enable_search=True（不经过流水线，验证 API 层）
try:
    from dashscope import Generation
    t0 = time.time()
    resp = Generation.call(
        model=settings.llm_model_name,
        messages=[{"role": "user", "content": "大唐妖探电影什么时候上映？"}],
        api_key=settings.dashscope_api_key,
        enable_search=True,
        result_format="message",
    )
    print(f"\n[3] 直接 API + enable_search=True (耗时 {time.time()-t0:.1f}s)")
    print("  status:", resp.status_code)
    if resp.status_code == 200:
        content = resp.output.choices[0].message.content
        print("  回答前 120 字:", content[:120])
        print("  含'上映':", "上映" in content)
    else:
        print("  message:", getattr(resp, "message", "无"))
except Exception as e:
    print(f"\n[3] API 调用异常: {e}")

# 4. 完整流水线（走 query_stream，观察降级分支与 enable_search）
try:
    from unittest.mock import MagicMock
    from src.rag_pipeline import RAGPipeline, QueryType
    from src.chunking import Chunk
    p = RAGPipeline(local_mode=True, project_id="jiabohui")
    p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
    fake_chunk = Chunk(id="c1", artifact_id="a1", artifact_name="家博会参观地图",
                       text="办公环境主题馆 CMF趋势论坛 参展商手册", chunk_type="detail", metadata={})
    low2 = [(fake_chunk, 0.25), (fake_chunk, 0.20)]
    p.hybrid_retriever.retrieve = MagicMock(return_value=low2)
    p.reranker.rerank = MagicMock(return_value=low2)
    p._ensure_knowledge_base = MagicMock()
    t0 = time.time()
    events = list(p.query_stream("大唐妖探电影什么时候上映？"))
    meta = events[0]
    tokens = "".join(e for e in events[1:] if isinstance(e, str))
    print(f"\n[4] 完整流水线 (mock 2条低分检索, 耗时 {time.time()-t0:.1f}s)")
    print("  search_enabled:", meta.get("search_enabled"))
    print("  chunks:", meta.get("chunks"))
    print("  强制重排被调用:", p.reranker.rerank.called)
    print("  回答前 120 字:", tokens[:120])
except Exception as e:
    print(f"\n[4] 流水线测试异常: {e}")

print("\n" + "=" * 60)
print("判断依据:")
print("  [1] llm_enable_search=False → .env 未读到/映射失败（最可能根因）")
print("  [3] 含'上映'→ API 层 enable_search 生效；不含 → 模型/API 层问题")
print("  [4] search_enabled=False → 流水线层问题")
print("=" * 60)
