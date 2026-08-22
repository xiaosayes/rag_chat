# -*- coding: utf-8 -*-
"""一次性诊断：复现「小石头和大山爷爷」插图大面积失败（真实 API，本地跑）。
用法: python scripts/_diag_story_images.py [theme]
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kiosk_server.story import (  # noqa: E402
    ImageClient, ScriptClient, build_image_prompt,
)


def main() -> None:
    theme = sys.argv[1] if len(sys.argv) > 1 else "小石头和大山爷爷"
    out_dir = Path("data/story_diag")
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 1. 脚本（现行 prompt，量时延+分镜字数）----
    script_client = ScriptClient("qwen-plus", max_tokens=2000, timeout_s=60)
    t0 = time.monotonic()
    script = script_client.generate(theme)
    dt = time.monotonic() - t0
    scenes = script["scenes"]
    chars = script.get("characters") or ""
    print(f"[script] {dt:.1f}s | title={script['title']} | scenes={len(scenes)}")
    print(f"[characters] {chars}")
    for i, s in enumerate(scenes, 1):
        flag = " <40!" if len(s) < 40 else ""
        print(f"  scene{i} ({len(s)}字{flag}): {s[:50]}")

    # ---- 2. 插图（绕过重试吞噬，直调 _once 抓真实异常；并发 4 对齐生产）----
    img = ImageClient("qwen-image-3.0", "1024*1024", 90)
    results: dict[int, tuple[bool, float, str]] = {}
    lock = threading.Lock()

    def gen(i: int, scene: str) -> None:
        prompt = build_image_prompt(chars, scene)
        t = time.monotonic()
        try:
            img._once(out_dir / f"p{i}.png", prompt)
            ok, err = True, ""
        except Exception as e:  # noqa: BLE001
            ok, err = False, f"{type(e).__name__}: {e}"
        with lock:
            results[i] = (ok, time.monotonic() - t, err)
            print(f"  [img{i}] {'OK' if ok else 'FAIL'} {results[i][1]:.1f}s {err}")

    sem = threading.Semaphore(4)

    def worker(i: int, scene: str) -> None:
        with sem:
            gen(i, scene)

    t0 = time.monotonic()
    ths = [threading.Thread(target=worker, args=(i, s)) for i, s in enumerate(scenes, 1)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    wall = time.monotonic() - t0
    fails = [i for i, r in results.items() if not r[0]]
    print(f"[images] wall={wall:.1f}s | ok={len(scenes) - len(fails)}/{len(scenes)} | fails={fails}")
    print("[prompts sample]")
    for i in (1, 2):
        print(f"  p{i}: {build_image_prompt(chars, scenes[i - 1])[:120]}")


if __name__ == "__main__":
    main()
