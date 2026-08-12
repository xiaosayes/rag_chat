"""诊断：实测 CosyVoice 流式合成首音频块延迟（真实 API，约 8 次调用）。

验收目标可行性：首句播报 ≤1s = 首文本提交 → 首音频块（本脚本测）+ 攒批/发布开销。
用法：python scripts/measure_tts_firstchunk.py
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings
from src.tts import _ensure_dashscope_key

TEXTS = [
    "好的，",                    # 3字（极短首段）
    "司母戊鼎是商代晚期的青铜礼器，",  # 14字
    "这件文物出土于河南安阳，距今已有三千多年历史。",  # 21字
    "青铜器的铸造工艺十分复杂，需要经过制模、翻范、浇注、打磨等多道工序。",  # 32字
] * 2


class C:
    def __init__(self):
        self.first_at = None
        self.nbytes = 0
        self.err = None
        self.done = threading.Event()
        self.t0 = 0.0

    def on_data(self, data: bytes):
        if self.first_at is None:
            self.first_at = time.time() - self.t0
        self.nbytes += len(data)

    def on_complete(self):
        self.done.set()

    def on_error(self, message):
        self.err = str(message)
        self.done.set()

    def on_close(self):
        self.done.set()

    on_open = lambda self: None
    on_event = lambda self, m: None


def main():
    _ensure_dashscope_key()
    from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

    for fmt in (AudioFormat.PCM_24000HZ_MONO_16BIT,):
        print(f"== format={fmt} ==")
        for i, text in enumerate(TEXTS):
            c = C()
            synth = SpeechSynthesizer(model=settings.tts_model, voice=settings.tts_voice,
                                      format=fmt, callback=c)
            c.t0 = time.time()
            try:
                synth.streaming_call(text)
                synth.streaming_complete()
            except Exception as e:
                print(f"[{i}] {len(text)}字 调用异常: {e}")
                continue
            ok = c.done.wait(timeout=30)
            total = time.time() - c.t0
            if c.err:
                print(f"[{i}] {len(text)}字 失败: {c.err}")
                continue
            audio_s = c.nbytes / 48000  # 24kHz 16bit mono
            print(f"[{i}] {len(text):3d}字  首块 {c.first_at:.3f}s  总 {total:.2f}s  "
                  f"音频 {audio_s:.2f}s  sdk首包延迟上报={synth.get_first_package_delay()}")


if __name__ == "__main__":
    main()
