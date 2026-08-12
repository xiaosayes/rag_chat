# -*- coding: utf-8 -*-
"""断粮烙停顿验证实验：顺流喂文本时，TTS 引擎在合成断粮点是否烙入静默。

A: 整段一次喂          —— 基线
B: 前半喂 → sleep 2.5s → 喂后半   —— 模拟 LLM 中途停顿（断粮 2.5s）
C: 前半喂 → 立即喂后半（不 sleep）—— 控制组：仅拆分无断粮

对比拼接处（≈ 前半音频时长处）的静默段时长与总时长。
"""
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import settings
from src.tts import CosyVoiceTTS  # 内部自带 .env 密钥注入


def _make_tts():
    from dashscope.audio.tts_v2 import AudioFormat

    return CosyVoiceTTS(model=settings.tts_model, voice=settings.tts_voice,
                        format=AudioFormat.PCM_24000HZ_MONO_16BIT, sample_rate=24000)

TEXT_A = "这件青花瓷器制作于清代乾隆年间"
TEXT_B = "是景德镇御窑厂的精品之作釉色青翠欲滴纹饰精美绝伦"


def collect(feeder):
    tts = _make_tts()
    chunks = []
    h = tts.start_stream(on_audio=lambda pcm: chunks.append(pcm))
    t0 = time.time()
    feeder(h)
    # 等首块音频（会话就绪）再 finish；finish 会阻塞至合成完成
    for _ in range(100):
        if chunks or h.error or h.done.is_set():
            break
        time.sleep(0.1)
    h.finish()
    assert h.done.wait(60), "合成超时"
    if h.error:
        print(f"   [error] {h.error}", flush=True)
    pcm = b"".join(chunks)
    print(f"   耗时 {time.time() - t0:.1f}s 音频 {len(pcm) / 48000:.2f}s", flush=True)
    return pcm


def silent_runs(pcm, thresh=300, min_ms=200):
    """返回 ≥min_ms 的静默区间列表 [(起s, 止s)]（16bit mono 24k）。"""
    n = len(pcm) // 2
    samples = struct.unpack(f"<{n}h", pcm)
    runs, start = [], None
    for i, x in enumerate(samples):
        if abs(x) < thresh:
            if start is None:
                start = i
        elif start is not None:
            if (i - start) / 24 >= min_ms:
                runs.append((start / 24000, i / 24000))
            start = None
    if start is not None and (n - start) / 24 >= min_ms:
        runs.append((start / 24000, n / 24000))
    return runs


print("== A: 整段一次喂 ==", flush=True)
pcm_a = collect(lambda h: h.feed(TEXT_A + TEXT_B))

print("== B: 断粮 2.5s ==", flush=True)
def feed_b(h):
    h.feed(TEXT_A)
    time.sleep(2.5)
    h.feed(TEXT_B)
pcm_b = collect(feed_b)

print("== C: 仅拆分（不断粮）==", flush=True)
def feed_c(h):
    h.feed(TEXT_A)
    h.feed(TEXT_B)
pcm_c = collect(feed_c)

print("== 前半单独合成（定位拼接点）==", flush=True)
pcm_a1 = collect(lambda h: h.feed(TEXT_A))
junction = len(pcm_a1) / 48000
print(f"拼接点 ≈ {junction:.2f}s")

for label, pcm in (("A 整段", pcm_a), ("B 断粮", pcm_b), ("C 拆分", pcm_c)):
    dur = len(pcm) / 48000
    runs = silent_runs(pcm)
    near = [r for r in runs if abs(r[0] - junction) < 1.5 or abs(r[1] - junction) < 1.5]
    print(f"{label}: 总时长 {dur:.2f}s 静默段={[(round(a,2), round(b,2)) for a, b in runs]}"
          f" 拼接点附近={[(round(a,2), round(b,2)) for a, b in near]}", flush=True)
