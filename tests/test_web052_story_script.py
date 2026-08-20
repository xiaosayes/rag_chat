# tests/test_web052_story_script.py
# web-052：分镜脚本——JSON 解析/校验/重试 1 次/确定性钳制/审核与失败分类
import json
import pytest
from kiosk_server import story
from kiosk_server.story import ScriptClient, StoryModerationError, StoryScriptError


def _ok_rsp(payload: dict):
    class R:
        status_code = 200
        output = type("O", (), {"choices": [
            type("C", (), {"message": type("M", (), {
                "content": json.dumps(payload, ensure_ascii=False)})})]})
    return R()


def _script(n=9, chars=30):
    return {"title": "霸王别姬", "characters": "虞姬：年轻女子，梳高髻，穿红色戏服",
            "scenes": ["第%d幕。" % i + "x" * chars for i in range(n)]}


class TestScriptGenerate:
    def test_parses_fenced_json(self, monkeypatch):
        calls = []
        def fake_call(**kw):
            calls.append(kw)
            return _ok_rsp(_script())
        monkeypatch.setattr(story, "_generation_call", fake_call)
        s = ScriptClient("qwen-plus", 1600, 60).generate("霸王别姬")
        assert s["title"] == "霸王别姬" and len(s["scenes"]) == 9
        assert "虞姬" in s["characters"]
        assert calls[0]["max_tokens"] == 1600 and calls[0]["model"] == "qwen-plus"

    def test_retry_once_on_bad_json(self, monkeypatch):
        seq = [ValueError("bad json"), _ok_rsp(_script())]
        def fake_call(**kw):
            r = seq.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        monkeypatch.setattr(story, "_generation_call", fake_call)
        s = ScriptClient("qwen-plus", 1600, 60).generate("t")
        assert len(s["scenes"]) == 9

    def test_clamp_overlong_scene_and_count(self, monkeypatch):
        bad = _script(n=12, chars=120)     # 12 段且每段超 80 字
        monkeypatch.setattr(story, "_generation_call", lambda **kw: _ok_rsp(bad))
        s = ScriptClient("qwen-plus", 1600, 60).generate("t")
        assert len(s["scenes"]) == 10                       # 段数切 10
        assert all(len(x) <= 80 for x in s["scenes"])       # 句边界截 80

    def test_too_few_scenes_fails(self, monkeypatch):
        monkeypatch.setattr(story, "_generation_call", lambda **kw: _ok_rsp(_script(n=3)))
        with pytest.raises(StoryScriptError):
            ScriptClient("qwen-plus", 1600, 60).generate("t")

    def test_moderation_classified(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError("data inspection failed: content filter")
        monkeypatch.setattr(story, "_generation_call", boom)
        with pytest.raises(StoryModerationError):
            ScriptClient("qwen-plus", 1600, 60).generate("t")

    def test_prompt_rules(self):
        p = story.SCRIPT_SYSTEM_PROMPT
        for kw in ("儿童", "8", "10", "80", "JSON", "characters", "健康"):
            assert kw in p
