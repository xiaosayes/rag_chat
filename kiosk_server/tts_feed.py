"""TTS 喂入切分与 PCM 静默压缩（web-006）。

移植自 app.py（audit-TTS 第五轮，冻结不可改）：_take_first_unit/_take_feed_unit/
_PauseCompressor 语义逐行保真；移植原因：薄层进程不 import app.py（模块级 gradio patch）。
"""
from __future__ import annotations

SENTENCE_END = "，。！？…；、："      # 宽标点集（首播兜底）
SENTENCE_FINAL = "。！？；!?\n"      # 句末标点（喂入单元只在这里切）
CLAUSE_PAUSE = "，、：,."


def cut_at_last(text: str, chars: str, limit: int) -> int:
    """text[:limit] 内最后一个 chars 字符的下一位置；无则 -1。"""
    head = text[:limit]
    for i in range(len(head) - 1, -1, -1):
        if head[i] in chars:
            return i + 1
    return -1


def take_first_unit(buf: str, hard_cap: int = 80, floor_chars: int = 0):
    """首播喂入单元：句末优先；≥8 字逗号等宽标点兜底；≥hard_cap 硬切。

    web-030 首播硬地板（floor_chars>0 时启用）：无标点且 ≥floor_chars 字 →
    地板处硬切抢首播（唯一例外：切点落在未闭合括号内则放弃——bug-121 教训）。
    """
    from src.tts import _unbalanced_parens

    text = buf.strip()
    if not text:
        return "", buf
    cut = cut_at_last(text, SENTENCE_FINAL, hard_cap)
    if cut > 0:
        return text[:cut], buf[len(buf) - len(text) + cut:]
    if len(text) >= 8:
        cut = cut_at_last(text, SENTENCE_END, hard_cap)
        if cut > 0:
            return text[:cut], buf[len(buf) - len(text) + cut:]
    if len(text) >= hard_cap:
        return text[:hard_cap], buf[len(buf) - len(text) + hard_cap:]
    if floor_chars > 0 and len(text) >= floor_chars:
        head = text[:floor_chars]
        if not _unbalanced_parens(head):
            return head, buf[len(buf) - len(text) + floor_chars:]
    return "", buf


def take_feed_unit(buf: str, min_chars: int, max_chars: int = 200, starve: bool = False):
    """后续喂入单元：攒 ≥min_chars 完整句只在句末切；starve 有完整句即喂；
    无完整句且超 max_chars 硬切（吞切点后孤立标点，bug-121）。"""
    text = buf.lstrip()
    if not text:
        return "", buf
    lead = len(buf) - len(text)
    cut = cut_at_last(text, SENTENCE_FINAL, max_chars)
    if cut > 0 and (cut >= min_chars or starve):
        return text[:cut], buf[lead + cut:]
    if cut <= 0 and len(text) >= max_chars:
        rest = text[max_chars:].lstrip(SENTENCE_END + " ")
        return text[:max_chars], rest
    return "", buf


class PauseCompressor:
    """PCM 流静默压缩：20ms 窗峰值 <thresh 判静默，每段静默保留前 cap_s，超出丢弃。"""

    def __init__(self, rate: int = 24000, cap_s: float = 0.35, thresh: int = 300):
        import numpy as np

        self._np = np
        self.rate = rate
        self.win = int(rate * 0.02)
        self.cap_windows = max(1, round(cap_s / 0.02))
        self.thresh = thresh
        self._buf = bytearray()
        self._silent_run = 0
        self.dropped_s = 0.0

    def feed(self, pcm: bytes) -> bytes:
        self._buf.extend(pcm)
        out = bytearray()
        wbytes = self.win * 2
        while len(self._buf) >= wbytes:
            w = bytes(self._buf[:wbytes])
            del self._buf[:wbytes]
            peak = int(self._np.abs(self._np.frombuffer(w, dtype=self._np.int16)).max())
            if peak < self.thresh:
                self._silent_run += 1
                if self._silent_run > self.cap_windows:
                    self.dropped_s += 0.02
                    continue
            else:
                self._silent_run = 0
            out += w
        return bytes(out)

    def flush(self) -> bytes:
        out = bytes(self._buf)
        self._buf.clear()
        return out
