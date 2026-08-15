"""kiosk_server /ws/voice 真实链路冒烟（非 pytest，真实 LLM+TTS，联网）。

用法：先起服务  python -m kiosk_server --host 127.0.0.1 --port 7862
      再跑      python scripts/smoke_kiosk_ws.py --port 7862 ["问题"]
断言：hello → ask → answer_start/chunk/audio_start/binary PCM/audio_end/answer_end 全序列；
统计首文本/首音频延迟与音频总量。web-008 留档脚本。
"""
from __future__ import annotations

import argparse
import json
import time

from websocket import create_connection  # websocket-client（requirements 已有）


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7862)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("question", nargs="?", default="家博会几点开门？")
    args = ap.parse_args()

    ws = create_connection(f"ws://{args.host}:{args.port}/ws/voice", timeout=120)
    t0 = time.time()
    ws.send(json.dumps({"type": "hello"}))
    hello = json.loads(ws.recv())
    assert hello.get("ok"), f"hello 失败: {hello}"
    print(f"[ok] hello: version={hello.get('version')}")

    ws.send(json.dumps({"type": "ask", "text": args.question}))
    first_text_at = first_audio_at = None
    text = ""
    audio_bytes = 0
    audio_frames = 0
    while True:
        raw = ws.recv()
        if isinstance(raw, bytes):
            audio_frames += 1
            audio_bytes += len(raw)
            if first_audio_at is None:
                first_audio_at = time.time()
                print(f"[ok] 首音频帧 @{first_audio_at - t0:.2f}s（{len(raw)}B）")
            continue
        ev = json.loads(raw)
        t = ev.get("type")
        if t == "answer_start":
            pass
        elif t == "answer_chunk":
            if first_text_at is None:
                first_text_at = time.time()
                print(f"[ok] 首文本 @{first_text_at - t0:.2f}s")
            text += ev["text"]
        elif t == "audio_start":
            print(f"[ok] audio_start format={ev.get('format')}")
        elif t == "audio_end":
            pass
        elif t == "answer_end":
            break
        elif t == "error":
            raise SystemExit(f"[fail] error 事件: {ev}")
    total = time.time() - t0
    print(f"[ok] answer_end: cancelled={ev.get('cancelled')}")
    print(f"[ok] 全文 {len(text)} 字 | 音频 {audio_bytes / 48000:.1f}s（{audio_frames} 帧）"
          f" | 总耗时 {total:.1f}s")
    print(f"--- 回答摘要: {text[:80]}...")
    assert text, "无回答文本"
    assert audio_bytes > 48000, "音频过少（<1s）"
    print("SMOKE_KIOSK_WS_OK")
    ws.close()


if __name__ == "__main__":
    main()
