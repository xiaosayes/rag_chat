# -*- coding: utf-8 -*-
"""web-067 真实 API 端到端自测（一次性，不入库）：
ScriptClient/ImageClient 真件 + 慢速假 TTS（4s/页模拟播报），量首屏时延与插图成功率。
用法: python -X utf8 scripts/_selftest_story_e2e.py [theme] [--cache-dir data/story_selftest]
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kiosk_server.config import KioskConfig  # noqa: E402
from kiosk_server.story import (  # noqa: E402
    ImageClient, ScriptClient, StoryCache, StorySession,
)


class _SlowTTSHandle:
    def __init__(self, on_audio):
        self._on_audio = on_audio
        self.error = None
        self.done = threading.Event()

    def feed(self, text):
        self._on_audio(b"\x01\x02" * 480)

    def finish(self):
        threading.Timer(4.0, self.done.set).start()   # 模拟 4s/页播报

    def cancel(self):
        self.done.set()


class _SlowTTS:
    def start_stream(self, on_audio):
        return _SlowTTSHandle(on_audio)


def main() -> None:
    theme = sys.argv[1] if len(sys.argv) > 1 else "守株待兔"
    cache_dir = sys.argv[3] if len(sys.argv) > 3 else "data/story_selftest"
    cfg = KioskConfig.from_env()
    print(f"[cfg] image_concurrency={cfg.story_image_concurrency} "
          f"first_image_fast={cfg.story_first_image_fast} script_model={cfg.story_script_model}")

    events: list[tuple[float, dict]] = []
    t0 = time.monotonic()
    lock = threading.Lock()

    def emit(ev: dict) -> None:
        with lock:
            events.append((time.monotonic() - t0, dict(ev)))
        t, e = events[-1]
        extra = ""
        if e["type"] == "story_begin":
            extra = f"title={e.get('title')} total={e.get('total')} cached={e.get('cached')}"
        elif e["type"] == "story_page_img":
            extra = f"n={e.get('n')} ok={bool(e.get('url'))} failed={e.get('failed', False)}"
        if not e["type"].startswith("story_speak"):
            print(f"[{t:6.1f}s] {e['type']} {extra}", flush=True)

    s = StorySession(
        emit,
        ScriptClient(cfg.story_script_model, cfg.story_script_max_tokens,
                     cfg.story_script_timeout_s),
        ImageClient(cfg.story_image_model, cfg.story_image_size,
                    cfg.story_image_timeout_s),
        StoryCache(cache_dir, 500), lambda: _SlowTTS(), cfg)
    th = threading.Thread(target=s.start, args=(theme,), daemon=True)
    th.start()
    th.join(timeout=420)
    if th.is_alive():
        print("!! start() 超时未结束")
        s.cancel()
        th.join(timeout=10)

    begin_t = next((t for t, e in events if e["type"] == "story_begin"), None)
    imgs = [(t, e) for t, e in events if e["type"] == "story_page_img"]
    ok = [e for _, e in imgs if e.get("url")]
    fail = [e for _, e in imgs if e.get("failed")]
    first_img_t = imgs[0][0] if imgs else None
    print("\n===== SUMMARY =====")
    print(f"story_begin: {begin_t:.1f}s | 首张插图事件: {first_img_t:.1f}s | "
          f"插图 ok={len(ok)} failed={len(fail)}")
    pages = next((e.get("pages") for _, e in events if e["type"] == "story_begin"), [])
    for p in pages:
        print(f"  p{p['n']} ({len(p['text'])}字): {p['text']}")
    d = Path(cache_dir)
    for f in sorted(d.rglob("page_*.png")):
        print(f"  file: {f.name} {f.stat().st_size // 1024}KB")


if __name__ == "__main__":
    main()
