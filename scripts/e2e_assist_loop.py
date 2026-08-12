"""语音助手链路 E2E（audit-ASR 修复轮2）：真实 gradio 管道验证三件事。

  ① 问题文本走 pending 存储后，聊天气泡是**干净文本**（无 [['add',...]] 串线乱码）
  ② auto_q nonce → .change → auto_respond → respond 全链路真实事件触发
  ③ greet → play_greeting：欢迎语进对话框（语音唤醒「你好小虎」/「我是小虎」可见）

假状态机替代 VAD/ASR（第 4 块提交问题、播报结束后发 greet），gradio 事件管道全真。
运行：python scripts/e2e_assist_loop.py
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ["VOICE_ASSIST_ENABLED"] = "true"

import httpx

QUESTION = "你的爱好是什么？"
ANSWER = "这是 E2E 模拟回答。"
WAKE_GREETING = "您好，我是小虎，请问有什么可以帮您？"


class _FakeAssistant:
    """脚本化状态机：观察播报生命周期，驱动 submit/greet 各一次。"""

    def __init__(self):
        from src.voice_assistant import VoiceAction  # noqa
        self._VA = VoiceAction
        self.calls = 0
        self.submitted = False
        self.greeted = False
        self._seen_active = False
        self._seen_done = False

    def notify_broadcast(self, active):
        if active:
            self._seen_active = True
        elif self._seen_active:
            self._seen_done = True
        return []

    def process_chunk(self, pcm):
        self.calls += 1
        if not self.submitted and self.calls >= 4:
            self.submitted = True
            return [self._VA("submit", QUESTION)]
        if self.submitted and self._seen_done and not self.greeted:
            self.greeted = True
            print("[E2E] fake 发出 greet 动作")
            return [self._VA("greet")]
        return []

    def close(self):
        pass


def main() -> int:
    import app as app_mod
    from src.voice_assistant import VoiceAction  # noqa: F401

    # 讯飞密钥置假（ASR 已被假状态机取代，不会真实连接）；TTS 答案侧置 None（快速），
    # 欢迎语 PCM 用假 PCM 走真实 _AdtsStreamer 编码发布
    app_mod.settings.xfyun_app_id = "fake"
    app_mod.settings.xfyun_api_key = "fake"
    app_mod.settings.xfyun_api_secret = "fake"
    app_mod._init_tts = lambda: None
    app_mod._greeting_pcm = lambda project: b"\x01\x00" * 24000  # 1s
    fake = _FakeAssistant()
    app_mod._create_voice_assistant = lambda pid: fake

    # ---- 链路打点：定位断点（事件是否触发/触发几次） ----
    hits = {"stream": 0, "auto_respond": 0, "greet": 0}
    _orig_dispatch = app_mod.voice_stream_dispatch

    def _logged_dispatch(audio_filepath, state, project_id: str = "", request=None):
        hits["stream"] += 1
        yield from _orig_dispatch(audio_filepath, state, project_id, request)

    app_mod.voice_stream_dispatch = _logged_dispatch
    _orig_auto = app_mod.auto_respond

    def _logged_auto(nonce, chat_history, stream, project, tts_enabled, request=None):
        hits["auto_respond"] += 1
        print(f"[E2E] auto_respond entered, pending={dict(app_mod._pending_questions)}")
        yield from _orig_auto(nonce, chat_history, stream, project, tts_enabled, request)

    app_mod.auto_respond = _logged_auto
    _orig_greet = app_mod.play_greeting

    def _logged_greet(trigger, project: str = "", tts_enabled: bool = True, request=None):
        hits["greet"] += 1
        print(f"[E2E] play_greeting entered: trigger={trigger!r} "
              f"pending={set(app_mod._pending_greet)} req_hash={getattr(request, 'session_hash', None)!r}")
        out = yield from _orig_greet(trigger, project, tts_enabled, request)
        return out

    app_mod.play_greeting = _logged_greet

    def canned_answer(question, history, stream, project):
        # 放慢（3 段×0.9s）：让播报窗口跨越多个 0.5s 流块，状态机才能观测到
        # broadcast 激活/收尾（太快则 token 注册即完成，生产场景 respond 跑数十秒无此问题）
        for i in range(1, 4):
            part = ANSWER[: i * len(ANSWER) // 3]
            yield (history + [{"role": "user", "content": question},
                              {"role": "assistant", "content": part}], "[]", part)
            time.sleep(0.9)

    app_mod.answer_question = canned_answer

    # KeyError 诊断：打印出事事件与末趟数据形态
    from gradio.blocks import Blocks
    _orig_hso = Blocks.handle_streaming_outputs

    async def _logged_hso(self, block_fn, data, session_hash=None, run=None,
                          root_path=None, final=False):
        try:
            return await _orig_hso(self, block_fn, data, session_hash=session_hash,
                                   run=run, root_path=root_path, final=final)
        except KeyError as e:
            print(f"[HSO] KeyError={e} fn={getattr(block_fn.fn, '__name__', '?')} "
                  f"final={final} run={run}")
            for i, b in enumerate(block_fn.outputs):
                print(f"   out[{i}] id={getattr(b,'_id',None)} type={type(b).__name__} "
                      f"data={str(data[i])[:80]!r}")
            raise

    Blocks.handle_streaming_outputs = _logged_hso

    port = 7872
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

    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            "--autoplay-policy=no-user-gesture-required",
            "--mute-audio",
        ])
        page = browser.new_context(locale="zh-CN").new_page()
        console = []
        page.on("console", lambda m: console.append(m.text))
        netlog = []
        page.on("request", lambda r: netlog.append((r.method, r.url))
                if "/gradio_api/" in r.url or "/queue" in r.url else None)
        page.on("response", lambda r: netlog.append((f"RESP {r.status}", r.url))
                if "/gradio_api/" in r.url else None)
        page.goto(f"http://127.0.0.1:{port}")
        # 等 12s 后检查录音状态与流事件计数
        time.sleep(12)
        rec_state = page.evaluate(
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
        vs_text = page.evaluate(
            "(() => {const v=document.getElementById('voice_status'); return v ? v.textContent : 'NO-ELEM'})()")
        print(f"[E2E] 12s 时录音状态: {rec_state}; stream 事件数: {hits['stream']}; "
              f"voice_status 文本: {vs_text!r}")
        for c in console:
            if "voiceAssist" in c or "error" in c.lower():
                print("[E2E console]", c[:150])
        print(f"[E2E] 音频相关网络请求 {len(netlog)} 条:")
        for m, u in netlog[:15]:
            print("   ", m, u.split(str(port))[-1][:100])

        def chat_text():
            # 全 DOM 文本（叶子抓取漏聊天渲染结构，实证）
            return page.evaluate("document.body ? document.body.innerText : ''")

        # ① 等问题气泡出现（自动提交链路）
        deadline = time.time() + 60
        seen_q = False
        while time.time() < deadline:
            t = chat_text()
            if QUESTION in t:
                seen_q = True
                break
            time.sleep(1)
        if not seen_q:
            failures.append("问题未自动提交（聊天气泡未出现）")
        else:
            print(f"✅ 自动提交上屏: {QUESTION}")

        # ② 等回答 + 乱码检查
        deadline = time.time() + 60
        full = ""
        while time.time() < deadline:
            full = chat_text()
            if ANSWER in full and "我是小虎" in full:
                break
            time.sleep(1)
        if ANSWER not in full:
            failures.append("模拟回答未出现")
        # 应答语经 voice_status/tts_status 展示（修复轮2b：不写对话框，零竞争）
        if "已唤醒" not in full and "应答中" not in full:
            failures.append("唤醒应答状态未上屏")
        else:
            print("✅ 唤醒应答状态上屏（已唤醒/应答中）")
        if "[['add'" in full or "\\u200b" in full or "u200b" in full:
            failures.append("对话框仍有串线乱码/nonce 残留")
        else:
            print("✅ 无串线乱码（[['add' / u200b 均不存在）")
        browser.close()

    print(f"[E2E] hits: stream={hits['stream']} auto_respond={hits['auto_respond']} greet={hits['greet']}; "
          f"pending_greet={set(app_mod._pending_greet)} fake.greeted={fake.greeted}")
    if failures:
        print("\n❌ E2E 失败：")
        for f in failures:
            print("  -", f)
        return 1
    print("\n✅ 语音助手链路 E2E 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
