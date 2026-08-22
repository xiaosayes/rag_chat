# tests/test_web074_story_intent_llm.py
# web-074：意图分层闸——负向元问题拦截 + 正则快路径 + 安全问答信号 + LLM 兜底分类。
# 目标：只要意图是讲故事，不管怎么提问都能识别（泛化性）；同时不抢问答、不加明显延迟。
import pytest

from kiosk_server import story
from kiosk_server.story import classify_intent_llm, resolve_story_intent


def _classify_recorder(result):
    calls = []

    def fn(text):
        calls.append(text)
        return result

    fn.calls = calls
    return fn


class TestResolveGate:
    """分层闸顺序：meta 拦截 → 正则快路径 → 安全问答信号 → LLM 兜底。"""

    def test_regex_fast_path_skips_llm(self):
        fn = _classify_recorder({"intent": "qa", "theme": ""})
        assert resolve_story_intent("给我讲一个霸王别姬的故事", classify=fn) == "霸王别姬"
        assert fn.calls == []                              # 快路径零 LLM 延迟

    def test_meta_questions_blocked_without_llm(self):
        fn = _classify_recorder({"intent": "story", "theme": "X"})
        for q in ["你会讲故事吗", "你能讲故事吗", "湘小图会不会讲绘本",
                  "什么是绘本故事", "这个故事讲了什么", "你都会讲什么故事"]:
            assert resolve_story_intent(q, classify=fn) is None, q
        assert fn.calls == []                              # 元问题直接拦，不进 LLM

    def test_safe_qa_signals_skip_llm(self):
        fn = _classify_recorder({"intent": "story", "theme": "恐龙"})
        for q in ["图书馆几点关门", "恐龙有哪些种类", "周末活动怎么预约",
                  "为什么天空是蓝色的", "家博会在哪里"]:
            assert resolve_story_intent(q, classify=fn) is None, q
        assert fn.calls == []

    def test_ambiguous_story_recognized_via_llm(self):
        fn = _classify_recorder({"intent": "story", "theme": "嫦娥奔月"})
        assert resolve_story_intent("我想听嫦娥奔月", classify=fn) == "嫦娥奔月"
        assert fn.calls == ["我想听嫦娥奔月"]

    def test_ambiguous_story_variant_via_llm(self):
        fn = _classify_recorder({"intent": "story", "theme": "后羿射日"})
        assert resolve_story_intent("给我讲讲后羿射日吧", classify=fn) == "后羿射日"

    def test_ambiguous_qa_via_llm(self):
        fn = _classify_recorder({"intent": "qa", "theme": ""})
        assert resolve_story_intent("袋鼠宝宝住在口袋里吗", classify=fn) is None

    def test_llm_failure_falls_back_to_none(self):
        def boom(_text):
            raise RuntimeError("LLM down")

        assert resolve_story_intent("我想听嫦娥奔月", classify=boom) is None

    def test_llm_story_without_theme_returns_none(self):
        fn = _classify_recorder({"intent": "story", "theme": ""})
        assert resolve_story_intent("随便给我讲一个", classify=fn) is None

    def test_meta_signals_inside_story_theme_not_blocked(self):
        """评审 Minor-1：故事主题内含 会不会/什么是 不误杀（快路径应正常出主题）。"""
        fn = _classify_recorder({"intent": "qa", "theme": ""})
        assert resolve_story_intent("讲一个丑小鸭会不会变天鹅的故事", classify=fn) is not None
        assert resolve_story_intent("我想听小熊能不能飞的故事", classify=fn) is not None
        assert fn.calls == []

    def test_no_classifier_keeps_regex_only_behavior(self):
        assert resolve_story_intent("我想听嫦娥奔月") is None        # 正则不中即 None（旧语义）
        assert resolve_story_intent("给我讲一个嫦娥奔月的故事") == "嫦娥奔月"


