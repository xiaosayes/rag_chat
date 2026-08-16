"""前端免提语音真实浏览器 E2E（非 pytest，web-028）。

断言浏览器侧免提链：页面加载 → 模型就绪 → **自动开麦**（无需点击）→ PCM 常开推流
→ 服务端 VAD/FSM 活跃（待机状态行出现且持续刷新）。

留档说明（实证）：Chrome `--use-file-for-fake-audio-capture` 在本 playwright Chromium
版本对合成 wav 注入无效（浏览器 mic 源 RMS 恒为静音底 ~0.002，16k/44k 均试）——
**内容级语音链（唤醒→应答→提问→回答）由 scripts/smoke_kiosk_voice.py 服务端真链路
实证**（真 VAD+讯飞+LLM+TTS 全过）；浏览器内容级唤醒列入现场验收清单（deploy/README.md §4）。
前置：kiosk_server 已起（:7861）。
用法：python scripts/e2e_frontend_voice.py
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
WAKE_WAV = Path("C:/Windows/Temp/kiosk_voice_fixtures/wake_16k.wav")


def ensure_wake_wav() -> Path:
    """CosyVoice 合成唤醒词 → 16k s16le WAV（Chrome fake audio capture 用）。"""
    if WAKE_WAV.exists():
        return WAKE_WAV
    sys.path.insert(0, str(ROOT))
    from src.config import settings
    from src.tts import CosyVoiceTTS

    WAKE_WAV.parent.mkdir(parents=True, exist_ok=True)
    tts = CosyVoiceTTS(model=settings.tts_model, voice=settings.tts_voice,
                       speech_rate=settings.tts_speech_rate)
    raw = WAKE_WAV.parent / "wake_24k.wav"
    raw.write_bytes(tts.synthesize_sentence("你好湘小图"))
    from static_ffmpeg import run
    ffmpeg = run.get_or_fetch_platform_executables_else_raise()[0]
    subprocess.run([ffmpeg, "-y", "-i", str(raw), "-ar", "16000", "-ac", "1",
                    "-acodec", "pcm_s16le", str(WAKE_WAV)],
                   check=True, capture_output=True)
    print(f"[fixture] 唤醒词 wav: {WAKE_WAV}")
    return WAKE_WAV


def main() -> None:
    from playwright.sync_api import sync_playwright

    proc = subprocess.Popen(
        ["npm", "run", "dev", "--", "--port", "5173", "--strictPort"],
        cwd=str(FRONTEND), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        shell=True)
    time.sleep(8)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=[
                "--use-fake-ui-for-media-stream",       # 免授权弹窗（免提关键）
                "--autoplay-policy=no-user-gesture-required",
                "--enable-unsafe-swiftshader",
            ])
            page = browser.new_page(viewport={"width": 1080, "height": 1920})
            page.on("pageerror", lambda e: print(f"[pageerror] {str(e)[:150]}"))
            page.goto("http://127.0.0.1:5173/", wait_until="load")
            page.wait_for_selector(".preset-item", timeout=60000)
            print("[ok] 首页就绪")
            # 免提自动开麦：无需任何点击，待机状态行应自动出现且持续（FSM 在收帧）
            deadline = time.time() + 45
            standby_seen = 0
            while time.time() < deadline:
                text = page.locator(".home-status").inner_text() \
                    if page.locator(".home-status").count() else ""
                if "待机中" in text and "湘小图" in text:
                    standby_seen += 1
                    if standby_seen >= 3:
                        break
                time.sleep(1)
            assert standby_seen >= 3, "免提自动开麦/FSM 待机行未出现（帧流未通）"
            print("[ok] 免提自动开麦 + PCM 推流 + 服务端 FSM 活跃（待机状态行持续）")
            browser.close()
        print("E2E_FRONTEND_VOICE_OK")
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
