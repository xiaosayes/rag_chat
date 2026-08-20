# web-056：绘本播报状态机——逐页 speak/事件改名/翻页即切/收尾/cancel
import threading
import time
from kiosk_server.story import StorySession, StoryCache


class _Cfg:
    story_min_scenes = 8; story_max_scenes = 10; story_scene_max_chars = 80
    story_image_concurrency = 4; story_total_budget_s = 300.0
    story_closing = "故事讲完啦，还想听什么故事吗？"


class _FakeTTSHandle:
    def __init__(self, on_audio):
        self._on_audio = on_audio
        self.error = None
        self.done = threading.Event()
        self.fed = []
    def feed(self, text):
        self.fed.append(text)
        self._on_audio(b"\x01\x02" * 480)      # 每喂入回 20ms PCM
    def finish(self):
        self.done.set()
    def cancel(self):
        self.done.set()


class _FakeTTS:
    def __init__(self):
        self.handles = []
    def start_stream(self, on_audio):
        h = _FakeTTSHandle(on_audio)
        self.handles.append(h)
        return h


class _FakeScript:
    def generate(self, theme):
        return {"title": "t", "characters": "", "scenes": [f"第{i}幕。" for i in range(1, 9)]}


class _NoopImage:
    def generate_to(self, path, prompt):
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

    def test_out_of_range_page_ignored(self, tmp_path):
        events = []
        s, th = _start(events, tmp_path, _FakeTTS())
        assert _wait(events, lambda e: any(x["type"] == "story_speak_start" for x in e))
        s.on_page(99)
        time.sleep(0.2)
        assert not any(x["type"] == "story_speak_start" and x["n"] == 99 for x in events)
        s.cancel(); th.join(3)
