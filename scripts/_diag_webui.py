"""Web UI 路径复现：完全模拟 app.py 的 answer_question 调用
在 /data/codes/rag_chat 下执行:
    python scripts/_diag_webui.py
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

print("=" * 60)
print("Web UI 路径复现（模拟 gradio 提问）")
print("=" * 60)

# 1. 检查是否有旧进程占用（关键！）
import subprocess
print("\n[1] 当前 app.py 进程:")
out = subprocess.run(["ps", "aux"], capture_output=True, text=True).stdout
for line in out.splitlines():
    if "app.py" in line and "grep" not in line:
        print("  ", line.strip())
print("  (若进程启动时间早于文件同步时间 → 运行的是旧代码!)")

# 2. 完全模拟 Web UI: 用 app.answer_question（与 gradio 完全相同的路径）
print("\n[2] 模拟 Web UI 提问（app.answer_question 流式）")
try:
    from src.app import answer_question  # 或 app 模块名按实际
except ImportError:
    import app as app_mod
    answer_question = app_mod.answer_question

q = "大唐妖探电影什么时候上映？"
t0 = time.time()
try:
    for history, chunks_json in answer_question(q, [], True, "jiabohui"):
        last = history[-1][1] if history else ""
        pass
    print(f"  耗时 {time.time()-t0:.1f}s")
    print(f"  chunks_json: {chunks_json[:100] if chunks_json else '(空)'}")
    print(f"  最终回答前 200 字: {last[:200]}")
except Exception as e:
    print(f"  调用异常: {e}")

# 3. 再试非流式
print("\n[3] 非流式提问（app.answer_question use_stream=False）")
t0 = time.time()
try:
    for history, chunks_json in answer_question(q, [], False, "jiabohui"):
        last = history[-1][1] if history else ""
        pass
    print(f"  耗时 {time.time()-t0:.1f}s")
    print(f"  最终回答前 200 字: {last[:200]}")
except Exception as e:
    print(f"  调用异常: {e}")

print("\n" + "=" * 60)
print("判断:")
print("  [1] 进程启动时间早 → 旧进程未重启，Web UI 跑旧代码（根因!）")
print("  [2]/[3] 回答正确 → Web UI 路径正常，问题在 gradio 前端/缓存")
print("  [2]/[3] 回答拒答  → app.py 层有问题（与 query_stream 差异点）")
print("=" * 60)
