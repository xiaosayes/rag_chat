# -*- coding: utf-8 -*-
"""一次性：经隧道观察服务器绘本事件流（验证 web-067 部署生效）。
用法: python -X utf8 scripts/_watch_story_ws.py [theme] [watch_s]
观察 watch_s 秒后断开（服务端 D7 取消）。不翻页——只验证准备期时延与插图事件。
"""
import json
import sys
import time

from websocket import create_connection  # websocket-client（smoke_kiosk_ws 同款依赖）


def main() -> None:
    theme = sys.argv[1] if len(sys.argv) > 1 else "龟兔赛跑"
    watch_s = float(sys.argv[2]) if len(sys.argv) > 2 else 75
    ws = create_connection("ws://127.0.0.1:7862/ws/voice", timeout=120)
    t0 = time.time()
    ws.send(json.dumps({"type": "hello"}))
    hello = json.loads(ws.recv())
    assert hello.get("ok"), hello
    ws.send(json.dumps({"type": "ask", "text": f"给我讲一个{theme}的故事"}))
    print(f"[watch] 主题={theme} 观察 {watch_s}s …", flush=True)
    imgs_ok = imgs_fail = 0
    while time.time() - t0 < watch_s:
        try:
            raw = ws.recv()
        except Exception as e:  # noqa: BLE001
            print(f"[{time.time()-t0:6.1f}s] recv 结束: {e}")
            break
        if isinstance(raw, bytes):
            continue
        ev = json.loads(raw)
        ty = ev.get("type", "")
        if not ty.startswith("story") and ty not in ("answer_start", "answer_end"):
            continue
        t = time.time() - t0
        extra = ""
        if ty == "story_begin":
            extra = f"title={ev.get('title')} total={ev.get('total')} cached={ev.get('cached')}"
        elif ty == "story_page_img":
            ok = bool(ev.get("url"))
            imgs_ok += ok
            imgs_fail += ev.get("failed", False)
            extra = f"n={ev.get('n')} {'OK' if ok else 'FAILED'}"
        elif ty == "story_error":
            extra = str(ev.get("message"))
        print(f"[{t:6.1f}s] {ty} {extra}", flush=True)
    ws.close()
    print(f"[done] 插图 OK={imgs_ok} FAILED={imgs_fail}")


if __name__ == "__main__":
    main()
