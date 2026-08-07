"""进程内加载检查：确认运行中的 app.py 进程加载的代码
在 /data/codes/rag_chat 下执行:
    python scripts/_diag_proc.py
"""
import sys, subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

print("=" * 60)
print("运行中 app.py 的代码加载检查")
print("=" * 60)

# 1. 当前 app.py 进程
print("\n[1] 运行中的 app.py 进程:")
out = subprocess.run(["ps", "aux"], capture_output=True, text=True).stdout
for line in out.splitlines():
    if "app.py" in line and "grep" not in line and "diag" not in line:
        print("  ", line.strip())

# 2. 检查是否有 __pycache__ 旧 pyc（可能干扰）
print("\n[2] __pycache__ 状态:")
for f in sorted(Path("src/__pycache__").glob("rag_pipeline*.pyc")) if Path("src/__pycache__").exists() else []:
    print("  ", f.name, "mtime:", f.stat().st_mtime)
for f in sorted(Path("src/__pycache__").glob("llm*.pyc")) if Path("src/__pycache__").exists() else []:
    print("  ", f.name, "mtime:", f.stat().st_mtime)

# 3. 关键：直接读服务器上的 llm.py 和 rag_pipeline.py 关键标记
import re
for fn in ["src/llm.py", "src/rag_pipeline.py"]:
    content = Path(fn).read_text(encoding="utf-8")
    has_note = "不要因参考信息缺失而拒绝回答" in content
    has_threshold = "RELEVANCE_THRESHOLD = 0.45" in content
    has_force = "temporal or len(retrieve_results)" in content
    print(f"\n[3] {fn}:")
    print(f"  新note: {has_note} | 阈值0.45: {has_threshold} | 强制重排: {has_force}")

print("\n" + "=" * 60)
print("若 [1] 进程存在且 [3] 全部 True → 代码正确，问题在运行逻辑")
print("若 [1] 无进程 → 服务没跑，用户访问的是缓存页面")
print("=" * 60)
