# tests/test_web058_story_ws_api.py
# web-058：WS 故事路由 + 供图端点 + token 查询参数
import base64
import pytest
from fastapi.testclient import TestClient
from kiosk_server.app import create_app
from kiosk_server.config import KioskConfig


class _FakeSession:
    voice_enabled = False
    def __init__(self):
        self.pages, self.finished, self.cancelled = [], 0, 0
    def feed_audio(self, b): pass
    def ask(self, t): pass
    def barge_in(self): pass
    def on_story_page(self, n): self.pages.append(n)
    def on_story_finish(self): self.finished += 1
    def on_story_cancel(self): self.cancelled += 1
    def close(self): pass


class TestStoryWs:
    def test_routes(self):
        sess = _FakeSession()
        app = create_app(config=KioskConfig(), session_factory=lambda emit: sess)
        with TestClient(app).websocket_connect("/ws/voice") as ws:
            ws.send_json({"type": "story_page", "n": 4})
            ws.send_json({"type": "story_finish"})
            ws.send_json({"type": "story_cancel"})
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"
        assert sess.pages == [4] and sess.finished == 1 and sess.cancelled == 1


class TestStoryImageApi:
    def _app(self, tmp_path, token=""):
        cfg = KioskConfig(token=token, story_cache_dir=str(tmp_path))
        return create_app(config=cfg, session_factory=lambda emit: _FakeSession())

    def test_serve_and_404(self, tmp_path):
        (tmp_path / "sid1").mkdir()
        (tmp_path / "sid1" / "page_2.png").write_bytes(b"\x89PNG")
        c = TestClient(self._app(tmp_path))
        assert c.get("/api/story/sid1/img/2").content == b"\x89PNG"
        assert c.get("/api/story/sid1/img/9").status_code == 404
        assert c.get("/api/story/..%2F..%2Fetc/img/1").status_code in (404, 422)

    def test_token_query_param(self, tmp_path):
        (tmp_path / "s").mkdir()
        (tmp_path / "s" / "page_1.png").write_bytes(b"P")
        c = TestClient(self._app(tmp_path, token="sekret"))
        assert c.get("/api/story/s/img/1").status_code == 401
        assert c.get("/api/story/s/img/1?token=sekret").content == b"P"
        assert c.get("/api/health").status_code == 200     # health 仍免 token
