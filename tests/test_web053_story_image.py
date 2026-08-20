# tests/test_web053_story_image.py
# web-053：插图客户端——prompt 模板/严格遵循（prompt_extend=False）/重试 1 次/超时/落盘
import pytest
from kiosk_server import story
from kiosk_server.story import ImageClient, build_image_prompt


def _img_rsp(url="https://oss.example/p.png"):
    class R:
        status_code = 200
        output = type("O", (), {"choices": [
            type("C", (), {"message": type("M", (), {
                "content": [{"image": url}]})})]})
    return R()


class TestBuildPrompt:
    def test_assembly(self):
        p = build_image_prompt("虞姬：高髻红衣", "虞姬在帐中舞剑")
        assert "虞姬：高髻红衣" in p and "虞姬在帐中舞剑" in p
        assert "绘本" in p and "文字" in p          # 风格锚 + 负向约束
    def test_no_characters(self):
        p = build_image_prompt("", "森林里的小鹿")
        assert "森林里的小鹿" in p


class TestGenerateTo:
    def test_success_downloads(self, monkeypatch, tmp_path):
        monkeypatch.setattr(story, "_mmconversation_call", lambda **kw: _img_rsp())
        monkeypatch.setattr(story, "_download", lambda url, path: path.write_bytes(b"PNG"))
        ok = ImageClient("qwen-image-3.0", "1024*1024", 90).generate_to(
            tmp_path / "page_1.png", "prompt")
        assert ok and (tmp_path / "page_1.png").read_bytes() == b"PNG"

    def test_params_pinned(self, monkeypatch, tmp_path):
        seen = {}
        def fake(**kw):
            seen.update(kw)
            return _img_rsp()
        monkeypatch.setattr(story, "_mmconversation_call", fake)
        monkeypatch.setattr(story, "_download", lambda u, p: p.write_bytes(b"x"))
        ImageClient("qwen-image-3.0", "1024*1024", 90).generate_to(tmp_path / "a.png", "p")
        assert seen["model"] == "qwen-image-3.0"
        assert seen["prompt_extend"] is False and seen["size"] == "1024*1024"
        assert seen["messages"][0]["content"][0]["text"] == "p"

    def test_retry_once_then_fail(self, monkeypatch, tmp_path):
        calls = []
        def boom(**kw):
            calls.append(1)
            raise RuntimeError("oss 抖动")
        monkeypatch.setattr(story, "_mmconversation_call", boom)
        ok = ImageClient("qwen-image-3.0", "1024*1024", 90).generate_to(tmp_path / "a.png", "p")
        assert ok is False and len(calls) == 2       # 重试 1 次后放弃（不抛）

    def test_retry_once_then_success(self, monkeypatch, tmp_path):
        seq = [RuntimeError("x"), _img_rsp()]
        def flaky(**kw):
            r = seq.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        monkeypatch.setattr(story, "_mmconversation_call", flaky)
        monkeypatch.setattr(story, "_download", lambda u, p: p.write_bytes(b"x"))
        assert ImageClient("m", "s", 90).generate_to(tmp_path / "a.png", "p") is True
