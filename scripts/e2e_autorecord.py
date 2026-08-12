"""自动点录音 E2E 验证（audit-ASR 修复轮）：zh-CN 本地化 DOM 下 head JS 能点中「录制」。

复现根因：gradio 6 按浏览器语言本地化按钮（zh-CN aria-label=「从麦克风录制」），
初版 JS 只匹配英文 "record" → 永远找不到按钮。本脚本以 locale=zh-CN + 假麦克风
（--use-fake-ui/device-for-media-stream 免授权弹窗）验证修复后选择器命中。

运行：python scripts/e2e_autorecord.py
通过：控制台出现 __voiceAssistAutoRecord clicked 且 #voice_audio 内出现「停止」按钮。
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ["VOICE_ASSIST_ENABLED"] = "true"

import httpx


def main() -> int:
    import app as app_mod

    # 本测试只验证"自动点录音"：置空讯飞密钥，避免假麦克风音调触发真实 ASR 连接
    app_mod.settings.xfyun_app_id = ""
    app_mod.settings.xfyun_api_key = ""
    app_mod.settings.xfyun_api_secret = ""

    port = 7871
    demo = app_mod.create_ui()
    demo.queue()
    demo.launch(
        server_name="127.0.0.1", server_port=port, prevent_thread_lock=True,
        head=app_mod._TTS_STALL_PROBE_HEAD + app_mod._voice_assist_head(),
    )
    t0 = time.time()
    while time.time() - t0 < 120:
        try:
            if httpx.get(f"http://127.0.0.1:{port}", timeout=2).status_code == 200:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        print("❌ app 启动超时")
        return 1
    print(f"app 已启动 :{port}")

    from playwright.sync_api import sync_playwright

    console_hits = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=[
            "--use-fake-ui-for-media-stream",      # 自动授予麦克风权限（免弹窗）
            "--use-fake-device-for-media-stream",  # 假麦克风（音调信号）
            "--autoplay-policy=no-user-gesture-required",
            "--mute-audio",
        ])
        ctx = browser.new_context(locale="zh-CN")  # 复现用户的中文本地化 DOM
        page = ctx.new_page()
        page.on("console", lambda m: console_hits.append(m.text))
        page.goto(f"http://127.0.0.1:{port}")

        ok = False
        deadline = time.time() + 30
        while time.time() < deadline:
            # 录音中标志：#voice_audio 内出现「停止」语义的按钮
            found = page.evaluate(
                """(() => {
                    const root = document.getElementById('voice_audio');
                    if (!root) return 'no-root';
                    for (const b of root.querySelectorAll('button')) {
                        const s = ((b.getAttribute('aria-label')||'') + ' ' + (b.textContent||''));
                        if (/停止|stop/i.test(s)) return 'recording';
                    }
                    return 'not-recording';
                })()"""
            )
            if found == "recording":
                ok = True
                break
            time.sleep(1)
        browser.close()

    clicked = any("__voiceAssistAutoRecord clicked" in t for t in console_hits)
    print(f"控制台标记 __voiceAssistAutoRecord clicked: {'有' if clicked else '无'}")
    print(f"录音自动启动（出现停止按钮）: {'是' if ok else '否'}")
    if ok and clicked:
        print("✅ 自动点录音 E2E 通过（zh-CN 本地化 DOM）")
        return 0
    print("❌ 自动点录音未生效")
    for t in console_hits:
        if "voiceAssist" in t:
            print("  console:", t)
    return 1


if __name__ == "__main__":
    sys.exit(main())
