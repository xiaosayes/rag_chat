# web-056：绘本播报状态机——逐页 speak/事件改名/翻页即切/收尾/cancel
import threading
import time
from kiosk_server.story import StorySession, StoryCache


class _Cfg:
    story_min_scenes = 8; story_max_scenes = 10; story_scene_max_chars = 80
    story_image_concurrency = 4; story_total_budget_s = 300.0
    story_closing = "故事讲完啦，还想听什么故事吗？"


class _FakeTTSHandle:
    def __init__(self, on_audio, gate=None):
        self._on_audio = on_audio
        self._gate = gate                        # 非 None：finish 挂起直到放行（模拟在播）
        self.error = None
        self.done = threading.Event()
        self.fed = []
    def feed(self, text):
        self.fed.append(text)
        self._on_audio(b"\x01\x02" * 480)      # 每喂入回 20ms PCM
    def finish(self):
        if self._gate is not None:
            self._gate.wait(5.0)                 # 挂起排水（daemon 线程内，有界）
        self.done.set()
    def cancel(self):
        self.done.set()


class _FakeTTS:
    def __init__(self, hang_first: threading.Event | None = None):
        self.handles = []
        self._hang_first = hang_first            # 仅第 1 个 handle 挂起
    def start_stream(self, on_audio):
        gate = self._hang_first if not self.handles else None
        h = _FakeTTSHandle(on_audio, gate=gate)
        self.handles.append(h)
        return h


class _FakeScript:
    def generate(self, theme):
        return {"title": "t", "characters": "", "scenes": [f"第{i}幕。" for i in range(1, 9)]}


class _NoopImage:
    def generate_to(self, path, prompt, should_stop=None):   # web-065：签名对齐
        return False                              # 本任务不关心图


def _start(events, tmp_path, tts):
    s = StorySession(events.append, _FakeScript(), _NoopImage(),
                     StoryCache(str(tmp_path), 500), tts_factory=lambda: tts,
                     cfg=_Cfg())
    th = threading.Thread(target=s.start, args=("霸王别姬",), daemon=True)
    th.start()
    return s, th


def _wait(events, pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred(events):
            return True
        time.sleep(0.01)
    return False


class TestSpeak:
    def test_page1_auto_speak_and_events(self, tmp_path):
        events = []
        s, th = _start(events, tmp_path, _FakeTTS())
        assert _wait(events, lambda e: any(x["type"] == "story_speak_start" and x["n"] == 1 for x in e))
        assert _wait(events, lambda e: any(x["type"] == "story_speak_end" and x["n"] == 1
                                           and x["cancelled"] is False for x in e))
        assert any(x["type"] == "audio_start" for x in events)        # 音频事件透传
        assert not any(x["type"] == "answer_chunk" for x in events)   # chunk 抑制
        s.cancel(); th.join(3)

    def test_manual_flip_barges_current_page(self, tmp_path):
        events = []
        s, th = _start(events, tmp_path, _FakeTTS())
        assert _wait(events, lambda e: any(x["type"] == "story_speak_start" for x in e))
        s.on_page(3)
        assert _wait(events, lambda e: any(x["type"] == "story_speak_start" and x["n"] == 3 for x in e))
        starts = [x["n"] for x in events if x["type"] == "story_speak_start"]
        assert starts[-1] == 3 and 2 not in starts        # 直达第 3 页，无中间页
        s.cancel(); th.join(3)

    def test_finish_speaks_closing_then_end(self, tmp_path):
        events = []
        tts = _FakeTTS()
        s, th = _start(events, tmp_path, tts)
        assert _wait(events, lambda e: any(x["type"] == "story_speak_start" for x in e))
        s.on_finish()
        assert _wait(events, lambda e: any(x["type"] == "story_end" and x["reason"] == "done" for x in e))
        fed = "".join(seg for h in tts.handles for seg in h.fed)
        assert "故事讲完啦" in fed
        th.join(3)

    def test_cancel_stops_and_emits(self, tmp_path):
        events = []
        s, th = _start(events, tmp_path, _FakeTTS())
        assert _wait(events, lambda e: any(x["type"] == "story_speak_start" for x in e))
        s.cancel()
        assert _wait(events, lambda e: any(x["type"] == "story_end"
                                           and x["reason"] == "cancelled" for x in e))
        th.join(3)

    def test_finish_while_page_speaking(self, tmp_path, monkeypatch):
        """web-056 补强：第 1 页仍在播时 on_finish——先排空再播收尾语。
        修复前复现：_speak 的 busy 等待被旧轮 busy 蒙混，_wait_speak_done 在
        串行化空窗（chat 侧 0.05s 轮询间隙）采样到 busy==False 提前返回，
        story_end{done} 抢跑、收尾语被 start() finally 的 close() 裁掉。"""
        import time as _t
        import kiosk_server.chat as chat_mod

        class _ChatTimeShim:   # 只放慢 chat 侧节拍：串行化空窗拉大至 0.3s（确定性复现）
            @staticmethod
            def sleep(_s):
                _t.sleep(0.3)

            @staticmethod
            def monotonic():
                return _t.monotonic()

        monkeypatch.setattr(chat_mod, "time", _ChatTimeShim)

        gate = threading.Event()
        events = []
        tts = _FakeTTS(hang_first=gate)
        s, th = _start(events, tmp_path, tts)
        assert _wait(events, lambda e: any(x["type"] == "story_speak_start" and x["n"] == 1 for x in e))
        assert _wait(events, lambda e: bool(tts.handles) and len(tts.handles[0].fed) > 0)
        s.on_finish()                                        # 第 1 页仍在播（排水挂起）
        assert _wait(events, lambda e: any(x["type"] == "story_end" and x["reason"] == "done"
                                           for x in e), timeout=10.0)
        gate.set()
        fed = "".join(seg for h in tts.handles for seg in h.fed)
        assert "故事讲完啦" in fed                            # 收尾语完整喂入（未被裁）
        idx_close = next(i for i, x in enumerate(events)
                         if x["type"] == "story_speak_end" and x["n"] == 0
                         and x["cancelled"] is False)
        idx_end = next(i for i, x in enumerate(events)
                       if x["type"] == "story_end" and x["reason"] == "done")
        assert idx_close < idx_end                           # 收尾语播尽才有 story_end
        th.join(3)

    def test_out_of_range_page_ignored(self, tmp_path):
        events = []
        s, th = _start(events, tmp_path, _FakeTTS())
        assert _wait(events, lambda e: any(x["type"] == "story_speak_start" for x in e))
        s.on_page(99)
        time.sleep(0.2)
        assert not any(x["type"] == "story_speak_start" and x["n"] == 99 for x in events)
        s.cancel(); th.join(3)
