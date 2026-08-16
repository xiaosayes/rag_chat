"""语音全链 glue（web-010）：VoiceSession。

一条 WS 连接 = 一个 VoiceSession = BroadcastSession（问答播报编排，web-007）
+ VoiceAssistant（四态 FSM，冻结内核）+ 唤醒应答播报线程。

职责（翻译层，无业务逻辑）：
- binary PCM → assistant.process_chunk → VoiceAction 翻译为 WS 事件；
- BroadcastSession 事件 → 驱动 assistant.notify_broadcast 生命周期
  （任意来源播报都进播报态；播报结束回倾听态——对齐 app.py 语义）；
- greet → 应答语 PCM 经同一音频下行通道播报，时长驱动 FSM（可被打断）。
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

from .chat import AUDIO_FORMAT, BroadcastSession

logger = logging.getLogger(__name__)


class VoiceSession:
    """pipeline/tts_factory：同 BroadcastSession；assistant：VoiceAssistant 或 None（降级）。"""

    def __init__(self, pipeline, tts_factory, assistant, emit: Callable[[dict], None],
                 greeting_pcm_fn: Optional[Callable[[], Optional[bytes]]], *,
                 sync_audio: bool = False, sleep: Callable[[float], None] = time.sleep,
                 submit_fn: Optional[Callable[[str], None]] = None, **broadcast_kw):
        self._assistant = assistant
        self._emit = emit
        self._greeting_pcm_fn = greeting_pcm_fn
        self._sleep = sleep
        self._broadcast = BroadcastSession(pipeline, tts_factory,
                                           self._on_broadcast_event, **broadcast_kw)
        # FSM submit → 自动提问；默认起线程跑（WS 层注入 asyncio 桥接版）
        self._submit_fn = submit_fn or (
            lambda text: threading.Thread(target=self.ask, args=(text,), daemon=True).start())
        self._greet_cancel = threading.Event()
        self._greet_thread: Optional[threading.Thread] = None
        self._bcast_active = False
        # 音频上行串行执行（保序）；测试 sync_audio=True 直驱
        self._audio_pool = None if sync_audio else ThreadPoolExecutor(max_workers=1)
        self._closed = False

    @property
    def busy(self) -> bool:
        return self._broadcast.busy

    @property
    def voice_enabled(self) -> bool:
        return self._assistant is not None

    @property
    def history(self):
        return self._broadcast.history

    # ---------- 控制面 ----------

    def ask(self, text: str) -> None:
        self._broadcast.ask(text)

    def barge_in(self) -> None:
        """打断：应答播报与问答播报同停（触屏按钮 / FSM 语音打断）。"""
        self._greet_cancel.set()
        self._broadcast.barge_in()

    def close(self) -> None:
        self._closed = True
        self.barge_in()
        if self._assistant is not None:
            try:
                self._assistant.close()
            except Exception:
                pass
        if self._audio_pool is not None:
            self._audio_pool.shutdown(wait=False)

    # ---------- 音频上行 ----------

    def feed_audio(self, pcm: bytes) -> None:
        if self._closed:
            return
        if self._audio_pool is not None:
            self._audio_pool.submit(self._process_audio, pcm)
        else:
            self._process_audio(pcm)

    def _process_audio(self, pcm: bytes) -> None:
        if self._assistant is None:
            self._emit({"type": "error", "code": "voice_unavailable",
                        "message": "语音模式不可用（VAD 初始化失败，详见服务端日志）"})
            return
        try:
            actions = self._assistant.process_chunk(pcm)
        except Exception as e:
            logger.warning("语音处理异常（跳过该帧）: %s", e)
            return
        self._handle_actions(actions)

    # ---------- FSM 动作翻译 ----------

    def _handle_actions(self, actions) -> None:
        for a in actions:
            if a.kind == "status":
                self._emit({"type": "state", "mode": self._assistant.mode,
                            "status_text": a.text})
            elif a.kind == "msg":
                self._emit({"type": "asr_partial", "text": a.text})
            elif a.kind == "greet":
                self._emit({"type": "greet"})
                self._start_greeting()
            elif a.kind == "barge_in":
                self.barge_in()
            elif a.kind == "submit":
                self._submit_fn(a.text)

    # ---------- 播报生命周期联动 ----------

    def _on_broadcast_event(self, ev: dict) -> None:
        t = ev.get("type")
        if t in ("answer_start", "audio_start") and not self._bcast_active:
            self._set_broadcast_active(True)
        elif t in ("audio_end", "playback_cancel", "answer_end") and self._bcast_active:
            self._set_broadcast_active(False)
        self._emit(ev)

    def _set_broadcast_active(self, active: bool) -> None:
        self._bcast_active = active
        if self._assistant is None:
            return
        try:
            actions = self._assistant.notify_broadcast(active)
        except Exception as e:
            logger.warning("notify_broadcast 异常: %s", e)
            return
        self._handle_actions(actions)

    # ---------- 唤醒应答播报 ----------

    def _start_greeting(self) -> None:
        self._greet_cancel.clear()

        def run() -> None:
            self._set_broadcast_active(True)         # await_broadcast → broadcast
            pcm = None
            try:
                pcm = self._greeting_pcm_fn() if self._greeting_pcm_fn else None
            except Exception as e:
                logger.warning("应答语获取失败（降级无音频）: %s", e)
            if pcm and not self._greet_cancel.is_set():
                self._emit({"type": "audio_start", "turn": 0,
                            "format": AUDIO_FORMAT, "greeting": True})
                step = 4800                          # ~0.1s/帧直发
                for i in range(0, len(pcm), step):
                    if self._greet_cancel.is_set():
                        break
                    self._emit({"type": "audio", "pcm": pcm[i:i + step]})
                self._emit({"type": "audio_end", "turn": 0, "greeting": True})
            duration = len(pcm) / 48000.0 if pcm else 1.5
            waited = 0.0
            while waited < duration and not self._greet_cancel.is_set():
                self._sleep(min(0.1, duration - waited))
                waited += 0.1
            if self._greet_cancel.is_set():
                self._emit({"type": "playback_cancel"})
            self._set_broadcast_active(False)        # → listen（8s 提问窗）

        self._greet_thread = threading.Thread(target=run, daemon=True)
        self._greet_thread.start()
