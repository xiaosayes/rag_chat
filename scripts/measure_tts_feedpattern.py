"""诊断：喂文本模式对 CosyVoice 流式产出速率的影响（真实 API）。

A: 一次性喂全文（~370字）→ streaming_complete
B: 20 字/片段 × 0.5s 间隔喂入（模拟当前 respond 的增量喂法）
观察音频块到达时间线：是否有 >3s 断流、总耗时、产出速率（音频秒/墙钟秒）。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings
from src.tts import _ensure_dashscope_key

TEXT = (
    "司母戊鼎是商代晚期的青铜礼器，1939年出土于河南省安阳市武官村，"
    "因其腹部内壁铸有“司母戊”三字铭文而得名。鼎高133厘米，口长110厘米，"
    "口宽79厘米，重达832.84公斤，是现存中国古代最重的青铜礼器。"
    "鼎身呈长方形，四柱足中空，器表饰以饕餮纹、云雷纹等典型商代纹样，"
    "整体造型雄浑庄重，体现了商代晚期青铜铸造工艺的最高水平。"
    "从铸造工艺看，司母戊鼎采用分铸法铸造：先分别铸出鼎耳、鼎身和鼎足，"
    "再合范浇铸成为一体。如此庞大的铸件需要七八十个坩埚同时熔铜浇注，"
    "数百名工匠协同操作，反映出商代青铜手工业高度发达的组织能力。"
    "从合金配比看，其铜、锡、铅比例与《考工记》记载的“六齐”基本吻合，"
    "说明当时已经掌握了相当成熟的合金技术。"
    "司母戊鼎现藏于中国国家博物馆，是镇馆之宝之一，"
    "被列入首批禁止出国（境）展览文物名录，具有极高的历史、艺术与科学价值。"
)


class Probe:
    def __init__(self):
        self.t0 = time.time()
        self.events = []   # (t, bytes)
        self.done_at = None
        self.err = None

    def on_data(self, data: bytes):
        self.events.append((time.time() - self.t0, len(data)))

    def on_complete(self):
        self.done_at = time.time() - self.t0

    def on_error(self, m):
        self.err = str(m)
        self.done_at = time.time() - self.t0

    def on_close(self):
        if self.done_at is None:
            self.done_at = time.time() - self.t0

    on_open = lambda self: None
    on_event = lambda self, m: None


def run(mode: str):
    _ensure_dashscope_key()
    from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer

    p = Probe()
    synth = SpeechSynthesizer(model=settings.tts_model, voice=settings.tts_voice,
                              format=AudioFormat.PCM_24000HZ_MONO_16BIT, callback=p)
    if mode == "oneshot":
        synth.streaming_call(TEXT)
        synth.streaming_complete()
    else:
        for i in range(0, len(TEXT), 20):
            synth.streaming_call(TEXT[i:i + 20])
            time.sleep(0.5)
        synth.streaming_complete()
    # 等待完成
    deadline = time.time() + 240
    while p.done_at is None and time.time() < deadline:
        time.sleep(0.2)

    total_b = sum(b for _, b in p.events)
    audio_s = total_b / 48000
    # 块间隔 >3s 的断流点
    gaps = []
    prev = None
    for t, _b in p.events:
        if prev is not None and t - prev > 3.0:
            gaps.append(round(t - prev, 1))
        prev = t
    wall = p.done_at or 0
    print(f"[{mode}] 音频 {audio_s:.1f}s 墙钟 {wall:.1f}s 速率 {audio_s / wall:.2f}x "
          f"块数 {len(p.events)} >3s 断流 {gaps} err={p.err}")
    # 每 10s 墙钟的累计音频秒
    marks = {}
    for t, b in p.events:
        marks[int(t // 10) * 10] = marks.get(int(t // 10) * 10, 0) + b / 48000
    print("   分段产出:", {k: round(v, 1) for k, v in sorted(marks.items())})


if __name__ == "__main__":
    run("oneshot")
    time.sleep(2)
    run("fragmented")
