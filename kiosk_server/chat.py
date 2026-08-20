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

from .tts_clean import clean_for_broadcast   # web-046：内核清洗+薄层补充（未配对 **/列表前缀）
from src.config import settings

# web-049：触 max_tokens 硬截断的判据与修剪（实测校准：dashscope 320 tokens ≈ 431 字）
_TRUNC_TERMINAL = "。！？!?”」』…"     # 句末终结符（含引号/省略号闭合）
_TRUNC_STRONG = "。！？!?"             # 可裁剪的强句边界


def _looks_truncated(full: str, max_tokens: int) -> bool:
    """答案疑似被 max_tokens 硬截断：长度达上限水位且结尾无句末终结符。

    水位按字符校准（int(max_tokens * 1.2)，320 tokens ≈ 384 字地板）；
    双条件缺一不可——长但有完整结尾的答案不误判，短残段（未达水位）不误判。
    """
    text = (full or "").rstrip()
    if not text:
        return False
    if len(text) < int(max_tokens * 1.2):
        return False
    return text[-1] not in _TRUNC_TERMINAL


def _trim_to_last_sentence(full: str) -> str:
    """裁至最后一个强句边界（保留终结符）；无边界则原样返回。"""
    text = (full or "").rstrip()
    for i in range(len(text) - 1, -1, -1):
        if text[i] in _TRUNC_STRONG:
            return text[:i + 1]
    return text

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
                 accum_chars: int = 60, watchdog_s: float = 15.0, max_restarts: int = 2,
                 first_floor_chars: int = 0):
        self._pipeline = pipeline
        self._tts_factory = tts_factory
        self._emit = emit
        self._clock = clock
        self._tick = tick_s
        self._accum = accum_chars
        self._watchdog_s = watchdog_s
        self._max_restarts = max_restarts
        self._first_floor = first_floor_chars   # web-030 首播硬地板（0=禁用）
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
        # web-029：新问题永远打断旧问题——串行化：barge 旧轮、等其收尾（有界），
        # 保证事件严格不乱序（旧 answer_end 先于新 answer_start）
        if self._busy.is_set():
            self.barge_in()
            deadline = time.monotonic() + 5.0
            while self._busy.is_set() and time.monotonic() < deadline:
                time.sleep(min(self._tick, 0.05))
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
            # web-040：只在「有已喂未合成的积压」时才判定异常。
            # 音频已追上喂入（last_audio_at >= last_feed_at）后的静默 = LLM 流式间隙
            # （联网搜索长流 chunk 间隔可达数十秒），不是 TTS 卡死——旧逻辑在此误重建
            # ×2 后放弃播报（用户实测：播几个字后全哑）。
            if not fed or handle.done.is_set():
                return False
            if last_audio_at >= last_feed_at:      # 无积压：最新喂入的音频已到达
                return False
            return clock() - last_audio_at > self._watchdog_s

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
                    while not dead:                # 句边界批量喂（首播句末/逗号/地板兜底）
                        seg, buf = (take_first_unit(buf, floor_chars=self._first_floor)
                                    if not fed else
                                    take_feed_unit(buf, self._accum,
                                                   starve=clock() - last_feed_at > 2.5))
                        if not seg:
                            break
                        seg_clean = clean_for_broadcast(seg)
                        if not seg_clean:
                            continue
                        last_feed_at = clock()     # web-040：先记喂入时刻——
                        if _feed(seg_clean):       # 随后到达的音频即视为「已追上喂入」
                            pass
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
        # web-049：主循环结束后判定截断并预修剪（打断轮不修剪——残段是用户主动打断的）
        truncated = (bool(full) and not interrupted and not failed
                     and _looks_truncated(full, settings.llm_max_tokens))
        trimmed = _trim_to_last_sentence(full) if truncated else full
        if trimmed == full:                # 整段无强句边界可裁 → 保持原样
            truncated = False
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
                # web-049：截断轮不喂悬尾段——播报止于最后完整句，与上屏文本一致
                tail = clean_for_broadcast(buf.strip()) if (buf.strip() and not truncated) else ""
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

        final_text = trimmed if truncated else full    # web-049：截断轮上屏/入史用修剪版
        if final_text:
            self._history.append({"role": "user", "content": question})
            self._history.append({"role": "assistant", "content": final_text})
            self._history = self._history[-8:]     # 4 轮（pipeline 内部亦截断，双保险）
        emit({"type": "answer_end", "turn": turn, "full_text": final_text,
              "cancelled": interrupted or failed})

    @staticmethod
    def _finish_quiet(handle) -> None:
        try:
            handle.finish()
        except Exception as e:
            logger.warning("TTS finish 异常: %s", e)
