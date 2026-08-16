# web-009/010/011：语音全链 glue 测试（真 FSM + 假 VAD/ASR/TTS，全离线）
import io
import threading
import time
import wave

import pytest

from src.config import settings

from kiosk_server import services


@pytest.fixture(autouse=True)
def _reset_services():
    services._reset_cache()
    yield
    services._reset_cache()


def _make_wav(pcm: bytes, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class TestVoiceAssistantFactory:
    def test_status_transitions(self, monkeypatch):
        assert services.voice_status() == "not_initialized"
        monkeypatch.setattr(services, "_load_voice_assistant",
                            lambda pid: object())
        assert services.make_voice_assistant("p") is not None
        assert services.voice_status() == "ready"

    def test_failure_degrades(self, monkeypatch):
        def _boom(pid):
            raise RuntimeError("onnxruntime 缺失")

        monkeypatch.setattr(services, "_load_voice_assistant", _boom)
        assert services.make_voice_assistant("p") is None
        assert services.voice_status().startswith("unavailable:onnxruntime 缺失")


class TestGreetingPcm:
    def test_synthesize_once_then_cache(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "project_root", tmp_path)
        calls = []

        class _FakeTTS:
            def synthesize_sentence(self, text):
                calls.append(text)
                return _make_wav(b"\x01\x02" * 2400)   # 0.05s PCM

        monkeypatch.setattr(services, "make_tts", lambda: _FakeTTS())
        pcm1 = services.greeting_pcm("p")
        pcm2 = services.greeting_pcm("p")
        assert pcm1 == b"\x01\x02" * 2400 and pcm2 == pcm1
        assert len(calls) == 1                          # 内存缓存命中，仅合成一次
        cached = list((tmp_path / "data" / "processed" / "tts_cache").glob("greeting_*.wav"))
        assert len(cached) == 1                         # 磁盘缓存落盘

    def test_tts_unavailable_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "project_root", tmp_path)
        monkeypatch.setattr(services, "make_tts", lambda: None)
        assert services.greeting_pcm("p") is None