class TestVoiceIntegration:
    """voice.ask 接线：注入分类器后模糊故事表达进绘本，元问题/问答不抢。"""

    def _make(self, events):
        from kiosk_server.voice import VoiceSession

        class _FakePipeline:
            def query_stream(self, question, conversation_history=None):
                yield "问答答案。"

        class _FakeStory:
            def __init__(self):
                self.started = []
            def start(self, theme):
                self.started.append(theme)
            def on_page(self, n): pass
            def on_finish(self): pass
            def cancel(self): pass
            def close(self): pass
            @property
            def active(self):
                return False

        vs = VoiceSession(_FakePipeline(), None, None, events.append,
                          greeting_pcm_fn=None, sync_audio=True)
        story = _FakeStory()
        vs.set_story_session(lambda emit: story)
        return vs, story

    def test_ambiguous_story_via_classifier(self):
        events = []
        vs, story = self._make(events)
        vs.set_story_intent_classifier(
            lambda t: {"intent": "story", "theme": "嫦娥奔月"})
        vs.ask("我想听嫦娥奔月")
        assert story.started == ["嫦娥奔月"]
        assert not any(e["type"] == "answer_start" for e in events)

    def test_meta_question_not_intercepted(self):
        events = []
        vs, story = self._make(events)
        fn_called = []
        vs.set_story_intent_classifier(
            lambda t: fn_called.append(t) or {"intent": "story", "theme": "X"})
        vs.ask("你会讲故事吗")
        assert story.started == []
        assert fn_called == []                             # meta 拦截不进 LLM
        assert any(e["type"] == "answer_start" for e in events)

    def test_no_classifier_regex_only(self):
        events = []
        vs, story = self._make(events)
        vs.ask("我想听嫦娥奔月")                           # 无分类器：正则未中→问答（旧语义）
        assert story.started == []
        assert any(e["type"] == "answer_start" for e in events)


class TestClassifyIntentLlm:
    def _ok_rsp(self, payload: dict):
        import json as _json

        class R:
            status_code = 200
            output = type("O", (), {"choices": [
                type("C", (), {"message": type("M", (), {
                    "content": _json.dumps(payload, ensure_ascii=False)})})]})
        return R()

    def test_parse_story_with_theme(self, monkeypatch):
        seen = {}

        def fake(**kw):
            seen.update(kw)
            return self._ok_rsp({"intent": "story", "theme": "嫦娥奔月"})

        monkeypatch.setattr(story, "_generation_call", fake)
        out = classify_intent_llm("我想听嫦娥奔月", model="deepseek-v4-flash-0731")
        assert out == {"intent": "story", "theme": "嫦娥奔月"}
        assert seen.get("enable_thinking") is False
        prompt = seen["messages"][0]["content"]
        assert "意图" in prompt and "story" in prompt and "qa" in prompt

    def test_parse_qa(self, monkeypatch):
        monkeypatch.setattr(story, "_generation_call",
                            lambda **kw: self._ok_rsp({"intent": "qa", "theme": ""}))
        assert classify_intent_llm("袋鼠宝宝住在口袋里吗", model="m")["intent"] == "qa"

    def test_invalid_intent_enum_raises(self, monkeypatch):
        """合法 JSON 但非法 intent 枚举 → 拒绝（评审 Minor-3 补钉）。"""
        monkeypatch.setattr(story, "_generation_call",
                            lambda **kw: self._ok_rsp({"intent": "chat", "theme": ""}))
        with pytest.raises(Exception):
            classify_intent_llm("随便", model="m")

    def test_garbage_payload_raises(self, monkeypatch):
        class R:
            status_code = 200
            output = type("O", (), {"choices": [
                type("C", (), {"message": type("M", (), {"content": ["不是JSON"]})})]})

        monkeypatch.setattr(story, "_generation_call", lambda **kw: R())
        with pytest.raises(Exception):
            classify_intent_llm("随便", model="m")
