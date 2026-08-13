"""对照探针（audit-ASR）：JS .click() vs playwright page.click 触发录音后，流事件是否到达。

用法：python scripts/probe_record_stream.py [js|user]
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
    mode = sys.argv[1] if len(sys.argv) > 1 else "user"
    import app as app_mod

    app_mod.settings.xfyun_app_id = "fake"
    app_mod.settings.xfyun_api_key = "fake"
    app_mod.settings.xfyun_api_secret = "fake"
    hits = {"stream": 0}
    _orig = app_mod.voice_stream_dispatch

    def _logged(audio_filepath, state, project_id: str = "", request=None):
        hits["stream"] += 1
        if hits["stream"] <= 3:
            print(f"[probe] stream 事件 #{hits['stream']} file={audio_filepath}")
        yield from _orig(audio_filepath, state, project_id, request)

    app_mod.voice_stream_dispatch = _logged

    port = 7873
    demo = app_mod.create_ui()
    demo.queue()
    head = app_mod._TTS_STALL_PROBE_HEAD
    demo.launch(server_name="127.0.0.1", server_port=port,
                prevent_thread_lock=True, head=head)
    t0 = time.time()
    while time.time() - t0 < 120:
        try:
            if httpx.get(f"http://127.0.0.1:{port}", timeout=2).status_code == 200:
                break
        except Exception:
            pass
        time.sleep(1)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            "--mute-audio",
        ])
        page = browser.new_context(locale="zh-CN").new_page()
        uploads = []
        page.on("request", lambda r: uploads.append(r.url) if "upload" in r.url or "join" in r.url else None)
        page.goto(f"http://127.0.0.1:{port}")
        time.sleep(6)
        if mode == "user":
            # 真实输入事件点击「录制」
            page.click("#voice_audio button:has-text('录制')", timeout=10000)
            print("[probe] page.click 录制 完成")
        elif mode in ("js", "jsfull"):
            # JS 找到录制按钮并点击：js=纯 .click()；jsfull=完整指针事件序列
            full = mode == "jsfull"
            page.evaluate(
                """(full) => {
                    const root = document.getElementById('voice_audio');
                    if (!root) { console.log('[probe] no root'); return; }
                    let btn = null;
                    for (const b of root.querySelectorAll('button')) {
                        const s = ((b.getAttribute('aria-label')||'') + ' ' + (b.textContent||'')).toLowerCase();
                        if (/stop|停止/.test(s)) continue;
                        if (/record|录制|录音/.test(s)) { btn = b; break; }
                    }
                    if (!btn) { console.log('[probe] no btn'); return; }
                    if (full) {
                        for (const t of ['pointerdown','mousedown','pointerup','mouseup','click']) {
                            btn.dispatchEvent(new MouseEvent(t, {bubbles:true, cancelable:true, view:window}));
                        }
                        console.log('[probe] full sequence dispatched');
                    } else {
                        btn.click();
                        console.log('[probe] plain click dispatched');
                    }
                }""", full)
            print(f"[probe] {mode} 已派发")
        time.sleep(10)
        print(f"[probe] mode={mode} stream 事件数={hits['stream']} 上传/事件请求数={len(uploads)}")
        for u in uploads[:8]:
            print("   ", u.split(str(port))[-1][:110])
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
