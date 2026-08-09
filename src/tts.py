"""阿里百炼 CosyVoice 语音合成封装（bug-121）。

一期：cosyvoice-v3-flash（系统音色，默认小男孩，TTS_VOICE 配置）
二期：cosyvoice-v3.5-flash + VoiceEnrollmentService 真人音色定制（见 README）
合成流式：dashscope SpeechSynthesizer + ResultCallback（on_data 逐块回调）
"""
import threading
from pathlib import Path
from typing import Callable, List, Optional, Union

from src.audio_bootstrap import ensure_ffmpeg


class _ChunkCollector:
    """收集流式合成音频块（dashscope ResultCallback 适配）。"""

    def __init__(self):
        self.data = bytearray()
        self.error: Optional[str] = None
        self.complete = threading.Event()

    def on_open(self) -> None:
        pass

    def on_data(self, data: bytes) -> None:
        self.data.extend(data)

    def on_complete(self) -> None:
        self.complete.set()

    def on_error(self, message) -> None:
        self.error = str(message)
        self.complete.set()

    def on_close(self) -> None:
        self.complete.set()

    def on_event(self, message: str) -> None:
        pass


class CosyVoiceTTS:
    """CosyVoice TTS 封装：逐句合成 + 句子级流式回调。"""

    def __init__(
        self,
        model: str = "cosyvoice-v3-flash",
        voice: str = "",
        format=None,  # dashscope AudioFormat 枚举；None → WAV 24kHz
        sample_rate: int = 24000,
        chunk_chars: int = 1000,
    ):
        from dashscope.audio.tts_v2 import AudioFormat

        self.model = model
        self.voice = voice
        # 真实 API 冒烟（Task 9）发现：SpeechSynthesizer 的 format 需 AudioFormat 枚举而非字符串
        self.format = format or AudioFormat.WAV_24000HZ_MONO_16BIT
        self.sample_rate = sample_rate
        self.chunk_chars = chunk_chars
        ensure_ffmpeg()  # 防御性引导（app 入口已提前调用）

    @staticmethod
    def split_sentences(text: str, max_chars: int = 1000) -> List[str]:
        """按句子边界（。！？；!?;\n）分段；超长单句硬切。

        不做短句合并：句子级流式播放按句 yield，每句一个音频段。
        """
        import re

        text = (text or "").strip()
        if not text:
            return []
        raw_parts = [p.strip() for p in re.split(r"(?<=[。！？；!?;\n])", text) if p.strip()]
        result: List[str] = []
        for s in raw_parts:
            while len(s) > max_chars:
                result.append(s[:max_chars])
                s = s[max_chars:]
            result.append(s)
        return result

    def synthesize_sentence(self, text: str) -> bytes:
        """合成单句，返回完整 wav 字节。60s 超时抛 TimeoutError；on_error 抛 RuntimeError。

        音色未配置的友好提示由调用方（app 层）在调用前检查 settings.tts_voice。
        """
        from dashscope.audio.tts_v2 import SpeechSynthesizer

        collector = _ChunkCollector()
        synth = SpeechSynthesizer(
            model=self.model,
            voice=self.voice,
            format=self.format,
            callback=collector,
        )
        synth.call(text)
        if not collector.complete.wait(timeout=60):
            raise TimeoutError("TTS 合成超时（60s）")
        if collector.error:
            raise RuntimeError(f"TTS 合成失败: {collector.error}")
        return bytes(collector.data)

    def synthesize_stream(self, text: str, on_sentence: Callable[[str, bytes], None]) -> None:
        """逐句合成，每句完成后立即回调 on_sentence(句文本, wav 字节)。"""
        for sentence in self.split_sentences(text, self.chunk_chars):
            wav = self.synthesize_sentence(sentence)
            on_sentence(sentence, wav)

    @staticmethod
    def write_wav(data: bytes, path: Union[str, Path]) -> None:
        Path(path).write_bytes(data)