"""kiosk_server /ws/voice 语音全链真实冒烟（非 pytest，真实 VAD+讯飞 ASR+LLM+CosyVoice）。

链路实证：PCM 上行 → 服务端 VAD/FSM → 唤醒「你好湘小图」→ 应答播报 → 提问段
→ wpgs 上屏 → 2s 静默自动提交 → 流式回答 + TTS 播报 → 回倾听态。
夹具由 CosyVoice 现场合成（/tmp 缓存复用），ffmpeg 转 16k PCM。

用法：python -m kiosk_server --host 127.0.0.1 --port 7863 （另窗）
      python scripts/smoke_kiosk_voice.py --port 7863
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 项目根入 path

from websocket import create_connection

FIXTURE_DIR = Path(sys.argv and "C:/Windows/Temp" or "/tmp") / "kiosk_voice_fixtures"


def _ffmpeg() -> str:
    from static_ffmpeg import run
    return run.get_or_fetch_platform_executables_else_raise()[0]


def synth_pcm16(text: str) -> bytes:
    """CosyVoice 合成文本 → 16k mono s16le PCM（缓存复用）。"""
    import hashlib

    key = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    pcm_path = FIXTURE_DIR / f"{key}.pcm"
    if pcm_path.exists():
        return pcm_path.read_bytes()
    from src.tts import CosyVoiceTTS
    from src.config import settings

    tts = CosyVoiceTTS(model=settings.tts_model, voice=settings.tts_voice,
                       speech_rate=settings.tts_speech_rate)
    wav = tts.synthesize_sentence(text)
    wav_path = FIXTURE_DIR / f"{key}.wav"
    wav_path.write_bytes(wav)
    subprocess.run([_ffmpeg(), "-y", "-i", str(wav_path), "-f", "s16le",
                    "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                    str(pcm_path)], check=True, capture_output=True)
    print(f"[fixture] 合成夹具: {text!r} → {pcm_path.name}")
    return pcm_path.read_bytes()


def stream_pcm(ws, pcm: bytes, frame_ms: int = 100, realtime: bool = True):
    frame = 16000 * 2 * frame_ms // 1000
    for i in range(0, len(pcm), frame):
        ws.send_binary(pcm[i:i + frame])
        if realtime:
            time.sleep(frame_ms / 1000)


def stream_silence(ws, seconds: float):
    frame = b"\x00\x00" * 1600   # 0.1s
    for _ in range(int(seconds * 10)):
        ws.send_binary(frame)
        time.sleep(0.1)


def recv_until(ws, pred, timeout_s: float, label: str):
    ws.settimeout(timeout_s)
    events, audio_bytes = [], 0
    t0 = time.time()
    while True:
        try:
            raw = ws.recv()
        except Exception as e:
            dump = [{k: v for k, v in ev.items() if k != 'pcm'} for ev in events]
            raise SystemExit(f"[fail] 等待 {label} 超时（{timeout_s}s）: {e}\n"
                             f"已收事件: {json.dumps(dump, ensure_ascii=False)}")
        if isinstance(raw, bytes):
            audio_bytes += len(raw)
            continue
        ev = json.loads(raw)
        events.append(ev)
        if pred(ev):
            return events, audio_bytes, time.time() - t0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7863)
    args = ap.parse_args()

    wake_pcm = synth_pcm16("你好湘小图")
    question_pcm = synth_pcm16("家博会几点开门")

    ws = create_connection(f"ws://{args.host}:{args.port}/ws/voice", timeout=30)
    ws.send(json.dumps({"type": "hello"}))
    hello = json.loads(ws.recv())
    assert hello.get("ok") and hello.get("voice"), f"语音模式未就绪: {hello}"
    print(f"[ok] hello voice={hello['voice']}")

    # FSM 双计时由上行帧驱动（内核契约）→ 独立发送线程全程推流（提问前等倾听态）
    import threading
    listen_event = threading.Event()

    def sender():
        stream_pcm(ws, wake_pcm)                 # 唤醒词
        stream_silence(ws, 1.2)                  # VAD 端点成段
        listen_event.wait(timeout=25)            # 等主线程见到倾听态
        stream_pcm(ws, question_pcm)             # 提问
        stream_silence(ws, 45)                   # 持续静音帧驱动计时 + 结束

    threading.Thread(target=sender, daemon=True).start()

    # ---- 唤醒 ----
    t0 = time.time()
    events, _, _ = recv_until(
        ws, lambda e: e["type"] == "state" and "已唤醒" in e.get("status_text", ""),
        25, "唤醒")
    print(f"[ok] 唤醒命中 @{time.time() - t0:.1f}s；状态: "
          f"{[e.get('status_text') for e in events if e['type'] == 'state'][-1]}")
    events, _, _ = recv_until(
        ws, lambda e: e["type"] == "audio_start" and e.get("greeting"), 10, "应答播报")
    print("[ok] 应答播报开始（greeting PCM 下行）")
    events, _, _ = recv_until(
        ws, lambda e: e["type"] == "state" and "倾听中" in e.get("status_text", ""),
        15, "倾听态")
    print("[ok] 进入倾听态（8s 提问窗）")
    listen_event.set()                           # 放行提问发送

    # ---- 提问 ----
    events, _, _ = recv_until(
        ws, lambda e: e["type"] == "asr_partial" and len(e.get("text", "")) >= 2,
        20, "wpgs 部分结果")
    partial = [e["text"] for e in events if e["type"] == "asr_partial"][-1]
    print(f"[ok] wpgs 上屏: {partial!r}")
    events, _, _ = recv_until(
        ws, lambda e: e["type"] == "state" and "已提交" in e.get("status_text", ""),
        20, "自动提交")
    submitted = [e["status_text"] for e in events
                 if e["type"] == "state" and "已提交" in e.get("status_text", "")][-1]
    print(f"[ok] 双计时自动提交: {submitted}")

    # ---- 回答 + 播报 ----
    t1 = time.time()
    events, audio_bytes, _ = recv_until(
        ws, lambda e: e["type"] == "answer_end", 90, "回答播报")
    chunks = "".join(e["text"] for e in events if e["type"] == "answer_chunk")
    end = [e for e in events if e["type"] == "answer_end"][-1]
    audio_start_at = None
    for e in events:
        if e["type"] == "audio_start" and not e.get("greeting"):
            audio_start_at = True
    print(f"[ok] answer_end cancelled={end['cancelled']} 全文 {len(end['full_text'])} 字"
          f" | 播报音频 {audio_bytes / 48000:.1f}s")
    assert end["full_text"] and not end["cancelled"]
    assert audio_bytes > 48000, "播报音频过少"
    print(f"--- 回答摘要: {end['full_text'][:60]}...")
    print("SMOKE_KIOSK_VOICE_OK")
    ws.close()


if __name__ == "__main__":
    main()
