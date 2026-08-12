"""诊断：实测 CosyVoice 单句合成延迟分布（真实 API，约 12 次调用）。

用于确定播报缓冲策略参数（并行度/首播缓冲阈值）。
用法：python scripts/measure_tts_latency.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings
from src.tts import CosyVoiceTTS

SAMPLES = [
    "司母戊鼎是商代晚期的青铜礼器，",          # ~15字
    "这件文物出土于河南安阳，距今已有三千多年历史。",  # ~21字
    "它的腹部铸有“司母戊”三字铭文，因此得名，是现存最重的古代青铜器。",  # ~31字
    "青铜器的铸造工艺十分复杂，需要经过制模、翻范、浇注、打磨等多道工序，体现了商代高超的冶金技术水平。",  # ~47字
] * 3


def main():
    tts = CosyVoiceTTS(model=settings.tts_model, voice=settings.tts_voice,
                       chunk_chars=settings.tts_chunk_chars)
    rows = []
    for i, text in enumerate(SAMPLES):
        t0 = time.time()
        try:
            wav = tts.synthesize_sentence(text)
            lat = time.time() - t0
            import io, wave
            with wave.open(io.BytesIO(wav), "rb") as w:
                dur = w.getnframes() / w.getframerate()
            rows.append((len(text), lat, dur))
            print(f"[{i+1:02d}] {len(text):3d}字  合成 {lat:5.2f}s  音频 {dur:5.2f}s  比率 {lat/dur:.2f}")
        except Exception as e:
            print(f"[{i+1:02d}] {len(text):3d}字  失败: {e}")
    if rows:
        lats = sorted(r[1] for r in rows)
        n = len(lats)
        print(f"\n延迟: min={lats[0]:.2f}s p50={lats[n//2]:.2f}s "
              f"p90={lats[int(n*0.9)]:.2f}s max={lats[-1]:.2f}s")
        ratios = [r[1] / r[2] for r in rows if r[2] > 0]
        ratios.sort()
        print(f"合成/音频时长比: min={ratios[0]:.2f} p50={ratios[len(ratios)//2]:.2f} max={ratios[-1]:.2f}")


if __name__ == "__main__":
    main()
