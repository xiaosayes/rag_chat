"""语音助手状态机（audit-ASR 需求1/3/4）：唤醒 → 双计时提问 → 播报打断 → 多轮循环。

设计要点：
- 纯状态机、零 gradio 依赖：process_chunk(PCM) / notify_broadcast(active) → VoiceAction
  列表，由 app 层翻译成 gradio 输出（离线可测：假 VAD/假 ASR/假时钟）。
- VAD 前置（StreamVAD）：只有确认语音段（≥min_speech）才开讯飞会话——待机/播报态
  不烧 ASR 额度、天然规避 IAT 60s 上限与 asr_max_duration 冲突。
- 双计时（需求3）：LISTEN 进入时 deadline=+8s（初始窗口）；每段语音结束 deadline=+2s
  （循环延长）；到期有文本→submit（自动提问）、无文本→回 STANDBY（流程中断）。
- 打断（需求4）：BROADCAST 态确认语音 → barge_in 动作（app 层取消播报）→ 直接进
  LISTEN，该段语音作为新问题（免唤醒）。
- 唤醒匹配：纠错 → 去标点空白归一 → 唤醒词子串命中（唤醒词可配置，需求1）。
"""
import re
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

# 归一化：去标点/空白（唤醒匹配容错："你好，小虎！" → "你好小虎"）
_NORM_RE = re.compile(r"[\s，。！？、；：,.!?;:\"\"''（）()【】…—~·\-]+")


def _normalize(text: str) -> str:
    return _NORM_RE.sub("", text or "")


def make_corrector(corrections: dict) -> Callable[[str], str]:
    """纠错函数（多字符优先，与 IflytekASR.correct 同语义）。"""
    items = sorted((corrections or {}).items(), key=lambda kv: len(kv[0]), reverse=True)

    def correct(text: str) -> str:
        for wrong, right in items:
            text = text.replace(wrong, right)
        return text

    return correct


@dataclass
class VoiceAction:
    """状态机产出（app 层翻译为 gradio 输出）。"""
    kind: str           # "msg" | "status" | "submit" | "greet" | "barge_in"
    text: str = ""


