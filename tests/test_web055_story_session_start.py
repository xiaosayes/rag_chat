# tests/test_web055_story_session_start.py
# web-055：StorySession 启动链路——preparing→begin（缓存命中/新生成）→逐图事件
import threading

from kiosk_server import story
from kiosk_server.story import StoryCache, StoryScriptError, StorySession


class _Cfg:
    story_min_scenes = 8; story_max_scenes = 10; story_scene_max_chars = 80
    story_image_concurrency = 4; story_total_budget_s = 300.0
    story_closing = "故事讲完啦，还想听什么故事吗？"


class _FakeScript:
    def __init__(self, script=None, err=None):
        self._script, self._err, self.calls = script, err, []

    def generate(self, theme):
        self.calls.append(theme)
        if self._err:
            raise self._err
        return self._script


def _script(n=8):
    return {"title": "霸王别姬", "characters": "虞姬",
            "scenes": [f"第{i}幕。" for i in range(1, n + 1)]}


class _FakeImage:
    def __init__(self, ok=True):
        self.ok, self.prompts, self._lock = ok, [], threading.Lock()

    def generate_to(self, path, prompt, should_stop=None):   # web-065：签名对齐
        with self._lock:
            self.prompts.append(prompt)
        if self.ok:
            from pathlib import Path
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"PNG")
        return self.ok


def _make(events, script=None, img=None, tmp_path=None, speak=None):
    cache = StoryCache(str(tmp_path), 500)
    s = StorySession(events.append, script or _FakeScript(_script()),
                     img or _FakeImage(), cache, tts_factory=None, cfg=_Cfg(),
                     speak_fn=speak or (lambda n: None))
    return s, cache


def _drive(s, theme="霸王别姬"):
    """启动链路时序（web-055 实施注意）：线程跑 start → 等插图编排收尾
    （假件秒级完成，消除 story_end 与逐图事件的竞态）→ finish 指令 → 等 start 返回。"""
    th = threading.Thread(target=s.start, args=(theme,), daemon=True)
    th.start()
    assert s._img_done.wait(5.0), "插图编排未在 5s 内收尾"
    s.on_finish()
    assert s.wait_idle(5.0), "start() 未在 5s 内返回"
    th.join(3)


def _types(events):
    return [e["type"] for e in events]


class TestStart:
    def test_fresh_flow(self, tmp_path):
        events = []
        s, _ = _make(events, tmp_path=tmp_path)
        _drive(s)
        t = _types(events)
        assert t[0] == "story_preparing" and events[0]["theme"] == "霸王别姬"
        begin = events[t.index("story_begin")]
        assert begin["total"] == 8 and begin["cached"] is False
        assert begin["pages"][0] == {"n": 1, "text": "第1幕。"}
        imgs = [e for e in events if e["type"] == "story_page_img"]
        assert len(imgs) == 8
        assert all(e["url"].startswith(f"/api/story/{begin['story_id']}/img/") for e in imgs)
        assert events[-1]["type"] == "story_end" and events[-1]["reason"] == "done"

    def test_cached_replay_skips_llm_and_images(self, tmp_path):
        events = []
        s, cache = _make(events, tmp_path=tmp_path)
        _drive(s)
        # 第二轮：同主题 → 命中缓存（LLM 不再调用、图片全已在盘 → 直接发 img 事件）
        script2 = _FakeScript(_script())
        img2 = _FakeImage()
        events2 = []
        s2, _ = _make(events2, script=script2, img=img2, tmp_path=tmp_path)
        _drive(s2, " 霸王别姬！")
        assert script2.calls == []                       # 跳 LLM
        assert img2.prompts == []                        # 已落盘的图跳生成
        begin = [e for e in events2 if e["type"] == "story_begin"][0]
        assert begin["cached"] is True
        assert len([e for e in events2 if e["type"] == "story_page_img"]) == 8

    def test_partial_cache_backfills_missing_images(self, tmp_path):
        events = []
        s, cache = _make(events, tmp_path=tmp_path)
        _drive(s)
        sid = StoryCache.story_id("霸王别姬")
        cache.image_path(sid, 3).unlink()                # 制造缺图缓存
        img2 = _FakeImage()
        events2 = []
        s2, _ = _make(events2, img=img2, tmp_path=tmp_path)
        _drive(s2)
        assert len(img2.prompts) == 1                    # 只补第 3 页

    def test_failed_image_emits_failed_event(self, tmp_path):
        """web-064：插图最终失败补发 failed 事件（前端据此放行自动翻页，不卡页）。"""
        events = []
        img = _FakeImage(ok=False)
        s, _ = _make(events, img=img, tmp_path=tmp_path)
        _drive(s)
        imgs = [e for e in events if e["type"] == "story_page_img"]
        assert len(imgs) == 8
        assert all(e["failed"] is True and e["url"] is None for e in imgs)
        assert [e for e in events if e["type"] == "story_end"]   # 照常讲完

    def test_script_failure_emits_error(self, tmp_path):
        events = []
        s, _ = _make(events, script=_FakeScript(err=StoryScriptError("x")),
                     tmp_path=tmp_path)
        s.start("霸王别姬")
        t = _types(events)
        assert "story_error" in t and events[t.index("story_error")]["code"] == "script_failed"
        assert "story_begin" not in t

    def test_image_prompt_carries_characters(self, tmp_path):
        events = []
        img = _FakeImage()
        s, _ = _make(events, img=img, tmp_path=tmp_path)
        _drive(s)
        assert all("虞姬" in p for p in img.prompts)     # 跨图一致性：角色锚定每张携带


