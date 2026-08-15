# web-006：TTS 喂入切分 + 静默压缩（移植自 app.py audit-TTS，语义保真）
import threading
import time

from kiosk_server.tts_feed import PauseCompressor, take_feed_unit, take_first_unit


class TestTakeFirstUnit:
    def test_sentence_end_cut(self):
        # 增量缓冲内最后一个句末点为切点（含），残余留待下次
        seg, rest = take_first_unit("你好。世界")
        assert seg == "你好。" and rest == "世界"

    def test_comma_fallback_after_8_chars(self):
        seg, rest = take_first_unit("这是一个很长的句子，还有后半句")
        assert seg == "这是一个很长的句子，" and rest == "还有后半句"

    def test_short_without_punct_waits(self):
        seg, rest = take_first_unit("短句")
        assert seg == "" and rest == "短句"

    def test_hard_cap(self):
        text = "啊" * 90
        seg, rest = take_first_unit(text)
        assert len(seg) == 80 and rest == text[80:]


class TestTakeFeedUnit:
    def test_batch_full_sentences(self):
        seg, rest = take_feed_unit("一" * 30 + "。" + "二" * 40 + "。", min_chars=60)
        assert seg == "一" * 30 + "。" + "二" * 40 + "。" and rest == ""

    def test_below_min_waits(self):
        seg, rest = take_feed_unit("一" * 30 + "。", min_chars=60)
        assert seg == ""

    def test_starve_feeds_any_full_sentence(self):
        seg, rest = take_feed_unit("一" * 5 + "。剩余", min_chars=60, starve=True)
        assert seg == "一" * 5 + "。" and rest == "剩余"

    def test_hard_cut_swallows_leading_punct(self):
        text = "啊" * 210
        seg, rest = take_feed_unit(text, min_chars=60)
        assert len(seg) == 200 and rest == text[200:]


class TestPauseCompressor:
    def test_silence_capped_keeps_head(self):
        # 1s 全静默（50 窗）→ 仅保留 0.35s（18 窗=cap），其余丢弃
        c = PauseCompressor()
        silence = b"\x00\x00" * 480          # 20ms 窗（24000*0.02=480 采样×2B）
        loud = b"\x10\x20" * 480
        out = c.feed(loud + silence * 50 + loud)
        assert len(out) == 960 * 2 + 960 * 18  # 两端有声 + 18 窗静默
        assert c.dropped_s > 0.5

    def test_flush_tail(self):
        c = PauseCompressor()
        c.feed(b"\x01" * 100)                # 不足一窗
        assert c.flush() == b"\x01" * 100


# ============ web-007：BroadcastSession 播报编排核心 ============
from kiosk_server.chat import BroadcastSession


class FakePipeline:
    """假 RAG 流水线：记录 conversation_history，按脚本产出 meta+增量 token。"""

    def __init__(self, tokens, raise_exc=None, gate: threading.Event = None):
        self._tokens = tokens
        self._raise = raise_exc
        self._gate = gate
        self.histories = []

    def query_stream(self, question, conversation_history=None):
        self.histories.append(list(conversation_history or []))
        yield {"type": "meta", "from_kb": True, "query_type": "fact", "chunks": []}
        if self._raise:
            raise self._raise
        for i, t in enumerate(self._tokens):
            if self._gate is not None and i > 0:   # 第 1 个 token 放行，后续卡住等门
                self._gate.wait(timeout=5)
            yield t


class FakeHandle:
    def __init__(self, on_audio, tts):
        self._on_audio = on_audio
        self._tts = tts
        self.error = None
        self.done = threading.Event()
        self.fed = []
        self.cancelled = False

    def feed(self, text):
        if self._tts.feed_raises:
            self.broken = True
            raise RuntimeError("feed boom")
        self.fed.append(text)
        if not self._tts.mute:
            self._on_audio(b"\x01\x02" * 480)   # 每喂一段产 20ms PCM

    def finish(self):
        if not self._tts.mute and not getattr(self, "broken", False):
            self._on_audio(b"\x03\x04" * 480)
        if not self._tts.hang_finish:
            self.done.set()

    def cancel(self):
        self.cancelled = True
        self.done.set()


class FakeTTS:
    def __init__(self, mute=False, hang_finish=False, feed_raises=False):
        self.mute = mute
        self.hang_finish = hang_finish
        self.feed_raises = feed_raises
        self.handles = []

    def start_stream(self, on_audio):
        h = FakeHandle(on_audio, self)
        self.handles.append(h)
        return h


class AutoClock:
    """自增假时钟：每次调用 +0.05s（看门狗测试确定性）。"""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 0.05
        return self.t


def _collect_events():
    lock = threading.Lock()
    events = []

    def emit(ev):
        with lock:
            events.append(ev)

    return emit, events


def _event_types(events):
    return [e["type"] for e in events if e["type"] != "audio"]


def _audio_bytes(events):
    return b"".join(e["pcm"] for e in events if e["type"] == "audio")


