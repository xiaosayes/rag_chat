"""前端 M5 真实联调 E2E（非 pytest）：真实浏览器 + 真 kiosk_server + 真前端产物。

链路：启动页 → 首页 → 点预设问题 → 聊天态流式回答（小鹿气泡出字）。
前置：kiosk_server 已起（:7861）、frontend 已 build（用 vite preview 伺服 dist）。
用法：python scripts/e2e_frontend_chat.py [--url http://127.0.0.1:4173]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    proc = None
    url = args.url
    if not url:
        url = "http://127.0.0.1:5173/"
        # dev 模式（.env.development → VITE_API_URL=127.0.0.1:7861）；
        # preview/production 产物指向 ub-server，本机 E2E 不可达
        proc = subprocess.Popen(
            ["npm", "run", "dev", "--", "--port", "5173", "--strictPort"],
            cwd=str(FRONTEND), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            shell=True)
        time.sleep(8)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=[
                "--use-fake-ui-for-media-stream",
                "--autoplay-policy=no-user-gesture-required",
                "--enable-unsafe-swiftshader",   # 无 GPU 环境跑 WebGL
            ])
            page = browser.new_page(viewport={"width": 1080, "height": 1920})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url, wait_until="load")
            # 启动页可能一闪而过（本地资产加载快）——直接等首页锚点
            splash_seen = page.locator(".splash").count() > 0
            print(f"[ok] 页面加载（启动页{'出现过' if splash_seen else '快速闪过'}）")
            page.wait_for_selector(".preset-item", timeout=60000)
            print("[ok] 模型加载完成，进入首页")
            first = page.locator(".preset-item").first.inner_text()
            print(f"[ok] 预设首条: {first}")
            # 点预设 → 聊天态流式回答
            t0 = time.time()
            page.locator(".preset-item").first.click()
            page.wait_for_selector(".chat-me .text", timeout=5000)
            print(f"[ok] 用户气泡上屏: {page.locator('.chat-me .text').first.inner_text()}")
            page.wait_for_selector(".chat-deer .text", timeout=30000)
            print(f"[ok] 小鹿气泡首字 @{time.time() - t0:.1f}s")
            # 等回答收尾（answer_end → 音频/文本定稿）
            deadline = time.time() + 90
            text = ""
            while time.time() < deadline:
                text = page.locator(".chat-deer .text").first.inner_text()
                stable = text
                time.sleep(2)
                if page.locator(".chat-deer .text").first.inner_text() == stable and len(stable) > 5:
                    break
            print(f"[ok] 回答定稿 {len(text)} 字: {text[:50]}...")
            # MusicBar 在 audio_end 后挂上（文本定稿早于音频收尾）
            try:
                page.wait_for_selector(".music-bar", timeout=30000)
                has_musicbar = True
            except Exception:
                has_musicbar = False
            print(f"[ok] MusicBar 重播器: {'有' if has_musicbar else '无'}")
            assert has_musicbar, "音频收尾后仍无 MusicBar（端侧 PCM 缓存缺失）"
            assert len(text) > 5, "回答为空"
            assert not errors, f"页面 JS 错误: {errors[:3]}"
            browser.close()
        print("E2E_FRONTEND_CHAT_OK")
    finally:
        if proc:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
