"""最终确认：app.answer_question 完整回答 + 进程 vs 同步时间
在 /data/codes/rag_chat 下执行:
    python scripts/_diag_webui3.py
"""
import sys, time, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

print("=" * 60)
print("最终确认：app.answer_question + 进程启动时间")
print("=" * 60)

# 1. 进程启动时间 vs 文件 mtime（决定性！）
import subprocess, os, datetime
print("\n[1] 进程启动时间 vs 文件修改时间")
out = subprocess.run(["ps", "-o", "pid,lstart,cmd", "-p", "3680589"],
                     capture_output=True, text=True).stdout
print(out.strip())
for f in ["src/llm.py", "src/rag_pipeline.py"]:
    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(f))
    print(f"  {f} mtime: {mtime}")
print("  → 若进程启动时间早于文件 mtime → 进程跑旧代码（根因!）")

# 2. app.answer_question 完整回答（正确处理 dict history）
import app as app_mod
from app import answer_question
q = "大唐妖探电影什么时候上映？"
print(f"\n[2] app.answer_question 流式提问")
answers = []
try:
    for history, chunks_json in answer_question(q, [], True, "jiabohui"):
        if history:
            last = history[-1]
            if isinstance(last, dict):
                answers.append(last.get("content", ""))
            else:
                answers.append(last[1])
    final = answers[-1] if answers else "(无回答)"
    print(f"  chunks_json: {chunks_json[:120]}")
    print(f"  最终回答前 250 字:")
    print(f"  {final[:250]}")
except Exception:
    traceback.print_exc()

print("\n" + "=" * 60)
print("判断:")
print("  [1] 进程启动早于文件mtime → 必须重启 app.py（就是根因）")
print("  [2] 回答含'8月22日' → 新代码正常，重启后即可")
print("=" * 60)
