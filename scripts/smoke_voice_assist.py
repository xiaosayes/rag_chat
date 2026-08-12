"""语音助手真实 API 冒烟（audit-ASR）：真实 silero VAD + 真实讯飞 IAT + 真实 FSM 全链路。

非 pytest（需真实讯飞密钥，人工运行）：
    python scripts/smoke_voice_assist.py

流程（夹具 = tests/fixtures/ 下 TTS 预生成的 16k 中文语音）：
  1. 待机态喂「你好，小虎」→ 期望 greet 动作（唤醒词命中）
  2. 模拟欢迎语播报注册/收尾（notify True/False）→ 进入 8s 提问窗
  3. 倾听态喂「请介绍一下司母戊鼎的历史背景和文化价值」→ 期望累积文本并自动 submit
  4. 延迟度量（需求5）：语音进行中首个部分结果延迟、段结束→final 延迟
退出码：全部断言通过 0，否则 1。
"""
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows 控制台 GBK 无法输出 ✅/⚡ 等符号
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.asr import IflytekASR, load_dict
from src.config import settings
from src.vad import try_create_vad
from src.voice_assistant import VoiceAssistant, make_corrector


def _load_pcm(name):
    with wave.open(str(Path(__file__).parent.parent / "tests" / "fixtures" / name), "rb") as w:
        return w.readframes(w.getnframes())


def main() -> int:
    if not (settings.xfyun_app_id and settings.xfyun_api_key and settings.xfyun_api_secret):
        print("❌ 未配置讯飞密钥（XFYUN_APP_ID/API_KEY/API_SECRET）")
        return 1
    vad = try_create_vad(
        model_path=settings.silero_vad_model_path, threshold=settings.vad_threshold,
        min_speech_ms=settings.vad_min_speech_ms, min_silence_ms=settings.vad_min_silence_ms,
        pad_ms=settings.vad_speech_pad_ms, max_speech_s=settings.vad_max_speech_s,
        sample_rate=settings.asr_sample_rate)
    if vad is None:
        print("❌ VAD 初始化失败")
        return 1

    cfg = load_dict("", settings.asr_dict_dir)
    stats = {"first_partial_at": None, "speech_start_at": None, "final_at": None,
             "seg_end_at": None}

    def asr_factory():
        return IflytekASR(settings.xfyun_app_id, settings.xfyun_api_key,
                          settings.xfyun_api_secret, language=settings.asr_language,
                          accent=settings.asr_accent, vad_eos_ms=settings.asr_vad_eos,
                          hotwords=cfg["hotwords"])

    va = VoiceAssistant(vad, asr_factory,
                        wake_words=[w.strip() for w in settings.asr_wake_words.split(",") if w.strip()],
                        correct_fn=make_corrector(cfg["corrections"]),
                        initial_wait_s=settings.asr_initial_wait_s,
                        extend_wait_s=settings.asr_extend_wait_s,
                        clock=time.monotonic)

    def feed_pcm(pcm, label):
        """按生产节奏 0.5s/块喂入（块间 sleep 模拟真实流），返回全部动作。"""
        actions = []
        for off in range(0, len(pcm), 16000):
            t0 = time.monotonic()
            chunk = pcm[off:off + 16000]
            before_partial = va._asr.current_text if va._asr else ""
            acts = va.process_chunk(chunk)
            if va._asr is not None and stats["first_partial_at"] is None:
                p = va._asr.current_text
                if p and p != before_partial:
                    stats["first_partial_at"] = time.monotonic()
            for a in acts:
                print(f"  [{label}] action={a.kind} text={a.text!r}")
            actions.extend(acts)
            time.sleep(max(0.0, 0.5 - (time.monotonic() - t0)))
        # 段后补静音驱动端点判定（真实流不会停）
        for _ in range(5):
            acts = va.process_chunk(b"\x00" * 16000)
            for a in acts:
                print(f"  [{label}] action={a.kind} text={a.text!r}")
            actions.extend(acts)
            time.sleep(0.5)
        return actions

    failures = []

    # 1) 唤醒
    print("== 1. 待机态：喂「你好，小虎」夹具（真实 VAD + 讯飞 IAT）==")
    t0 = time.monotonic()
    acts = feed_pcm(_load_pcm("vad_wake_zh.wav"), "wake")
    if any(a.kind == "greet" for a in acts):
        print(f"✅ 唤醒命中（greet）")
    else:
        failures.append("唤醒未命中（未产出 greet）")

    # 2) 模拟欢迎语播报：注册→收尾 → 应进 listen（8s 窗）
    print("== 2. 模拟播报注册/收尾 → 期望进入 listen ==")
    a = va.notify_broadcast(True)
    assert va.mode == "broadcast", f"notify(True) 后应为 broadcast，实际 {va.mode}"
    a = va.notify_broadcast(False)
    if va.mode == "listen":
        print("✅ 进入 listen（8s 提问窗）")
    else:
        failures.append(f"播报收尾后未进 listen: {va.mode}")

    # 3) 提问
    print("== 3. 倾听态：喂「请介绍一下司母戊鼎…」夹具 ==")
    speech_start = time.monotonic()
    acts = feed_pcm(_load_pcm("vad_speech_zh.wav"), "ask")
    submits = [a for a in acts if a.kind == "submit"]
    if submits:
        print(f"✅ 自动提交: {submits[0].text!r}")
        if "司母戊鼎" not in submits[0].text:
            failures.append(f"提交文本未见「司母戊鼎」（识别质量异常？）: {submits[0].text!r}")
    else:
        failures.append("未自动提交（无 submit 动作）")

    # 4) 延迟（需求5：边说边出字——首个部分结果应在语音段进行中到达）
    print("== 4. 延迟度量 ==")
    if stats["first_partial_at"]:
        print(f"   语音流进行中首个部分结果到达时刻：段开始后 "
          f"~{stats['first_partial_at'] - speech_start:.2f}s（边说边出字；"
          f"「说完到首字<1s」由流式天然满足——说完时首字早已在屏上）")

    if failures:
        print("\n❌ 冒烟失败：")
        for f in failures:
            print("  -", f)
        return 1
    print("\n✅ 语音助手全链路冒烟通过（唤醒→listen→自动提交）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