class TestCancelStopsNewImages:
    """web-065：返回取消后生图止血——取消后不再发起任何新生成（未开始页 0 次新调用）。"""

    def test_cancel_stops_new_image_generation(self, tmp_path):
        gate = threading.Event()
        started = threading.Event()
        calls = []
        lock = threading.Lock()

        class _GatedImage:
            def generate_to(self, path, prompt, should_stop=None):
                with lock:
                    calls.append(prompt)
                    if len(calls) >= 4:
                        started.set()            # 首批并发 4 张全部在途
                gate.wait(5.0)                   # 挂起等测试放行（有界防死）
                from pathlib import Path
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_bytes(b"PNG")
                return True

        events = []
        s, _ = _make(events, img=_GatedImage(), tmp_path=tmp_path)
        th = threading.Thread(target=s.start, args=("霸王别姬",), daemon=True)
        th.start()
        assert started.wait(5.0), "首批 4 张插图未发起"
        s.cancel()
        gate.set()
        assert s.wait_idle(5.0), "start() 未在 5s 内返回"
        th.join(3)
        with lock:
            total = len(calls)
        assert total <= 4, f"取消后仍发起新生图（共 {total} 次调用）"
        ends = [e for e in events if e["type"] == "story_end"]
        assert ends and ends[-1]["reason"] == "cancelled"


class TestEvictionWired:
    """web-063 终审 F1：LRU 接线——start 流程必须调用 evict_if_needed（500MB 上限名存实亡）。"""

    def test_evict_called_during_story_flow(self, tmp_path, monkeypatch):
        events = []
        s, cache = _make(events, tmp_path=tmp_path)
        calls = []
        monkeypatch.setattr(cache, "evict_if_needed", lambda: calls.append(1))
        _drive(s)
        assert calls, "evict_if_needed 从未被调用（LRU 未接线）"


class TestCancelDuringPreparing:
    """web-063 终审 F5：准备期取消不反弹——script 阻塞期间 cancel：
    无 story_begin、无 story_speak_start、有 story_end{cancelled}。"""

    def test_cancel_before_begin_no_rebound(self, tmp_path):
        gate = threading.Event()

        class _SlowScript:
            def __init__(self):
                self.entered = threading.Event()

            def generate(self, theme):
                self.entered.set()
                gate.wait(5.0)                       # 等测试线程 cancel 后再返回
                return _script()

        events = []
        script = _SlowScript()
        s, _ = _make(events, script=script, tmp_path=tmp_path)
        th = threading.Thread(target=s.start, args=("霸王别姬",), daemon=True)
        th.start()
        assert script.entered.wait(2.0), "script.generate 未被调用"
        s.cancel()
        gate.set()
        assert s.wait_idle(5.0), "start() 未在 5s 内返回"
        th.join(3)
        t = _types(events)
        assert "story_begin" not in t
        assert "story_speak_start" not in t
        ends = [e for e in events if e["type"] == "story_end"]
        assert ends and ends[-1]["reason"] == "cancelled"
