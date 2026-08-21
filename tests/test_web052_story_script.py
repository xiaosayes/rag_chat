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


def _script(n=9, chars=45):      # web-064：默认 ≥45 保 happy path（分镜字数要求 40~80）
    return {"title": "霸王别姬", "characters": "虞姬：年轻女子，梳高髻，穿红色戏服",
            "scenes": ["第%d幕。" % i + "x" * chars for i in range(n)]}


class TestScriptSpeedAndFidelity:
    """web-067：enable_thinking=False 提速；web-070：脚本换型 deepseek-v4-flash-0731
    （实测 8.7s 出全量脚本+images 字段；农夫与蛇主线全对——蛇咬农夫/寓意不反转），
    prompt 同步换为「严格按主流版本+画面描述字段」版。"""

    def test_enable_thinking_disabled(self, monkeypatch):
        calls = []

        def fake_call(**kw):
            calls.append(kw)
            return _ok_rsp(_script())
        monkeypatch.setattr(story, "_generation_call", fake_call)
        ScriptClient("qwen-plus", 1600, 60).generate("守株待兔")
        assert calls[0]["enable_thinking"] is False

    def test_prompt_has_fidelity_and_length_clause(self, monkeypatch):
        calls = []

        def fake_call(**kw):
            calls.append(kw)
            return _ok_rsp(_script())
        monkeypatch.setattr(story, "_generation_call", fake_call)
        ScriptClient("qwen-plus", 1600, 60).generate("守株待兔")
        prompt = calls[0]["messages"][0]["content"]
        # web-070：换型后 prompt 钉忠实条款+画面描述字段要求（原文逐字取自
        # scripts/_diag_ds_script.py NEW_PROMPT——实测 8.7s/农夫与蛇主线全对）
        assert "严格按照大家熟知的主流版本" in prompt
        assert "40 到 80" in prompt                  # 分镜字数要求（web-064）
        assert "15 到 25" in prompt                  # 画面描述长度要求（web-070）
        assert "images" in prompt                    # 画面描述字段（web-070）
        assert "农夫与蛇" in prompt                  # 忠实示例列举防漏（寓意反转事故主题）


class TestScriptImages:
    """web-070：images 画面描述字段解析——生图 prompt 用 15~25 字纯画面短句
    （实测整段叙述 prose 喂图会被模型当文字渲染=插图乱码根因）；
    缺失/长度不符→None 回退（worker 侧用 scene 剥引语兑底）。"""

    def _payload(self, n=8, with_images=True):
        p = _script(n)
        if with_images:
            p["images"] = [f"画面{i}：虞姬在帐中。" for i in range(1, n + 1)]
        return p

    def test_images_parsed_and_aligned(self, monkeypatch):
        monkeypatch.setattr(story, "_generation_call",
                            lambda **kw: _ok_rsp(self._payload()))
        out = ScriptClient("deepseek-v4-flash-0731", 2200, 60).generate("霸王别姬")
        assert out["images"] is not None
        assert len(out["images"]) == len(out["scenes"])
        assert out["images"][0].startswith("画面1")

    def test_images_sanitized(self, monkeypatch):
        p = self._payload()
        p["images"][0] = "虞姬说：「大王饶命」，《霸王别姬》开场。"
        monkeypatch.setattr(story, "_generation_call", lambda **kw: _ok_rsp(p))
        out = ScriptClient("m", 2200, 60).generate("霸王别姬")
        assert "大王饶命" not in out["images"][0]          # 引语剥除
        assert "《" not in out["images"][0] and "》" not in out["images"][0]

    def test_images_missing_falls_back_none(self, monkeypatch):
        monkeypatch.setattr(story, "_generation_call",
                            lambda **kw: _ok_rsp(self._payload(with_images=False)))
        out = ScriptClient("m", 2200, 60).generate("霸王别姬")
        assert out["images"] is None
        assert len(out["scenes"]) == 8                     # scenes 行为不变

    def test_images_length_mismatch_falls_back_none(self, monkeypatch):
        p = self._payload()
        p["images"] = p["images"][:-1]                     # 7 vs 8 不齐
        monkeypatch.setattr(story, "_generation_call", lambda **kw: _ok_rsp(p))
        out = ScriptClient("m", 2200, 60).generate("霸王别姬")
        assert out["images"] is None

    def test_retry_message_mentions_images(self, monkeypatch):
        calls = []

        def fake(**kw):
            calls.append(kw)
            if len(calls) == 1:
                return _ok_rsp({"title": "t", "characters": "", "scenes": ["短"] * 8})
            return _ok_rsp(self._payload())
        monkeypatch.setattr(story, "_generation_call", fake)
        ScriptClient("m", 2200, 60).generate("霸王别姬")
        retry_msg = calls[1]["messages"][-1]["content"]
        assert "images" in retry_msg                      # 重试指引补画面字段要求


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

    def test_moderation_http_path_not_retried(self, monkeypatch):
        """web-052 补强（I-1）：真实审核拦截通常不抛异常——Generation.call 返回
        status_code=400, code=DataInspectionFailed。走真 _generation_call 包装路径，
        必须重归类 StoryModerationError 且不可重试（底层只调 1 次）。"""
        calls = []

        class R:
            status_code = 400
            code = "DataInspectionFailed"

        def fake_generation_call(**kw):
            calls.append(kw)
            return R()

        import dashscope
        monkeypatch.setattr(dashscope.Generation, "call", fake_generation_call)
        with pytest.raises(StoryModerationError):
            ScriptClient("qwen-plus", 1600, 60).generate("t")
        assert len(calls) == 1                         # 审核不重试

    def test_short_scenes_retry_then_accept(self, monkeypatch):
        """web-064：分镜 <40 字=校验不合格重试 1 次；重试后仍短→接受+告警（等图兜底）。"""
        calls = []

        def fake_call(**kw):
            calls.append(kw)
            return _ok_rsp(_script(chars=30))          # 每段 34 字 <40

        monkeypatch.setattr(story, "_generation_call", fake_call)
        s = ScriptClient("qwen-plus", 1600, 60).generate("t")
        assert len(calls) == 2                         # 首轮短段触发重试
        assert "40" in calls[1]["messages"][-1]["content"]   # 修正意见含字数下限
        assert len(s["scenes"]) == 9                  # 仍短则接受（段数不变）

    def test_prompt_rules(self):
        p = story.SCRIPT_SYSTEM_PROMPT
        for kw in ("儿童", "8", "10", "40", "80", "JSON", "characters", "健康"):
            assert kw in p
