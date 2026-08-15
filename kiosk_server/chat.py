"""问答播报编排核心（web-007）：BroadcastSession。

一轮问答 = query_stream 流式问答 + CosyVoice 单会话流式合成 PCM 直推。
编排语义移植自 app.py respond（audit-TTS/audit-ASR，冻结不可改）：
后台泵线程迭代问答流、主循环 0.1s 节拍、句边界批量喂入、静默压缩、
看门狗重建 ≤2 次、打断即停喂停发跳过收尾。差异：音频产出为原始 PCM 事件
（PCM 直推 WebAudio），不再经 _AdtsStreamer/HLS。

线程模型：ask() 由调用线程阻塞执行；on_audio 由 TTS SDK 接收线程回调；
emit 的线程安全由调用方（WS 层）保证。
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Optional

from src.utils import clean_text_for_tts

from .tts_feed import PauseCompressor, take_feed_unit, take_first_unit

logger = logging.getLogger(__name__)

AUDIO_FORMAT = "pcm_s16le_24k"


class BroadcastSession:
    """单连接问答播报会话（一体机单用户）。

    pipeline：有 query_stream(question, conversation_history) 生成器（meta dict + 增量 str）。
    tts_factory：() -> tts|None；None = 纯文本（播报静默跳过，回答不受影响）。
    """

    def __init__(self, pipeline, tts_factory: Callable, emit: Callable[[dict], None], *,
                 clock: Callable[[], float] = time.monotonic, tick_s: float = 0.1,
                 accum_chars: int = 60, watchdog_s: float = 15.0, max_restarts: int = 2):
        self._pipeline = pipeline
        self._tts_factory = tts_factory
        self._emit = emit
        self._clock = clock
        self._tick = tick_s
        self._accum = accum_chars
        self._watchdog_s = watchdog_s
        self._max_restarts = max_restarts
        self._history = []
        self._cancel = threading.Event()
        self._busy = threading.Event()
        self._turn = 0
        self.replay_pcm = b""      # 最近一轮原始 PCM（预留；重播为端侧缓存，见设计 §3.1）
        self.last_meta: Optional[dict] = None

    @property
    def busy(self) -> bool:
        return self._busy.is_set()

    @property
    def history(self):
        return list(self._history)

    def barge_in(self) -> None:
        """打断（触屏按钮或 M3 语音打断）：主循环/排空每拍检查。"""
        self._cancel.set()

    def close(self) -> None:
        self._cancel.set()

    # ---------- 主流程 ----------

    def ask(self, question: str) -> None:
        if not question or not question.strip():
            return
        self._cancel.clear()
        self._busy.set()
        self._turn += 1
        try:
            self._ask_inner(question.strip(), self._turn)
        finally:
            self._busy.clear()

    def _ask_inner(self, question: str, turn: int) -> None:
        emit = self._emit
        clock = self._clock
        emit({"type": "answer_start", "turn": turn})

        text_q: queue.Queue = queue.Queue()

        def _pump():
            try:
                for item in self._pipeline.query_stream(
                        question=question, conversation_history=list(self._history)):
                    if self._cancel.is_set():      # 打断即停泵（省 LLM 额度）
                        break
                    if isinstance(item, dict) and item.get("type") == "meta":
                        self.last_meta = item
                        continue
                    text_q.put(("text", item))
            except Exception as e:                 # 防御兜底（pipeline 多已内部处理）
                text_q.put(("error", e))
            finally:
                text_q.put(("end", None))

        threading.Thread(target=_pump, daemon=True).start()

        tts = self._tts_factory() if self._tts_factory else None
        compressor = PauseCompressor() if tts is not None else None
        handle = None
        fed = []
        buf = ""
        full = ""
        restarts = 0
        dead = False
        audio_started = False
        replay = bytearray()
        last_feed_at = clock()
        last_audio_at = clock()

        def _on_audio(pcm: bytes) -> None:         # TTS SDK 接收线程
            nonlocal last_audio_at, audio_started
            if self._cancel.is_set():
                return
            last_audio_at = clock()
            out = compressor.feed(pcm)             # 丢弃 >0.35s 静默窗（喂入边界烙入）
            if not out:
                return
            replay.extend(out)
            if not audio_started:
                audio_started = True
                emit({"type": "audio_start", "turn": turn, "format": AUDIO_FORMAT})
            emit({"type": "audio", "pcm": out})

        def _feed(text: str) -> bool:              # False = 会话异常需重建
            nonlocal handle
            try:
                if handle is None:
                    handle = tts.start_stream(_on_audio)
                handle.feed(text)
            except Exception as e:
                logger.warning("TTS 喂文本失败（会话异常）: %s", e)
                return False
            fed.append(text)
            return True

        def _broken() -> bool:
            if handle is None:
                return False
            if handle.error:
                return True
            return (fed and not handle.done.is_set()
                    and clock() - last_audio_at > self._watchdog_s)

        def _restart() -> None:                    # 重建并只重喂最后片段（有界重复）
            nonlocal handle, restarts, dead
            if restarts >= self._max_restarts:
                dead = True
                logger.warning("TTS 会话多次异常，放弃本轮播报（回答不受影响）")
                return
            restarts += 1
            logger.warning("TTS 会话异常（%s），重建会话(%d/%d)",
                           (handle.error if handle else None) or "无音频超时",
                           restarts, self._max_restarts)
            try:
                if handle is not None:
                    handle.cancel()
            except Exception:
                pass
            handle = None
            if fed:
                _feed(fed[-1])

        interrupted = False
        failed = False
        ended = False
        while not ended:
            if self._cancel.is_set():
                interrupted = True
                break
            try:
                kind, payload = text_q.get(timeout=self._tick)
            except queue.Empty:
                kind, payload = "tick", None
            if kind == "text":
                full += payload
                emit({"type": "answer_chunk", "turn": turn, "text": payload})
                if tts is not None and not dead:
                    buf += payload
                    while not dead:                # 句边界批量喂（首播句末/逗号兜底）
                        seg, buf = (take_first_unit(buf) if not fed else
                                    take_feed_unit(buf, self._accum,
                                                   starve=clock() - last_feed_at > 2.5))
                        if not seg:
                            break
                        seg_clean = clean_text_for_tts(seg)
                        if not seg_clean:
                            continue
                        if _feed(seg_clean):
                            last_feed_at = clock()
                        else:
                            _restart()
            elif kind == "error":
                logger.warning("回答流异常: %s", payload)
                emit({"type": "error", "code": "answer_failed",
                      "message": "回答生成失败，请稍后再试"})
                failed = True
                break
            elif kind == "end":
                ended = True
            if tts is not None and not dead and _broken():
                _restart()

        # ---------- 收尾 ----------
        if interrupted or failed:
            if handle is not None:
                try:
                    handle.cancel()
                except Exception:
                    pass
            if interrupted:
                logger.info("播报被打断（barge-in）：停喂停发、取消合成、跳过收尾")
                emit({"type": "playback_cancel"})
        else:
            if tts is not None and not dead:
                tail = clean_text_for_tts(buf.strip()) if buf.strip() else ""
                if tail and not _feed(tail):
                    _restart()
                if handle is not None:
                    # finish 阻塞等合成完成（实测 26s+）→ 后台线程，防冻结发布
                    threading.Thread(target=self._finish_quiet, args=(handle,),
                                     daemon=True).start()
                    end_at = clock() + 120         # 总兜底
                    while not dead and clock() < end_at:
                        if self._cancel.is_set():
                            interrupted = True
                            break
                        if handle.done.is_set():
                            break                  # 会话完成：全部 PCM 已交付
                        if clock() - last_audio_at > self._watchdog_s:
                            _restart()
                            if not dead and handle is not None:
                                threading.Thread(target=self._finish_quiet,
                                                 args=(handle,), daemon=True).start()
                        time.sleep(self._tick)
                    if interrupted:                # 排空期被打断：跳过冲尾
                        try:
                            handle.cancel()
                        except Exception:
                            pass
                        emit({"type": "playback_cancel"})
                    else:
                        tail_pcm = compressor.feed(b"") + compressor.flush()
                        if tail_pcm:
                            replay.extend(tail_pcm)
                            if not audio_started:
                                audio_started = True
                                emit({"type": "audio_start", "turn": turn,
                                      "format": AUDIO_FORMAT})
                            emit({"type": "audio", "pcm": tail_pcm})
                        if handle is not None and handle.error:
                            logger.warning("TTS 会话出错（部分音频可能缺失）: %s",
                                           handle.error)
                        logger.info("播报完成: 音频=%.1fs 重建=%d 丢弃静默=%.2fs",
                                    len(replay) / 48000, restarts,
                                    compressor.dropped_s)
            if not interrupted:                  # 排空期被打断：跳过 audio_end/重播留存
                if audio_started:
                    emit({"type": "audio_end", "turn": turn})
                if replay:
                    self.replay_pcm = bytes(replay)

        if full:
            self._history.append({"role": "user", "content": question})
            self._history.append({"role": "assistant", "content": full})
            self._history = self._history[-8:]     # 4 轮（pipeline 内部亦截断，双保险）
        emit({"type": "answer_end", "turn": turn, "full_text": full,
              "cancelled": interrupted or failed})

    @staticmethod
    def _finish_quiet(handle) -> None:
        try:
            handle.finish()
        except Exception as e:
            logger.warning("TTS finish 异常: %s", e)