class TestBroadcastSession:
    def test_normal_full_chain(self):
        pipe = FakePipeline(["这是第一句。", "这是第二", "句。"])
        tts = FakeTTS()
        emit, events = _collect_events()
        s = BroadcastSession(pipe, lambda: tts, emit, clock=AutoClock(), tick_s=0.01)
        s.ask("测试问题")
        types = _event_types(events)
        assert types[0] == "answer_start"
        assert types[-1] == "answer_end"
        assert "audio_start" in types and "audio_end" in types
        assert types.index("audio_start") > types.index("answer_start")
        assert types.index("audio_end") < types.index("answer_end")
        end = [e for e in events if e["type"] == "answer_end"][0]
        assert end["full_text"] == "这是第一句。这是第二句。" and end["cancelled"] is False
        assert len(_audio_bytes(events)) > 0
        assert len(s.replay_pcm) > 0
        assert s.history == [
            {"role": "user", "content": "测试问题"},
            {"role": "assistant", "content": "这是第一句。这是第二句。"},
        ]

    def test_feed_units_end_with_sentence_final(self):
        pipe = FakePipeline(["这是第一句。", "这是第二", "句。"])
        tts = FakeTTS()
        emit, _ = _collect_events()
        s = BroadcastSession(pipe, lambda: tts, emit, clock=AutoClock(), tick_s=0.01,
                             accum_chars=5)
        s.ask("q")
        fed = tts.handles[0].fed
        assert fed == ["这是第一句。", "这是第二句。"]

    def test_history_passed_to_pipeline(self):
        pipe = FakePipeline(["好。"])
        emit, _ = _collect_events()
        s = BroadcastSession(pipe, lambda: None, emit, clock=AutoClock(), tick_s=0.01)
        s.ask("第一问")
        s.ask("第二问")
        assert pipe.histories[0] == []
        assert pipe.histories[1] == [
            {"role": "user", "content": "第一问"},
            {"role": "assistant", "content": "好。"},
        ]

    def test_barge_in_interrupts(self):
        gate = threading.Event()   # 关闭：泵在第 1 个 token 后卡住
        pipe = FakePipeline(["一。", "二。", "三。"], gate=gate)
        tts = FakeTTS()
        emit, events = _collect_events()
        s = BroadcastSession(pipe, lambda: tts, emit, clock=AutoClock(), tick_s=0.01)
        th = threading.Thread(target=s.ask, args=("长问题",), daemon=True)
        th.start()
        # 等首个 chunk 出现（播报进行中）再打断
        deadline = time.time() + 5
        while time.time() < deadline and not any(
                e["type"] == "answer_chunk" for e in events):
            time.sleep(0.01)
        s.barge_in()
        gate.set()
        th.join(timeout=5)
        types = _event_types(events)
        assert "playback_cancel" in types
        assert "audio_end" not in types
        end = [e for e in events if e["type"] == "answer_end"][0]
        assert end["cancelled"] is True
        assert tts.handles and tts.handles[0].cancelled is True

    def test_watchdog_restart_then_dead(self):
        # mute+hang：从不产音频、finish 不完 → 看门狗重建 ≤2 次后放弃，回答仍完整交付
        pipe = FakePipeline(["一句。"])
        tts = FakeTTS(mute=True, hang_finish=True)
        emit, events = _collect_events()
        s = BroadcastSession(pipe, lambda: tts, emit, clock=AutoClock(), tick_s=0.01,
                             watchdog_s=1.0)
        s.ask("q")
        assert len(tts.handles) == 3          # 初始 + 2 次重建
        assert "audio_start" not in _event_types(events)
        end = [e for e in events if e["type"] == "answer_end"][0]
        assert end["full_text"] == "一句。" and end["cancelled"] is False

    def test_pipeline_error(self):
        pipe = FakePipeline([], raise_exc=RuntimeError("kb boom"))
        emit, events = _collect_events()
        s = BroadcastSession(pipe, lambda: None, emit, clock=AutoClock(), tick_s=0.01)
        s.ask("q")
        types = _event_types(events)
        assert "error" in types and "playback_cancel" not in types
        end = [e for e in events if e["type"] == "answer_end"][0]
        assert end["cancelled"] is True

    def test_tts_none_text_only(self):
        pipe = FakePipeline(["只有文本。"])
        emit, events = _collect_events()
        s = BroadcastSession(pipe, lambda: None, emit, clock=AutoClock(), tick_s=0.01)
        s.ask("q")
        types = _event_types(events)
        assert "audio_start" not in types and "audio_end" not in types
        end = [e for e in events if e["type"] == "answer_end"][0]
        assert end["full_text"] == "只有文本。" and end["cancelled"] is False

    def test_tts_feed_failure_degrades_to_text(self):
        pipe = FakePipeline([" degrade 场景一句话。"])
        tts = FakeTTS(feed_raises=True)
        emit, events = _collect_events()
        s = BroadcastSession(pipe, lambda: tts, emit, clock=AutoClock(), tick_s=0.01,
                             accum_chars=5)
        s.ask("q")
        end = [e for e in events if e["type"] == "answer_end"][0]
        assert end["cancelled"] is False
        assert "audio_start" not in _event_types(events)
