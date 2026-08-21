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


class TestGenerateToShouldStop:
    """web-065：生图费用止血——should_stop 在每次尝试前检查：取消后不重试、不发起新调用。"""

    def test_no_retry_after_cancel(self, monkeypatch, tmp_path):
        calls = []
        stop = {"v": False}
        def boom(**kw):
            calls.append(1)
            stop["v"] = True                       # 第 1 次失败后用户点了返回
            raise RuntimeError("oss 抖动")
        monkeypatch.setattr(story, "_mmconversation_call", boom)
        ok = ImageClient("qwen-image-3.0", "1024*1024", 90).generate_to(
            tmp_path / "a.png", "p", should_stop=lambda: stop["v"])
        assert ok is False and len(calls) == 1     # 取消后不重试（白烧一次重试被堵住）

    def test_no_first_attempt_when_already_stopped(self, monkeypatch, tmp_path):
        calls = []
        def boom(**kw):
            calls.append(1)
            raise RuntimeError("不应被调用")
        monkeypatch.setattr(story, "_mmconversation_call", boom)
        ok = ImageClient("m", "s", 90).generate_to(
            tmp_path / "a.png", "p", should_stop=lambda: True)
        assert ok is False and len(calls) == 0     # 首次尝试前已取消 → 0 次调用


class TestRateLimitBackoff:
    """web-067：429 Throttling.RateQuota 退避重试——实测并发>2 即限流（0.2s 秒拒），
    立即重发只会再撞限流：限流错退避重试 ≤3 次；非限流错立即重试 ≤1 次（原语义）。"""

    def _flaky(self, monkeypatch, seq, calls):
        def f(**kw):
            calls.append(1)
            r = seq.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        monkeypatch.setattr(story, "_mmconversation_call", f)
        monkeypatch.setattr(story, "_download", lambda u, p: p.write_bytes(b"x"))

    def test_429_then_success(self, monkeypatch, tmp_path):
        calls = []
        self._flaky(monkeypatch,
                    [RuntimeError("image HTTP 429: Throttling.RateQuota"), _img_rsp()],
                    calls)
        ok = ImageClient("m", "s", 90, rate_wait_s=0).generate_to(tmp_path / "a.png", "p")
        assert ok is True and len(calls) == 2        # 退避后第 2 次成功

    def test_429_always_fails_after_bounded_retries(self, monkeypatch, tmp_path):
        calls = []
        self._flaky(monkeypatch,
                    [RuntimeError("image HTTP 429: Throttling.RateQuota")] * 4, calls)
        ok = ImageClient("m", "s", 90, rate_wait_s=0).generate_to(tmp_path / "a.png", "p")
        assert ok is False and len(calls) == 4       # 1 + 限流退避重试 3 次封顶

    def test_plain_error_retry_semantics_unchanged(self, monkeypatch, tmp_path):
        calls = []
        self._flaky(monkeypatch, [RuntimeError("oss 抖动")] * 5, calls)
        ok = ImageClient("m", "s", 90, rate_wait_s=0).generate_to(tmp_path / "a.png", "p")
        assert ok is False and len(calls) == 2       # 非限流：立即重试 ≤1 次（防回归）

    def test_429_then_plain_then_plain(self, monkeypatch, tmp_path):
        calls = []
        self._flaky(monkeypatch,
                    [RuntimeError("HTTP 429: Throttling.RateQuota"),
                     RuntimeError("oss 抖动"), RuntimeError("oss 又抖")], calls)
        ok = ImageClient("m", "s", 90, rate_wait_s=0).generate_to(tmp_path / "a.png", "p")
        assert ok is False and len(calls) == 3       # 限流 1 次退避 + 普通立即重试 1 次

    def test_cancel_during_backoff_stops_immediately(self, monkeypatch, tmp_path):
        calls = []
        stop = {"v": False}

        def boom(**kw):
            calls.append(1)
            raise RuntimeError("HTTP 429: Throttling.RateQuota")
        monkeypatch.setattr(story, "_mmconversation_call", boom)

        def fake_sleep(_s):
            stop["v"] = True                         # 退避期间用户点了返回
        monkeypatch.setattr(story.time, "sleep", fake_sleep)
        ok = ImageClient("m", "s", 90, rate_wait_s=6.0).generate_to(
            tmp_path / "a.png", "p", should_stop=lambda: stop["v"])
        assert ok is False and len(calls) == 1       # 退避中取消：不再发起新调用


class TestDownloadAtomic:
    """web-063 终审 F2：_download 原子落盘——中断不留截断残文件、临时文件清理
    （缓存命中 path.exists() 不误用半张图）。"""

    def test_urlopen_failure_leaves_nothing(self, tmp_path, monkeypatch):
        import urllib.request
        def boom(url, timeout=0):
            raise RuntimeError("连接中断")
        monkeypatch.setattr(urllib.request, "urlopen", boom)
        target = tmp_path / "page_1.png"
        with pytest.raises(RuntimeError):
            story._download("https://oss.example/x.png", target)
        assert not target.exists()
        assert list(tmp_path.iterdir()) == []        # 临时文件也被清理

    def test_read_failure_leaves_no_partial(self, tmp_path, monkeypatch):
        import urllib.request
        class _Resp:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self):
                raise RuntimeError("传输中断")
        monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=0: _Resp())
        target = tmp_path / "page_1.png"
        with pytest.raises(RuntimeError):
            story._download("https://oss.example/x.png", target)
        assert not target.exists()
        assert list(tmp_path.iterdir()) == []

    def test_success_writes_via_tmp_no_residue(self, tmp_path, monkeypatch):
        import urllib.request
        class _Resp:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self):
                return b"PNGBYTES"
        monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=0: _Resp())
        target = tmp_path / "page_1.png"
        story._download("https://oss.example/x.png", target)
        assert target.read_bytes() == b"PNGBYTES"
        assert list(tmp_path.iterdir()) == [target]  # 无临时文件残留
