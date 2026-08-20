# tests/test_web057_story_voice.py
# web-057：VoiceSession 故事集成——ask 单点拦截/故事态帧静默/barge 忽略/退出复原
from kiosk_server.voice import VoiceSession


class _FakePipeline:
    def query_stream(self, question, conversation_history=None):
        yield "问答答案。"


class _FakeStory:
    def __init__(self, emit):
        self.emit, self.started, self.cancelled = emit, [], 0
        self.active = False
    def start(self, theme):
        self.active = True
        self.started.append(theme)
        self.emit({"type": "story_preparing", "theme": theme})
        self.emit({"type": "story_end", "reason": "done"})
        self.active = False
    def on_page(self, n): pass
    def on_finish(self): pass
    def cancel(self):
        self.cancelled += 1
        self.active = False
    def close(self): pass


def _make(events):
    vs = VoiceSession(_FakePipeline(), None, None, events.append,
                      greeting_pcm_fn=None, sync_audio=True)
    story = _FakeStory(events.append)
    vs.set_story_session(lambda emit: story)
    return vs, story


class TestStoryRouting:
    def test_story_intent_routes_to_story(self):
        events = []
        vs, story = _make(events)
        vs.ask("给我讲一个霸王别姬的故事")
        assert story.started == ["霸王别姬"]
        assert any(e["type"] == "story_preparing" for e in events)
        assert not any(e["type"] == "answer_start" for e in events)   # 未进问答

    def test_normal_question_untouched(self):
        events = []
        vs, story = _make(events)
        vs.ask("图书馆几点关门")
        assert story.started == []
        assert any(e["type"] == "answer_start" for e in events)

    def test_audio_frames_dropped_in_story_mode(self):
        events = []
        vs, story = _make(events)
        vs.set_story_mode(True)
        vs.feed_audio(b"\x00" * 640)
        assert not events                                  # assistant=None 本应报错，静默=生效
        vs.set_story_mode(False)
        vs.feed_audio(b"\x00" * 640)
        assert any(e.get("code") == "voice_unavailable" for e in events)

    def test_barge_ignored_in_story_mode(self):
        events = []
        vs, story = _make(events)
        vs.set_story_mode(True)
        vs.barge_in()
        assert story.cancelled == 0                        # 不级联取消故事

    def test_close_in_story_mode_full_cleanup(self):
        # web-057 补强：close 是会话生命周期行为——故事态 barge guard 不得阻断清理
        events = []
        vs, story = _make(events)
        called = []
        orig_barge = vs._broadcast.barge_in
        vs._broadcast.barge_in = lambda: (called.append(1), orig_barge())
        vs.set_story_mode(True)
        vs._story = story
        story.active = True
        vs.close()
        assert story.cancelled == 1                        # 故事实例被取消（线程/TTS 不泄漏）
        assert called == [1]                               # _broadcast.barge_in 未被阻断
        assert vs._greet_cancel.is_set()                   # 应答取消置位
        assert vs._closed is True
        vs.feed_audio(b"\x00" * 640)                       # close 后帧丢弃（_closed 短路）
        assert not events

    def test_close_without_story_unchanged(self):
        # 非故事态 close 语义与修复前一致；未启动的故事实例不被误伤
        events = []
        vs, story = _make(events)
        called = []
        orig_barge = vs._broadcast.barge_in
        vs._broadcast.barge_in = lambda: (called.append(1), orig_barge())
        vs.close()
        assert called == [1]
        assert story.cancelled == 0
        assert vs._closed is True

    def test_ask_during_story_defensive_cancel(self):
        events = []
        vs, story = _make(events)
        vs.set_story_mode(True)
        vs._story = story    # 模拟故事实例在进行（真实流由 _start_story 赋值；brief 原测试缺此线）
        story.active = True
        vs.ask("图书馆几点关门")                           # 非故事文本 → 取消故事后照常问答
        assert story.cancelled == 1
        assert any(e["type"] == "answer_start" for e in events)
