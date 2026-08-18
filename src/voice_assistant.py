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
                 greeting: str = "",
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
        self._wake_display = (wake_words or [""])[0]  # 状态行展示用
        self._greeting = greeting                      # 应答语文本（await 状态行展示）
        self._last_line = ""           # 上次已上屏的常驻状态行（不变不重复刷）

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
            self._last_line = ""  # 下次 process_chunk 重发常驻行
            return [VoiceAction("status", "🔊 播报中｜说话可随时打断")]
        if not active and self._mode == "broadcast":
            # 播报结束 → 8s 初始窗口（需求3：语音播报后开始识别录音，8秒）
            self._mode = "listen"
            self._deadline = self._clock() + self._initial_wait
            self._last_line = ""
            return [VoiceAction("status",
                                f"👂 倾听中｜{int(self._initial_wait)}s 内可开口提问")]
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
                        actions.append(VoiceAction(
                            "status", f"✅ 已唤醒｜{self._greeting}" if self._greeting else "✅ 已唤醒"))
                    else:
                        actions.append(VoiceAction("status", "未听到唤醒词，请说「你好小虎」"))
                elif self._mode == "listen":
                    norm = self._correct(_normalize(text))
                    if norm and any(norm == w for w in self._wake_norm):
                        # 倾听态整句即唤醒词 → 重新唤醒应答（用户复测实证：
                        # 8s 窗内说唤醒词被当问题提交走 LLM，与预期不符）
                        self._question = ""
                        self._deadline = None
                        self._mode = "await_broadcast"
                        self._await_since = now
                        actions.append(VoiceAction("greet"))
                        actions.append(VoiceAction("status", "✅ 已唤醒"))
                        return self._with_status_line(actions, now)
                    text = self._strip_wake_prefix(text)  # “你好小虎，xxx” → 前缀剥离
                    if text:
                        self._question += text
                    self._deadline = now + self._extend_wait  # 段结束 → +2s 循环延长
                    actions.append(VoiceAction("msg", self._question))
                    actions.append(VoiceAction("status", "🎙 可继续补充，稍候自动提交…"))

        # 增量喂 ASR + 部分结果实时上屏（需求5：边说边出字）
        # web-048：评估窗口从「in_speech 期间」放宽到「ASR 会话存活期间」——
        # 讯飞完整部分结果多数在说话结束后、VAD 端点闭合前的窗口内才到齐，
        # 旧窗口使待机唤醒必然落入「端点 500ms + finish 往返」慢通道（实测 +1.22s）；
        # 放宽后部分结果一到齐即命中（实测 +0.3~0.5s）。匹配规则不变——
        # 同样文本早晚都会命中，只是提前，不产生新的误唤醒类别。
        if self._asr is not None:
            pending = self._vad.take_pending()
            if pending:
                self._asr.feed(pending)
            partial = self._correct(self._asr.current_text or "")
            if self._mode == "listen" and partial:
                actions.append(VoiceAction("msg", self._question + partial))
            elif self._mode == "standby" and partial:
                # 优化轮3：待机态部分结果提前命中唤醒词——wpgs 部分结果在词尾后
                # ~0.3-0.5s 即可见，不等 VAD 800ms 静音端点（省 ~1s 唤醒延迟）
                norm = self._correct(_normalize(partial))
                if any(w in norm for w in self._wake_norm):
                    self._close_asr()  # 提前结束会话；同段尾部 segment 事件被忽略
                    self._mode = "await_broadcast"
                    self._await_since = now
                    actions.append(VoiceAction("greet"))
                    actions.append(VoiceAction(
                        "status",
                        f"✅ 已唤醒｜{self._greeting}" if self._greeting else "✅ 已唤醒"))
        return self._with_status_line(actions, now)

    # ---------- 常驻状态行（修复轮2：用户对状态无感知） ----------

    def _status_line(self, now: float) -> str:
        if self._mode == "standby":
            return f"🎙 待机中｜说「{self._wake_display}」唤醒"
        if self._mode == "await_broadcast":
            # 应答语全文上屏（用户复测“唤醒后无默认回答”——音频之外文本可见）
            return f"🔊 应答中｜{self._greeting}" if self._greeting else "⏳ 正在准备应答…"
        if self._mode == "broadcast":
            return "🔊 播报中｜说话可随时打断"
        # listen
        if self._vad.in_speech:
            return "👂 正在倾听…（说完自动提交）"
        if self._deadline is not None:
            remain = max(0.0, self._deadline - now)
            return f"👂 倾听中｜{remain:.0f}s 内可开口提问"
        return "👂 倾听中"

    def _with_status_line(self, actions: List[VoiceAction], now: float) -> List[VoiceAction]:
        """每块收尾挂常驻状态行：本块已有瞬时提示（✅/⚡/📨）则让位，下块补常驻行。"""
        if any(a.kind == "status" for a in actions):
            self._last_line = ""  # 瞬时提示上屏后，下块重发常驻行
        else:
            line = self._status_line(now)
            if line != self._last_line:
                self._last_line = line
                actions.append(VoiceAction("status", line))
        return actions

    def _strip_wake_prefix(self, text: str) -> str:
        """剥离唤醒词前缀（"你好小虎，司母戊鼎" → "司母戊鼎"）；非前缀原样返回。"""
        norm = self._correct(_normalize(text))
        for w in self._wake_norm:
            if norm.startswith(w) and len(norm) > len(w):
                cnt, i = 0, 0
                while i < len(text) and cnt < len(w):
                    if not _NORM_RE.match(text[i]):  # 只数非标点字符
                        cnt += 1
                    i += 1
                rest = text[i:].lstrip("，。！？、,.!? ")
                if rest:
                    return rest
        return text

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
