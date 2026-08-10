"""阿里百炼 CosyVoice 语音合成封装（bug-121）。

一期：cosyvoice-v3-flash（系统音色，默认小男孩，TTS_VOICE 配置）
二期：cosyvoice-v3.5-flash + VoiceEnrollmentService 真人音色定制（见 README）
合成流式：dashscope SpeechSynthesizer + ResultCallback（on_data 逐块回调）
"""
import threading
from pathlib import Path
from typing import Callable, List, Optional, Union

from src.audio_bootstrap import ensure_ffmpeg


def _ensure_dashscope_key() -> None:
    """确保 dashscope SDK 能拿到 API Key。

    SpeechSynthesizer 无 api_key 参数，只读 dashscope.api_key 全局或环境变量
    DASHSCOPE_API_KEY；而项目密钥走 pydantic .env（不进 os.environ），
    服务器上因此报 "apikey is required"（bug-121 实测）。此处从 settings 注入。
    """
    import os

    from src.config import settings

    key = settings.dashscope_api_key or ""
    if key:
        os.environ.setdefault("DASHSCOPE_API_KEY", key)
        import dashscope

        dashscope.api_key = key


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


def _unbalanced_parens(s: str) -> bool:
    """判断字符串是否有未闭合的括号（（【「《[ 等）。"""
    depth = 0
    for ch in s:
        if ch in "（【「《[":
            depth += 1
        elif ch in "）】」》]" and depth > 0:
            depth -= 1
    return depth > 0


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

        _ensure_dashscope_key()  # 注入 dashscope.api_key（服务器 .env 不进 os.environ）
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
        括号内标点不视为句边界：切分后合并括号未闭合的片段，
        避免产生孤立闭合片段（如 "）。"）导致 CosyVoice 报 invalid text（bug-121 实测）。
        """
        import re

        text = (text or "").strip()
        if not text:
            return []
        raw_parts = [p.strip() for p in re.split(r"(?<=[。！？；!?;\n])", text) if p.strip()]
        # 合并括号未闭合的片段（如 "交通路线（地铁怎么坐？" + "）。"）
        merged: List[str] = []
        for part in raw_parts:
            if merged and _unbalanced_parens(merged[-1]):
                merged[-1] += part
            else:
                merged.append(part)
        result: List[str] = []
        for s in merged:
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