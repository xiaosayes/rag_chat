"""语音功能 UI 集成测试（bug-121）：mock ASR/TTS，不依赖真实 API"""
import json
from pathlib import Path

import pytest


class _FakeASR:
    """模拟 IflytekASR：满足 app 层调用（feed/correct/current_text/is_final/finish）。"""

    def __init__(self, *a, **kw):
        self.fed = []
        self._current = ""

    def feed(self, pcm):
        self.fed.append(pcm)
        self._current = "你好"
        return self._current

    def correct(self, text):
        return text

    @property
    def current_text(self):
        return self._current

    def is_final(self):
        return False

    def finish(self):
        return self._current


class TestAsrStreamChunk:
    def test_no_keys_returns_message(self, monkeypatch):
        from app import asr_stream_chunk
        from src.config import Settings

        s = Settings(_env_file=None)
        monkeypatch.setattr("app.settings", s)  # 全部为空 → 未配置提示
        results = list(asr_stream_chunk(None, None, ""))
        state, msg_update, status = results[0]
        assert "未配置讯飞密钥" in status["value"]

    def test_feeds_audio_and_updates_text(self, monkeypatch, tmp_path):
        from app import asr_stream_chunk
        from src.config import Settings

        s = Settings(_env_file=None)
        s.xfyun_app_id = "a"
        s.xfyun_api_key = "k"
        s.xfyun_api_secret = "s"
        s.asr_dict_dir = tmp_path
        monkeypatch.setattr("app.settings", s)
        monkeypatch.setattr("app.IflytekASR", _FakeASR)
        monkeypatch.setattr("app._to_pcm16k", lambda b, r: b)
        chunk = tmp_path / "chunk.wav"
        chunk.write_bytes(b"\x00\x01\x02\x03")

        results = list(asr_stream_chunk(str(chunk), None, ""))
        state, msg_update, status = results[0]
        assert state["session"] is not None
        assert "你好" in msg_update["value"]
        assert "识别中" in status["value"]

    def test_vad_finalize_marks_done(self, monkeypatch, tmp_path):
        from app import asr_stream_chunk
        from src.config import Settings

        s = Settings(_env_file=None)
        s.xfyun_app_id = "a"
        s.xfyun_api_key = "k"
        s.xfyun_api_secret = "s"
        s.asr_dict_dir = tmp_path
        monkeypatch.setattr("app.settings", s)

        class _VadASR(_FakeASR):
            def is_final(self):
                return True

        monkeypatch.setattr("app.IflytekASR", _VadASR)
        monkeypatch.setattr("app._to_pcm16k", lambda b, r: b)
        chunk = tmp_path / "chunk.wav"
        chunk.write_bytes(b"data")

        results = list(asr_stream_chunk(str(chunk), None, ""))
        _, msg_update, status = results[0]
        assert "已识别完成" in status["value"]
        assert "你好" in msg_update["value"]


class TestAsrStreamStop:
    def test_stop_finishes_session(self, monkeypatch, tmp_path):
        from app import asr_stream_stop
        from src.config import Settings

        s = Settings(_env_file=None)
        monkeypatch.setattr("app.settings", s)
        state = {"session": type("S", (), {"finish": lambda self: "最终文本"})(), "finalized": False}
        results = list(asr_stream_stop(state, ""))
        new_state, msg_update, status = results[0]
        assert new_state is None
        assert "最终文本" in msg_update["value"]
        assert "已识别完成" in status["value"]

    def test_stop_without_session_noop(self):
        from app import asr_stream_stop
        results = list(asr_stream_stop(None, ""))
        assert results[0][0] is None