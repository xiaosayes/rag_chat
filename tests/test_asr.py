"""语音功能测试（bug-121）：src/asr.py 与音频环境引导"""
import base64
import json


class TestAudioBootstrap:
    def test_ensure_ffmpeg_returns_bool(self):
        from src.audio_bootstrap import ensure_ffmpeg
        result = ensure_ffmpeg()
        assert isinstance(result, bool)

    def test_ensure_ffmpeg_does_not_raise(self):
        from src.audio_bootstrap import ensure_ffmpeg
        ensure_ffmpeg()  # 不应抛异常