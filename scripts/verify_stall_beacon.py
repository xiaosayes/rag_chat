# -*- coding: utf-8 -*-
"""客户端停顿遥测全链路验证（一次性诊断，不进测试套件）：

1. 启动真实 app（走 main() → head 探针注入）
2. 浏览器开页 → 动态创建 <video>（探针 MutationObserver 应捕获）
3. 合成 waiting →(1.0s)→ playing 事件 → 探针应 sendBeacon
4. 服务端日志应出现「客户端停顿上报: 时长≈1.0s ...」
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PORT = 7892
LOG = "beacon_server.log"


def main():
    from playwright.sync_api import sync_playwright

    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "app.py", "--port", str(PORT)],
        stdout=open(LOG, "w", encoding="utf-8"), stderr=subprocess.STDOUT)
    try:
        import httpx

        t0 = time.time()
        while time.time() - t0 < 120:
            try:
                if httpx.get(f"http://127.0.0.1:{PORT}", timeout=2).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("app 启动超时")

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{PORT}")
            page.wait_for_timeout(4000)
            # 动态创建 video → 探针应自动挂载；再合成停顿事件
            page.evaluate("""() => {
                const v = document.createElement('video');
                v.id = 'probe-video';
                document.body.appendChild(v);
            }""")
            page.wait_for_timeout(300)
            page.evaluate("""() => {
                const v = document.getElementById('probe-video');
                v.dispatchEvent(new Event('waiting'));
            }""")
            page.wait_for_timeout(1000)
            page.evaluate("""() => {
                const v = document.getElementById('probe-video');
                v.dispatchEvent(new Event('playing'));
            }""")
            page.wait_for_timeout(2000)
            browser.close()
        print("页面 JS 错误:", errors or "无")
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except Exception:
            proc.kill()

    time.sleep(1)
    # loguru 的 WARNING 写文件 sink（logs/rag_*.log）；stdout 只有 import 期日志
    import glob

    lines = []
    for f in glob.glob("logs/rag_*.log"):
        with open(f, encoding="utf-8", errors="replace") as fh:
            lines += [l for l in fh if "客户端停顿上报" in l]
    with open(LOG, encoding="utf-8", errors="replace") as fh:
        log = fh.read()
    print("服务端停顿上报日志:", [l.strip()[-90:] for l in lines[-3:]] or "【未收到——信标链路不通！】")
    print("前端 patch 自检:", "通过" if "前端 patch 自检通过" in log else "【未见自检通过日志】")


if __name__ == "__main__":
    main()
