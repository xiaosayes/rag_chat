"""最终验证：模拟 Web UI 完整路径（带 Gradio 6.x dict 历史 + 流式）
在 /data/codes/rag_chat 下执行:
    python scripts/_diag_final.py
"""
import sys, time, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

print("=" * 60)
print("最终验证：Web UI 完整路径（Gradio 6.x dict 历史 + 流式）")
print("=" * 60)

import app as app_mod
from app import answer_question

q = "大唐妖探电影什么时候上映？"

# 模拟 gradio 6.x 的 dict 历史（含之前的拒答，模拟用户反复提问的会话）
history = [
    {"role": "user", "content": "大唐妖探电影什么时候上映？"},
    {"role": "assistant", "content": "目前家博会（广州）的官方资料里，并没有提到大唐妖探这个活动哦。"},
]

print(f"\n[测试] 带历史 + 流式提问（= Web UI 真实路径）")
final_answer = ""
try:
    for hist, chunks_json in answer_question(q, list(history), True, "jiabohui"):
        if hist:
            last = hist[-1]
            if isinstance(last, dict):
                final_answer = last.get("content", "")
            elif isinstance(last, tuple):
                final_answer = last[1]
    print(f"  chunks_json: {chunks_json[:100]}")
    print(f"  最终回答前 250 字:")
    print(f"  {final_answer[:250]}")
    has_reject = ("并没有提到" in final_answer or "没有提到" in final_answer
                  or "没找到" in final_answer or "没有这个" in final_answer)
    has_answer = "上映" in final_answer and "8月" in final_answer
    print(f"\n  判定: 拒答={has_reject} | 含上映信息={has_answer}")
    if has_answer and not has_reject:
        print("  ✅ Web UI 路径正常，重启后应已修复")
    elif has_reject:
        print("  ⚠️ 仍拒答 → 需要进一步排查（把此输出发我）")
except Exception:
    traceback.print_exc()
