"""ASR 延迟实测（audit-ASR 优化轮3）：真实讯飞 IAT + 真实 silero VAD，量化两项提速。

度量（夹具 = tests/fixtures/ 下 TTS 预生成语音，按生产 0.3s 节奏喂入）：
  A. 唤醒路径：「你好，小虎」语音结束 → greet 动作（优化轮3 部分结果提前命中，
     不再等 VAD 静音端点；对照旧路径 = 端点+finish ≈ 1.1-1.5s）
  B. 提问路径：语音结束 → segment 端点 → finish 定稿（新默认 min_silence=500ms）
  C. 首字延迟：语音流中首个部分结果（边说边出字）

运行：python scripts/measure_asr_latency.py
"""
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.asr import IflytekASR, load_dict
from src.config import settings
from src.vad import try_create_vad
from src.voice_assistant import VoiceAssistant, make_corrector

CHUNK = 9600  # 0.3s @16k 16bit（生产节奏 stream_every=0.3）


def _load_pcm(name):
    with wave.open(str(Path(__file__).parent.parent / "tests" / "fixtures" / name), "rb") as w:
        return w.readframes(w.getnframes())


def _new_assistant():
    vad = try_create_vad(
        model_path=settings.silero_vad_model_path, threshold=settings.vad_threshold,
        min_speech_ms=settings.vad_min_speech_ms, min_silence_ms=settings.vad_min_silence_ms,
        pad_ms=settings.vad_speech_pad_ms, max_speech_s=settings.vad_max_speech_s,
        sample_rate=settings.asr_sample_rate)
    cfg = load_dict("", settings.asr_dict_dir)

    def factory():
        return IflytekASR(settings.xfyun_app_id, settings.xfyun_api_key,
                          settings.xfyun_api_secret, language=settings.asr_language,
                          accent=settings.asr_accent, vad_eos_ms=settings.asr_vad_eos,
                          hotwords=cfg["hotwords"])

    return VoiceAssistant(vad, factory,
                          wake_words=[w.strip() for w in settings.asr_wake_words.split(",") if w.strip()],
                          correct_fn=make_corrector(cfg["corrections"]),
                          initial_wait_s=settings.asr_initial_wait_s,
                          extend_wait_s=settings.asr_extend_wait_s,
                          greeting=settings.asr_wake_greeting,
                          clock=time.monotonic)


def feed_timed(pcm, va, label, marks):
    """按 0.3s 节奏喂入并记录关键时点；语音内容结束后再补静音驱动端点。"""
    actions_all = []
    speech_end_at = None
    off = 0
    while off < len(pcm):
        t0 = time.monotonic()
        acts = va.process_chunk(pcm[off:off + CHUNK])
        if va._asr is not None and va._asr.current_text and "first_partial" not in marks:
            marks["first_partial"] = time.monotonic()
        actions_all.extend(acts)
        off += CHUNK
        time.sleep(max(0.0, 0.3 - (time.monotonic() - t0)))
    speech_end_at = time.monotonic()  # 语音内容喂完（≈ 真实说完时刻）
    marks["speech_end"] = speech_end_at
    for _ in range(8):  # 补 2.4s 静音驱动 VAD 端点
        t0 = time.monotonic()
        acts = va.process_chunk(b"\x00" * CHUNK)
        actions_all.extend(acts)
        if any(a.kind == "submit" for a in acts) or any(a.kind == "greet" for a in acts):
            break
        time.sleep(max(0.0, 0.3 - (time.monotonic() - t0)))
    return actions_all


def main() -> int:
    if not (settings.xfyun_app_id and settings.xfyun_api_key and settings.xfyun_api_secret):
        print("❌ 未配置讯飞密钥")
        return 1

    print(f"参数: stream_every=0.3s, min_silence={settings.vad_min_silence_ms}ms, "
          f"extend_wait={settings.asr_extend_wait_s}s")

    # A. 唤醒路径
    print("\n== A. 唤醒：「你好，小虎」（1.69s 夹具）==")
    va = _new_assistant()
    marks = {}
    t_start = time.monotonic()
    acts = feed_timed(_load_pcm("vad_wake_zh.wav"), va, "wake", marks)
    greet_at = None
    for a in acts:
        if a.kind == "greet" and greet_at is None:
            greet_at = time.monotonic()  # 动作在 process_chunk 内产生，时间近似
    # greet 时间取不到精确——改为在循环里标
    # （重新精确度量：greet 动作产生时刻）
    va2 = _new_assistant()
    t0 = time.monotonic()
    greet_delay = None
    pcm = _load_pcm("vad_wake_zh.wav")
    off = 0
    last_partial = ""
    for i in range(20):  # 夹具喂完后继续静音块等 greet（partial 可能晚于夹具尾到达）
        c0 = time.monotonic()
        chunk = pcm[off:off + CHUNK] if off < len(pcm) else b"\x00" * CHUNK
        off += CHUNK
        acts = va2.process_chunk(chunk)
        if va2._asr is not None and va2._asr.current_text != last_partial:
            last_partial = va2._asr.current_text or ""
            print(f"   [partial @{(c0 - t0):.2f}s] {last_partial!r}")
        if any(a.kind == "greet" for a in acts):
            greet_delay = c0 - t0 - len(pcm) / 32000  # 词尾（夹具全长秒）→ greet
            break
        time.sleep(max(0.0, 0.3 - (time.monotonic() - c0)))
    if greet_delay is None:
        print("   ❌ 20 块内未唤醒")
        return 1
    print(f"   词尾→greet 动作: {greet_delay:.2f}s（目标 ≤0.6s；含前端起播 ~0.1s 即总响应）")

    # B. 提问路径
    print("\n== B. 提问：「请介绍一下司母戊鼎…」（4.45s 夹具）==")
    va3 = _new_assistant()
    va3.notify_broadcast(True)
    va3.notify_broadcast(False)  # 进 listen（8s 窗）
    pcm = _load_pcm("vad_speech_zh.wav")
    off = 0
    seg_at = submit_at = None
    t0 = time.monotonic()
    feed_done = None
    question = ""
    while True:
        c0 = time.monotonic()
        if off < len(pcm):
            chunk = pcm[off:off + CHUNK]
            off += CHUNK
            if off >= len(pcm):
                feed_done = c0  # 语音喂完时刻
        else:
            chunk = b"\x00" * CHUNK
        acts = va3.process_chunk(chunk)
        for a in acts:
            if a.kind == "msg" and a.text:
                question = a.text
            if a.kind == "submit":
                submit_at = c0
        if submit_at:
            break
        if off >= len(pcm) and c0 - feed_done > 12:
            break
        time.sleep(max(0.0, 0.3 - (time.monotonic() - c0)))
    if feed_done and submit_at:
        print(f"   说完→自动提交: {submit_at - feed_done:.2f}s"
              f"（构成：VAD 端点 {settings.vad_min_silence_ms}ms + finish + 延长计时 "
              f"{settings.asr_extend_wait_s}s，后者为需求3既定参数）")
        print(f"   提交文本: {question!r}")
    else:
        print("   ❌ 未提交")

    # C. 首字
    if marks.get("first_partial"):
        print(f"\n== C. 首字（部分结果）: 语音流开始 ~{marks['first_partial'] - t_start:.2f}s 处出现"
              f"（边说边出字；「说完到首字」由流式天然满足）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