class VoiceAssistant:
    """四态：standby（等唤醒）/ await_broadcast（等播报注册）/ broadcast（播报中）/ listen（提问中）。"""

    AWAIT_TIMEOUT_S = 12.0   # await_broadcast 兜底：播报迟迟未注册（异常）→ 回待机

    def __init__(self, vad, asr_factory: Callable, *, wake_words: List[str],
                 correct_fn: Callable[[str], str] = None,
                 initial_wait_s: float = 8.0, extend_wait_s: float = 2.0,
                 clock: Callable[[], float] = time.monotonic):
        self._vad = vad
        self._asr_factory = asr_factory
        self._wake_norm = [w for w in (_normalize(x) for x in (wake_words or [])) if w]
        self._correct = correct_fn or (lambda t: t)
        self._initial_wait = initial_wait_s
        self._extend_wait = extend_wait_s
        self._clock = clock
        self._mode = "standby"
        self._asr = None               # 当前语音段的 ASR 会话
        self._question = ""            # listen 态累积的已落定问题文本
        self._deadline: Optional[float] = None
        self._await_since: float = 0.0

    # ---------- 只读状态（测试/观测用） ----------

    @property
    def mode(self) -> str:
        return self._mode

    # ---------- 外部事件 ----------

    def notify_broadcast(self, active: bool) -> List[VoiceAction]:
        """app 层每块告知播报注册表状态（respond/play_greeting 的 token 生命周期）。"""
        if active and self._mode != "broadcast":
            # 任意来源播报（语音提交/手动发送/欢迎语）都进播报态 → 支持打断
            self._close_asr()
            self._deadline = None
            self._mode = "broadcast"
            return [VoiceAction("status", "🔊 播报中…（说话可打断）")]
        if not active and self._mode == "broadcast":
            # 播报结束 → 8s 初始窗口（需求3：语音播报后开始识别录音，8秒）
            self._mode = "listen"
            self._deadline = self._clock() + self._initial_wait
            return [VoiceAction("status", f"🎙 请提问（{int(self._initial_wait)} 秒内开口）")]
        return []

    # ---------- 主循环 ----------

    def process_chunk(self, pcm: bytes) -> List[VoiceAction]:
        now = self._clock()
        actions: List[VoiceAction] = []
        events = self._vad.feed(pcm)

        # await_broadcast 超时兜底（播报注册失败的异常路径）
        if self._mode == "await_broadcast" and now - self._await_since > self.AWAIT_TIMEOUT_S:
            self._mode = "standby"
            return [VoiceAction("status", "⏳ 播报未启动，已回待机（说「你好小虎」唤醒）")]

        # 双计时到期判定（仅当无语音段进行中；说话中 deadline 本就该挂起）
        if (self._mode == "listen" and self._deadline is not None
                and now > self._deadline and not self._vad.in_speech):
            self._deadline = None
            question = self._question.strip()
            self._question = ""
            if question:
                self._mode = "await_broadcast"
                self._await_since = now
                actions.append(VoiceAction("submit", question))
                actions.append(VoiceAction("status", f"📨 已提交提问：{question}"))
            else:
                self._mode = "standby"
                actions.append(VoiceAction("status", "未检测到提问，已回待机（说「你好小虎」唤醒）"))
            return actions

        for kind, _payload in events:
            if kind == "confirmed_start":
                if self._mode in ("standby", "listen"):
                    self._open_asr()
                    if self._mode == "listen":
                        self._deadline = None  # 开口中：挂起计时
                        actions.append(VoiceAction("status", "🎙 识别中…"))
                    # standby 态静默识别：环境语音频繁，不打扰 UI
                elif self._mode == "broadcast":
                    # 打断：停播报，该段语音直接作新问题（免唤醒）
                    self._mode = "listen"
                    self._deadline = None
                    self._question = ""
                    self._open_asr()
                    actions.append(VoiceAction("barge_in"))
                    actions.append(VoiceAction("status", "⚡ 已打断播报，请继续提问"))
                # await_broadcast：忽略（播报即将开始）
            elif kind == "segment" and self._asr is not None:
                text = self._finish_asr()
                if self._mode == "standby":
                    # 先归一（去标点）再纠错：ASR 标点会切断错词导致纠错失配
                    # （"泥好，小胡！" → 归一 "泥好小胡" → 纠错 → "你好小虎" 命中）
                    norm = self._correct(_normalize(text))
                    if any(w in norm for w in self._wake_norm):
                        self._mode = "await_broadcast"
                        self._await_since = now
                        actions.append(VoiceAction("greet"))
                        actions.append(VoiceAction("status", "✅ 已唤醒"))
                    else:
                        actions.append(VoiceAction("status", "未听到唤醒词，请说「你好小虎」"))
                elif self._mode == "listen":
                    if text:
                        self._question += text
                    self._deadline = now + self._extend_wait  # 段结束 → +2s 循环延长
                    actions.append(VoiceAction("msg", self._question))
                    actions.append(VoiceAction("status", "🎙 可继续补充，稍候自动提交…"))

        # 语音进行中：增量喂 ASR + 部分结果实时上屏（需求5：边说边出字）
        if self._asr is not None and self._vad.in_speech:
            pending = self._vad.take_pending()
            if pending:
                self._asr.feed(pending)
            partial = self._correct(self._asr.current_text or "")
            if self._mode == "listen" and partial:
                actions.append(VoiceAction("msg", self._question + partial))
        return actions

    # ---------- ASR 会话管理 ----------

    def _open_asr(self) -> None:
        self._close_asr()
        self._asr = self._asr_factory()
        first = self._vad.take_pending()  # 段首缓冲（含 pad），不漏字
        if first:
            self._asr.feed(first)

    def _finish_asr(self) -> str:
        asr, self._asr = self._asr, None
        try:
            raw = asr.finish()
        finally:
            try:
                asr.close()
            except Exception:
                pass
        return self._correct(raw or "").strip()

    def _close_asr(self) -> None:
        if self._asr is not None:
            try:
                self._asr.close()
            except Exception:
                pass
            self._asr = None

    def close(self) -> None:
        """录音停止/会话销毁时调用（防会话悬挂）。"""
        self._close_asr()
