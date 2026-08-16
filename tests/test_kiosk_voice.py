# web-009/010/011：语音全链 glue 测试（真 FSM + 假 VAD/ASR/TTS，全离线）
import io
import os
import threading
import time
import wave

import pytest

from src.config import settings

from kiosk_server import services


@pytest.fixture(autouse=True)
def _reset_services():
    services._reset_cache()
    yield
    services._reset_cache()


def _make_wav(pcm: bytes, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class TestVoiceAssistantFactory:
    def test_status_transitions(self, monkeypatch):
        assert services.voice_status() == "not_initialized"
        monkeypatch.setattr(services, "_load_voice_assistant",
                            lambda pid: object())
        assert services.make_voice_assistant("p") is not None
        assert services.voice_status() == "ready"

    def test_failure_degrades(self, monkeypatch):
        def _boom(pid):
            raise RuntimeError("onnxruntime 缺失")

        monkeypatch.setattr(services, "_load_voice_assistant", _boom)
        assert services.make_voice_assistant("p") is None
        assert services.voice_status().startswith("unavailable:onnxruntime 缺失")


class TestGreetingPcm:
    def test_synthesize_once_then_cache(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "project_root", tmp_path)
        calls = []

        class _FakeTTS:
            def synthesize_sentence(self, text):
                calls.append(text)
                return _make_wav(b"\x01\x02" * 2400)   # 0.05s PCM

        monkeypatch.setattr(services, "make_tts", lambda: _FakeTTS())
        pcm1 = services.greeting_pcm("p")
        pcm2 = services.greeting_pcm("p")
        assert pcm1 == b"\x01\x02" * 2400 and pcm2 == pcm1
        assert len(calls) == 1                          # 内存缓存命中，仅合成一次
        cached = list((tmp_path / "data" / "processed" / "tts_cache").glob("greeting_*.wav"))
        assert len(cached) == 1                         # 磁盘缓存落盘

    def test_tts_unavailable_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr(settings, "project_root", tmp_path)
        monkeypatch.setattr(services, "make_tts", lambda: None)
        assert services.greeting_pcm("p") is None


# ============ web-010：VoiceSession 语音全链 glue ============
from kiosk_server.chat import BroadcastSession
from kiosk_server.voice import VoiceSession
from src.voice_assistant import VoiceAssistant


class FakeVAD:
    """脚本化 VAD：每次 feed 弹一段 {events, pending, in_speech}。"""

    def __init__(self, script):
        self._script = list(script)
        self.in_speech = False
        self._pending = b""

    def feed(self, pcm):
        if not self._script:
            self.in_speech = False
            return []
        step = self._script.pop(0)
        self.in_speech = step.get("in_speech", False)
        self._pending = step.get("pending", b"")
        return step.get("events", [])

    def take_pending(self):
        pending, self._pending = self._pending, b""
        return pending


class FakeASR:
    instances = []

    def __init__(self, finish_text="", partials=None):
        self._finish = finish_text
        self._partials = list(partials or [])
        self._cur = ""
        self.fed = b""
        self.closed = False
        FakeASR.instances.append(self)

    def feed(self, pcm):
        self.fed += pcm
        if self._partials:
            self._cur = self._partials.pop(0)

    @property
    def current_text(self):
        return self._cur

    def finish(self):
        return self._finish

    def close(self):
        self.closed = True


class ManualClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


class AutoClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 0.05
        return self.t


class FakePipeline:
    def __init__(self, tokens):
        self._tokens = tokens

    def query_stream(self, question, conversation_history=None):
        yield {"type": "meta", "from_kb": True, "query_type": "fact", "chunks": []}
        yield from self._tokens


class FakeHandle:
    def __init__(self, on_audio):
        self._on_audio = on_audio
        self.error = None
        self.done = threading.Event()
        self.cancelled = False

    def feed(self, text):
        self._on_audio(b"\x01\x02" * 480)

    def finish(self):
        self.done.set()

    def cancel(self):
        self.cancelled = True
        self.done.set()


class FakeTTS:
    def __init__(self):
        self.handles = []

    def start_stream(self, on_audio):
        h = FakeHandle(on_audio)
        self.handles.append(h)
        return h


def _vsession(monkeypatch, vad_script, asr_scripts, *, tokens=("答句。",),
              greeting_pcm=b"\x01\x02" * 4800, submit=None, default_submit=False):
    """构造 VoiceSession：真 FSM + 假 VAD/ASR/时钟 + 假播报。返回 (session, events, clock, submits)。"""
    clock = ManualClock()
    FakeASR.instances = []
    asr_iter = iter(asr_scripts)

    def asr_factory():
        cfg = next(asr_iter)
        return FakeASR(finish_text=cfg.get("finish", ""), partials=cfg.get("partials"))

    assistant = VoiceAssistant(
        FakeVAD(vad_script), asr_factory, wake_words=["你好湘小图"],
        correct_fn=lambda t: t, initial_wait_s=8.0, extend_wait_s=2.0,
        greeting="您好，请问有什么可以帮您？", clock=clock)
    emit, events = lambda ev: events.append(ev), []
    submits = []
    fake_tts = FakeTTS()
    # default_submit=True 时不注入 submit_fn → VoiceSession 默认起线程跑真实问答播报
    submit_fn = None if default_submit else (submit or submits.append)
    session = VoiceSession(
        FakePipeline(list(tokens)), lambda: fake_tts, assistant, emit,
        greeting_pcm_fn=lambda: greeting_pcm, sync_audio=True,
        submit_fn=submit_fn,
        clock=AutoClock(), tick_s=0.01)
    session._test_submits = submits
    return session, events, clock


def _types(events):
    return [e["type"] for e in events if e["type"] != "audio"]


def _states(events):
    return [e["status_text"] for e in events if e["type"] == "state"]


class TestVoiceSession:
    def test_wake_greet_listen_chain(self, monkeypatch):
        vad_script = [
            {"events": [("confirmed_start", None)], "in_speech": True, "pending": b"AA"},
            {"events": [("segment", b"AA")], "in_speech": False},
        ]
        session, events, clock = _vsession(
            monkeypatch, vad_script,
            [{"finish": "你好，湘小图！", "partials": ["你好湘小"]}])
        session.feed_audio(b"\x00" * 640)
        session.feed_audio(b"\x00" * 640)
        session._greet_thread.join(timeout=3)
        types = _types(events)
        states = _states(events)
        assert "greet" in types
        assert any("已唤醒" in s for s in states)
        assert any("播报中" in s for s in states)          # notify True 联动
        assert any("倾听中" in s for s in states)          # 应答播完 → 倾听 8s 窗
        # 应答语音频经同一 PCM 下行通道
        assert any(e.get("greeting") for e in events if e["type"] == "audio_start")
        assert session._assistant.mode == "listen"

    def test_listen_question_submit(self, monkeypatch):
        vad_script = [
            {"events": [("confirmed_start", None)], "in_speech": True, "pending": b"AA"},
            {"events": [("segment", b"AA")], "in_speech": False},
            {"events": [("confirmed_start", None)], "in_speech": True, "pending": b"BB"},
            {"events": [("segment", b"BB")], "in_speech": False},
            {},   # 第 5 块：无事件（推进超时判定用）
        ]
        session, events, clock = _vsession(
            monkeypatch, vad_script,
            [{"finish": "你好，湘小图！", "partials": ["你好湘小"]},
             {"finish": "介绍一下司母戊鼎", "partials": ["介绍一下"]}])
        for _ in range(2):
            session.feed_audio(b"\x00" * 640)
        session._greet_thread.join(timeout=3)
        session.feed_audio(b"\x00" * 640)   # 第 3 块：开口
        partials = [e["text"] for e in events if e["type"] == "asr_partial"]
        assert any("介绍一下" in p for p in partials)     # 边说边出字
        session.feed_audio(b"\x00" * 640)   # 第 4 块：段结束，延长计时
        clock.advance(2.1)                  # 超过 2s 延长窗
        session.feed_audio(b"\x00" * 640)   # 第 5 块：触发超时提交
        assert session._test_submits == ["介绍一下司母戊鼎"]
        assert any("已提交" in s for s in _states(events))

    def test_broadcast_events_drive_fsm(self, monkeypatch):
        # submit → 同步跑真实播报编排 → FSM 随 audio_start/audio_end 迁移
        vad_script = [
            {"events": [("confirmed_start", None)], "in_speech": True, "pending": b"AA"},
            {"events": [("segment", b"AA")], "in_speech": False},
            {"events": [("confirmed_start", None)], "in_speech": True, "pending": b"BB"},
            {"events": [("segment", b"BB")], "in_speech": False},
            {},
        ]
        holder = {}
        session, events, clock = _vsession(
            monkeypatch, vad_script,
            [{"finish": "你好，湘小图！", "partials": ["你好湘小"]},
             {"finish": "家博会几点开门", "partials": ["家博会"]}],
            submit=lambda text: holder["s"].ask(text))
        holder["s"] = session
        for _ in range(4):
            session.feed_audio(b"\x00" * 640)
        session._greet_thread.join(timeout=3)
        clock.advance(2.1)
        session.feed_audio(b"\x00" * 640)   # 提交并同步完成问答播报
        types = _types(events)
        assert "answer_start" in types and "answer_end" in types
        assert session._assistant.mode == "listen"        # 播报落回倾听（多轮）
        assert any("倾听中" in s for s in _states(events))

    def test_barge_in_during_broadcast(self, monkeypatch):
        session, events, _ = _vsession(monkeypatch, [], [])
        session._assistant.notify_broadcast(True)          # 直接置入播报态
        called = []
        orig = session._broadcast.barge_in
        session._broadcast.barge_in = lambda: (called.append(1), orig())
        # 播报中检测到 ≥400ms 持续语音（confirmed_start）→ FSM 产出 barge_in
        session._assistant._vad._script = [
            {"events": [("confirmed_start", None)], "in_speech": True, "pending": b"XX"}]
        # 补一个 ASR 会话脚本（barge_in 后该段直接作新问题）
        FakeASR.instances = []
        session._assistant._asr_factory = lambda: FakeASR(finish_text="")
        session.feed_audio(b"\x00" * 640)
        assert called == [1]                                # 播报编排被打断
        assert session._assistant.mode == "listen"          # 该段语音直接作新问题
        assert any("已打断" in s for s in _states(events))

    def test_voice_unavailable(self, monkeypatch):
        emit, events = lambda ev: events.append(ev), []
        session = VoiceSession(FakePipeline([]), None, None, emit,
                               greeting_pcm_fn=None, sync_audio=True,
                               clock=AutoClock(), tick_s=0.01)
        session.feed_audio(b"\x00" * 640)
        assert events[0]["type"] == "error" and events[0]["code"] == "voice_unavailable"


# ============ web-011：/ws/voice 语音全链集成 ============
import json

from fastapi.testclient import TestClient

from kiosk_server.app import create_app
from kiosk_server.config import KioskConfig


def _voice_ws_client(monkeypatch, vad_script, asr_scripts, default_submit=False, **cfg):
    """WS 集成：真 FSM + 假 VAD/ASR + 假播报；返回 (TestClient, session_holder)。"""
    holder = {}

    def factory(emit):
        session, events, clock = _vsession(
            monkeypatch, vad_script, asr_scripts, default_submit=default_submit)
        # VoiceSession 默认 submit_fn = 起线程跑真实问答播报（假件秒回）
        session._emit = emit          # 换绑到 WS 桥接
        session._broadcast._emit = session._on_broadcast_event
        holder["s"] = session
        return session

    for k in list(os.environ):
        if k.startswith("KIOSK_"):
            monkeypatch.delenv(k)
    return TestClient(create_app(KioskConfig(**cfg), session_factory=factory)), holder


def _recv_until(ws, pred, max_msgs=300):
    events, binaries = [], 0
    for _ in range(max_msgs):
        msg = ws.receive()
        if msg.get("bytes") is not None:
            binaries += 1
            continue
        ev = json.loads(msg["text"])
        events.append(ev)
        if pred(ev):
            break
    return events, binaries


class TestVoiceWsIntegration:
    def test_full_voice_chain_over_ws(self, monkeypatch):
        vad_script = [
            {"events": [("confirmed_start", None)], "in_speech": True, "pending": b"AA"},
            {"events": [("segment", b"AA")], "in_speech": False},
            {"events": [("confirmed_start", None)], "in_speech": True, "pending": b"BB"},
            {"events": [("segment", b"BB")], "in_speech": False},
            {},
        ]
        client, holder = _voice_ws_client(
            monkeypatch, vad_script,
            [{"finish": "你好，湘小图！", "partials": ["你好湘小"]},
             {"finish": "家博会几点开门", "partials": ["家博会"]}],
            default_submit=True)
        with client.websocket_connect("/ws/voice") as ws:
            ws.send_text(json.dumps({"type": "hello"}))
            hello = json.loads(ws.receive_text())
            assert hello["ok"] is True and hello["voice"] is True
            for _ in range(2):                        # 唤醒段
                ws.send_bytes(b"\x00" * 640)
            events, _ = _recv_until(ws, lambda e: e["type"] == "state"
                                    and "倾听中" in e.get("status_text", ""))
            assert any(e["type"] == "greet" for e in events)
            ws.send_bytes(b"\x00" * 640)              # 开口
            ws.send_bytes(b"\x00" * 640)              # 段结束
            holder["s"]._assistant._clock.advance(2.1)  # 越过 2s 延长窗
            ws.send_bytes(b"\x00" * 640)              # 触发提交 → 问答播报
            events, binaries = _recv_until(ws, lambda e: e["type"] == "answer_end")
        types = [e["type"] for e in events]
        assert "answer_start" in types and "audio_end" in types
        assert binaries > 0
        assert holder["s"]._assistant.mode == "listen"

    def test_hello_voice_false_when_degraded(self, monkeypatch):
        def factory(emit):
            return VoiceSession(FakePipeline([]), lambda: None, None, emit,
                                greeting_pcm_fn=None, sync_audio=True,
                                clock=AutoClock(), tick_s=0.01)

        for k in list(os.environ):
            if k.startswith("KIOSK_"):
                monkeypatch.delenv(k)
        client = TestClient(create_app(KioskConfig(), session_factory=factory))
        with client.websocket_connect("/ws/voice") as ws:
            ws.send_text(json.dumps({"type": "hello"}))
            hello = json.loads(ws.receive_text())
            assert hello["ok"] is True and hello["voice"] is False

    def test_health_vad_field(self, monkeypatch):
        client, _ = _voice_ws_client(monkeypatch, [], [])
        body = client.get("/api/health").json()
        assert body["vad"] == "not_initialized"        # 未探测过（假装配未走 services）
