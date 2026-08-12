"""诊断：gradio 6.22 流式音频多轮输出的 run id 稳定性（repro for TTS 第2轮无声）。

blocks.py:2346 `run = id(iterator)`：迭代器 GC 后 id 可能被新迭代器复用 →
第 2 轮 playlist URL 与第 1 轮相同，且 segments 追加到已 ended 的旧 MediaStream
（ENDLIST 之后的段 hls.js 不播）→ 前端即使重建 hls 也无声/重播旧音频。

用法：python scripts/repro_hls_rounds.py [--rounds 4]
"""
import argparse
import io
import json
import sys
import threading
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.audio_bootstrap import ensure_ffmpeg

ensure_ffmpeg()  # stream_output 的 wav→adts 转码依赖 ffmpeg

import gradio as gr
import httpx


def _wav_bytes(dur=0.5, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * dur))
    return buf.getvalue()


def gen():
    for _ in range(3):
        time.sleep(0.3)
        yield _wav_bytes()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--port", type=int, default=7899)
    args = parser.parse_args()

    with gr.Blocks() as demo:
        btn = gr.Button("go")
        audio = gr.Audio(streaming=True, autoplay=True)
        btn.click(gen, None, audio)

    demo.queue()
    demo.launch(server_name="127.0.0.1", server_port=args.port, prevent_thread_lock=True)
    base = f"http://127.0.0.1:{args.port}"
    time.sleep(1.5)

    blocks = demo.app.get_blocks()
    session_hash = "diag-session"
    prev_url = None
    for rnd in range(1, args.rounds + 1):
        # 与前端一致的 SSE 协议：join → 轮询 /queue/data
        r = httpx.post(f"{base}/gradio_api/queue/join", json={
            "data": [], "fn_index": 0, "session_hash": session_hash,
            "event_data": None,
        }, timeout=30)
        event_id = r.json()["event_id"]
        urls, done = [], False
        with httpx.stream("GET", f"{base}/gradio_api/queue/data",
                          params={"session_hash": session_hash}, timeout=30) as resp:
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = json.loads(line[5:].strip())
                msg = payload.get("msg")
                if msg == "process_generating":
                    out = payload["output"]["data"][0]
                    if isinstance(out, dict) and out.get("is_stream"):
                        urls.append(out["path"])
                elif msg in ("process_completed", "process_starts"):
                    if msg == "process_completed":
                        done = True
                        break
        # 服务端状态
        streams = blocks.pending_streams.get(session_hash, {})
        runs = {run: {cid: (len(s.segments), s.ended) for cid, s in per.items()}
                for run, per in streams.items()}
        uniq = sorted(set(urls))
        collision = "!!! URL 与上轮相同（id 碰撞）" if prev_url and uniq and uniq[0] == prev_url else ""
        print(f"轮{rnd}: yields={len(urls)} urls={len(uniq)} {uniq[0] if uniq else None} {collision}")
        print(f"     pending_streams(run: segments,ended): {runs}")
        # 拉取本轮 playlist 检查 ENDLIST 位置
        if uniq:
            pl = httpx.get(f"{base}/gradio_api/stream/{uniq[0]}", timeout=10).text
            endlist_at = pl.find("#EXT-X-ENDLIST")
            seg_after = pl.find(".aac", endlist_at) if endlist_at >= 0 else -1
            tail = "ENDLIST 后仍有 segment!" if seg_after > 0 else ""
            print(f"     playlist: {len(pl.splitlines())} 行 {tail}")
            print("     " + "\\n     ".join(pl.splitlines()[:12]))
        if uniq:
            prev_url = uniq[0]
        time.sleep(0.5)

    demo.close()


if __name__ == "__main__":
    main()
