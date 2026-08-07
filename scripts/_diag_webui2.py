"""Web UI 路径复现 v2：打印完整 traceback
在 /data/codes/rag_chat 下执行（会与新开的 app.py 冲突，先停掉 app.py 更干净）:
    python scripts/_diag_webui2.py
"""
import sys, time, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

print("=" * 60)
print("Web UI 路径复现 v2（完整 traceback）")
print("=" * 60)

import app as app_mod
from app import answer_question

q = "大唐妖探电影什么时候上映？"
print(f"\n[尝试] 流式提问: {q}")
try:
    for history, chunks_json in answer_question(q, [], True, "jiabohui"):
        last = history[-1][1] if history else ""
    print("最终回答前 200 字:", last[:200])
except Exception:
    traceback.print_exc()

print("\n" + "=" * 60)
print("尝试2：直接调用 pipe.query_stream 带 conversation_history=[]")
print("=" * 60)
try:
    pipe = app_mod.init_pipeline("jiabohui")
    events = list(pipe.query_stream(q, conversation_history=[]))
    meta = events[0]
    tokens = "".join(e for e in events[1:] if isinstance(e, str))
    print("meta:", {k: v for k, v in meta.items() if k != "timing"})
    print("回答前 200 字:", tokens[:200])
except Exception:
    traceback.print_exc()
