"""语音助手（audit-ASR）测试：VAD / 唤醒状态机 / 双计时 / 打断 / 词典新格式。

全离线：silero 模型为本地 ONNX（pip 包内置）；讯飞/dashscope 一律 fake。
"""
import json
import math
import struct
import time

import gradio as gr
import pytest


# ============ T1: 配置项 + load_dict 新格式 ============

class TestVoiceAssistConfig:
    def test_assist_defaults(self):
        from src.config import Settings
        s = Settings()
        assert s.voice_assist_enabled is False  # 默认关：手动模式行为零变化
        assert s.asr_wake_words == "你好小虎"
        assert s.asr_wake_greeting == "您好，我是小虎，请问有什么可以帮您？"
        assert s.asr_initial_wait_s == 8.0
        assert s.asr_extend_wait_s == 2.0

    def test_vad_defaults(self):
        from src.config import Settings
        s = Settings()
        assert s.vad_threshold == 0.5
        assert s.vad_min_speech_ms == 400
        assert s.vad_min_silence_ms == 500  # 优化轮3 提速（原 800，双计时兜底完整性）
        assert s.vad_speech_pad_ms == 200
        assert s.vad_max_speech_s == 15
        assert s.silero_vad_model_path == ""


class TestLoadDictListFormat:
    """audit-ASR 需求6：纠词典支持 [{"from":..,"to":..}] 顶层列表格式。"""

    def test_top_level_list_as_corrections(self, tmp_path):
        from src.asr import load_dict
        (tmp_path / "asr_dict.json").write_text(json.dumps([
            {"from": "巨声智能", "to": "具身智能"},
            {"from": "大圣", "to": "大晟"},
        ], ensure_ascii=False), encoding="utf-8")
        d = load_dict("", tmp_path)
        assert d["corrections"] == {"巨声智能": "具身智能", "大圣": "大晟"}
        assert d["hotwords"] == []

    def test_project_list_overrides_global_dict(self, tmp_path):
        from src.asr import load_dict
        (tmp_path / "asr_dict.json").write_text(json.dumps(
            {"hotwords": ["司母戊鼎"], "corrections": {"四亩无顶": "司母戊鼎"}},
            ensure_ascii=False), encoding="utf-8")
        (tmp_path / "p1_asr_dict.json").write_text(json.dumps([
            {"from": "广盛", "to": "广晟"}], ensure_ascii=False), encoding="utf-8")
        d = load_dict("p1", tmp_path)
        assert d["corrections"] == {"四亩无顶": "司母戊鼎", "广盛": "广晟"}
        assert d["hotwords"] == ["司母戊鼎"]

    def test_dict_with_wake_keys(self, tmp_path):
        from src.asr import load_dict
        (tmp_path / "asr_dict.json").write_text(json.dumps({
            "hotwords": [], "corrections": {},
            "wake_words": ["你好小虎", "小虎你好"], "wake_greeting": "我在，请讲",
        }, ensure_ascii=False), encoding="utf-8")
        d = load_dict("", tmp_path)
        assert d["wake_words"] == ["你好小虎", "小虎你好"]
        assert d["wake_greeting"] == "我在，请讲"

    def test_wake_keys_absent_when_not_configured(self, tmp_path):
        """向后兼容：无 wake 配置时返回 dict 不含这两个键（旧调用方契约不变）。"""
        from src.asr import load_dict
        (tmp_path / "asr_dict.json").write_text(json.dumps(
            {"hotwords": ["a"], "corrections": {}}), encoding="utf-8")
        d = load_dict("", tmp_path)
        assert "wake_words" not in d
        assert "wake_greeting" not in d

    def test_list_format_skips_bad_entries(self, tmp_path):
        from src.asr import load_dict
        (tmp_path / "asr_dict.json").write_text(json.dumps([
            {"from": "大圣", "to": "大晟"},
            {"from": "只有from"},
            {"to": "只有to"},
            "不是对象",
            {"from": "", "to": "空from"},
        ], ensure_ascii=False), encoding="utf-8")
        d = load_dict("", tmp_path)
        assert d["corrections"] == {"大圣": "大晟"}


# ============ T2: src/vad.py（silero VAD 流式状态机） ============

def _win(n: int) -> bytes:
    """n 个 30ms 窗的 16k 16bit PCM 字节（内容无关，FakeModel 只看窗口数）。"""
    return b"\x00\x01" * (512 * n)


class _FakeVadModel:
    """脚本化概率序列的假 VAD 模型（每窗吐一个概率，耗尽后重复最后值）。"""

    def __init__(self, probs):
        self.probs = list(probs)
        self.idx = 0
        self.resets = 0

    def prob_pcm(self, win: bytes) -> float:
        p = self.probs[min(self.idx, len(self.probs) - 1)]
        self.idx += 1
        return p

    def reset_states(self):
        self.resets += 1


def _make_vad(probs, **kw):
    from src.vad import StreamVAD
    args = dict(threshold=0.5, min_speech_ms=400, min_silence_ms=800,
                pad_ms=200, max_speech_s=15)
    args.update(kw)
    return StreamVAD(_FakeVadModel(probs), **args)


class TestStreamVADParams:
    def test_idle_silence_no_events(self):
        vad = _make_vad([0.01] * 100)
        assert vad.feed(_win(50)) == []
        assert vad.in_speech is False

    def test_short_utterance_discarded(self):
        """192ms 短音（"嗯"）< min_speech 400ms：不发 confirmed_start，不成段。"""
        vad = _make_vad([0.9] * 6 + [0.01] * 30)
        events = vad.feed(_win(36))
        assert ("confirmed_start", None) not in events
        assert not [e for e in events if e[0] == "segment"]

    def test_confirmed_start_at_min_speech(self):
        """连续语音 ≥400ms 发 confirmed_start：400ms=6400 采样，13 窗(6656)达标。"""
        vad = _make_vad([0.9] * 20 + [0.01] * 30)
        fired_at = None
        for i in range(50):
            evs = vad.feed(_win(1))
            if ("confirmed_start", None) in evs:
                fired_at = i + 1
        assert fired_at == 13

    def test_segment_after_min_silence_with_pads(self):
        """段结束于 800ms 连续静音；段字节 = 前 pad 200ms + 语音 640ms + 后 pad 200ms。"""
        vad = _make_vad([0.01] * 10 + [0.9] * 20 + [0.01] * 30)
        events = vad.feed(_win(60))
        segs = [p for k, p in events if k == "segment"]
        assert len(segs) == 1
        # 200ms=3200 采样; 20 窗=10240 采样; 合计 (3200+10240+3200)*2 字节
        assert len(segs[0]) == (3200 + 10240 + 3200) * 2

    def test_segment_survives_short_inner_silence(self):
        """段内 320ms 短暂停顿（<800ms）不切段。"""
        vad = _make_vad([0.9] * 20 + [0.01] * 10 + [0.9] * 20 + [0.01] * 30)
        events = vad.feed(_win(80))
        segs = [e for e in events if e[0] == "segment"]
        assert len(segs) == 1
        assert events.count(("confirmed_start", None)) == 1

    def test_confirmed_start_once_per_segment(self):
        vad = _make_vad([0.9] * 60 + [0.01] * 30)
        events = vad.feed(_win(90))
        assert events.count(("confirmed_start", None)) == 1

    def test_max_speech_force_cut(self):
        """连续语音 ≥15s 无静音也强制切段；之后新语音开启新段。"""
        vad = _make_vad([0.9] * 600, max_speech_s=2)  # 2s 便于测试
        events = vad.feed(_win(600))
        segs = [e for e in events if e[0] == "segment"]
        # 2s=32000 采样=62.5 窗 → 每 ~63 窗一段：600 窗约 9 段
        assert len(segs) >= 8
        assert events.count(("confirmed_start", None)) >= 8

    def test_model_reset_between_segments(self):
        vad = _make_vad([0.9] * 20 + [0.01] * 30 + [0.9] * 20 + [0.01] * 30)
        vad.feed(_win(100))
        assert vad.model.resets >= 2

    def test_take_pending_incremental(self):
        """confirmed 后 take_pending 增量返回段字节（含首 pad），供 ASR 流式喂入。"""
        vad = _make_vad([0.9] * 20 + [0.01] * 30)
        total = b""
        confirmed = False
        for i in range(50):
            evs = vad.feed(_win(1))
            if ("confirmed_start", None) in evs:
                confirmed = True
            if confirmed:
                total += vad.take_pending()
        assert confirmed
        # 20 窗语音 + 确认后持续读到段结束（含静音尾，消费端无所谓）
        assert len(total) >= 20 * 1024


class TestSileroVadOnnxReal:
    """真实 ONNX 模型冒烟（本地模型文件，离线）。"""

    @pytest.fixture(scope="class")
    def speech_pcm(self):
        import wave
        from pathlib import Path
        p = Path(__file__).parent / "fixtures" / "vad_speech_zh.wav"
        with wave.open(str(p), "rb") as w:
            return w.readframes(w.getnframes())

    def test_find_model_auto(self):
        from src.vad import find_silero_model
        assert find_silero_model("").exists()

    def test_find_model_bad_path_raises(self):
        from src.vad import find_silero_model
        with pytest.raises(FileNotFoundError):
            find_silero_model("nope/missing.onnx")

    def test_silence_low_prob(self):
        from src.vad import SileroVadOnnx
        m = SileroVadOnnx()
        probs = [m.prob_pcm(_win(1)) for _ in range(30)]
        assert max(probs) < 0.1

    def test_noise_no_speech(self):
        import numpy as np
        from src.vad import SileroVadOnnx, StreamVAD
        rng = np.random.default_rng(7)
        noise = (rng.standard_normal(16000 * 3) * 0.01 * 32768).astype(np.int16).tobytes()
        vad = StreamVAD(SileroVadOnnx(), threshold=0.5, min_speech_ms=400,
                        min_silence_ms=800, pad_ms=200, max_speech_s=15)
        events = vad.feed(noise)
        assert not [e for e in events if e[0] in ("confirmed_start", "segment")]

    def test_speech_fixture_segmented(self, speech_pcm):
        from src.vad import SileroVadOnnx, StreamVAD
        vad = StreamVAD(SileroVadOnnx(), threshold=0.5, min_speech_ms=400,
                        min_silence_ms=800, pad_ms=200, max_speech_s=15)
        events = []
        for off in range(0, len(speech_pcm), 16000):  # 0.5s/块，同生产节奏
            events.extend(vad.feed(speech_pcm[off:off + 16000]))
        # 夹具尾部静音 ~768ms < min_silence 800ms（生产流不会停，段结束靠后续静音触发）
        # → 补 2s 静音模拟真实持续流
        events.extend(vad.feed(b"\x00" * 32000 * 2))
        assert ("confirmed_start", None) in events
        segs = [p for k, p in events if k == "segment"]
        assert segs, "4.45s 中文语音应至少产出一个段"
        assert sum(len(s) for s in segs) / 32000 >= 2.0  # 覆盖语音主体

    def test_try_create_vad_graceful_on_missing_model(self):
        from src.vad import try_create_vad
        assert try_create_vad(model_path="nope/missing.onnx") is None


# ============ T3: src/voice_assistant.py（语音助手状态机） ============

class _ScriptedVAD:
    """按脚本逐块吐事件的假 VAD（接口同 StreamVAD）。"""

    def __init__(self, script):
        self.script = [list(s) for s in script]
        self.pending = b""
        self.in_speech = False

    def feed(self, pcm):
        evs = self.script.pop(0) if self.script else []
        for k, _ in evs:
            if k == "confirmed_start":
                self.in_speech = True
                self.pending = pcm  # 段首缓冲（含 pad）供增量喂入
            elif k == "segment":
                self.in_speech = False
                self.pending = b""
        return evs

    def take_pending(self):
        b, self.pending = self.pending, b""
        return b


class _FakeASRSession:
    def __init__(self, final):
        self._final = final
        self.fed = b""
        self.closed = False

    def feed(self, b):
        self.fed += b

    @property
    def current_text(self):
        return "部分识别"

    def is_final(self):
        return False

    def finish(self):
        return self._final

    def close(self):
        self.closed = True


class _FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def _make_assistant(script, finals, clock=None, **kw):
    """构造 VoiceAssistant：scripted VAD + 脚本化 ASR 会话工厂 + 假时钟。"""
    from src.voice_assistant import VoiceAssistant
    sessions = [_FakeASRSession(t) for t in finals]
    it = iter(sessions)
    args = dict(wake_words=["你好小虎"], correct_fn=lambda t: t,
                initial_wait_s=8.0, extend_wait_s=2.0, clock=clock or _FakeClock())
    args.update(kw)
    va = VoiceAssistant(_ScriptedVAD(script), lambda: next(it), **args)
    return va, sessions


def _kinds(actions):
    return [a.kind for a in actions]


_PCM = b"\x00\x01" * 16000  # 0.5s 块（内容无关）


class TestWakeWord:
    def test_wake_word_triggers_greet(self):
        va, sessions = _make_assistant(
            [[("confirmed_start", None)], [("segment", b"x")]], ["你好，小虎"])
        a1 = va.process_chunk(_PCM)
        # 待机态确认开始：不产出 greet/submit（常驻状态行允许存在）
        assert va.mode == "standby"
        assert "greet" not in _kinds(a1) and "submit" not in _kinds(a1)
        a2 = va.process_chunk(_PCM)
        assert "greet" in _kinds(a2)
        assert va.mode == "await_broadcast"
        assert sessions[0].fed  # ASR 会话收到了段音频

    def test_non_wake_speech_ignored(self):
        va, _ = _make_assistant(
            [[("confirmed_start", None)], [("segment", b"x")]], ["今天天气怎么样"])
        va.process_chunk(_PCM)
        actions = va.process_chunk(_PCM)
        assert "greet" not in _kinds(actions)
        assert "submit" not in _kinds(actions)
        assert va.mode == "standby"
        assert any(a.kind == "status" and "唤醒" in a.text for a in actions)

    def test_wake_match_normalizes_and_corrects(self):
        """唤醒匹配：去标点空白 + 先纠错（"泥好小胡"→纠词典归一→命中）。"""
        from src.voice_assistant import make_corrector
        va, _ = _make_assistant(
            [[("confirmed_start", None)], [("segment", b"x")]], ["泥好，小胡！"],
            correct_fn=make_corrector({"泥好小胡": "你好小虎"}))
        va.process_chunk(_PCM)
        actions = va.process_chunk(_PCM)
        assert "greet" in _kinds(actions)

    def test_wake_word_substring_in_sentence(self):
        va, _ = _make_assistant(
            [[("confirmed_start", None)], [("segment", b"x")]], ["那个你好小虎在吗"])
        va.process_chunk(_PCM)
        assert "greet" in _kinds(va.process_chunk(_PCM))


class TestDualTimer:
    def _enter_listen(self, va, clock):
        """驱动到 listen 态：broadcast 激活后立即结束 → listen(deadline=+8s)。"""
        va.notify_broadcast(True)
        assert va.mode == "broadcast"
        actions = va.notify_broadcast(False)
        assert va.mode == "listen"
        assert va._deadline == clock() + 8.0
        assert any(a.kind == "status" for a in actions)

    def test_initial_8s_timeout_returns_standby(self):
        clock = _FakeClock()
        va, _ = _make_assistant([], [], clock=clock)
        self._enter_listen(va, clock)
        clock.advance(8.1)
        actions = va.process_chunk(_PCM)
        assert va.mode == "standby"
        assert "submit" not in _kinds(actions)
        assert any(a.kind == "status" and "待机" in a.text for a in actions)

    def test_no_timeout_before_8s(self):
        clock = _FakeClock()
        va, _ = _make_assistant([], [], clock=clock)
        self._enter_listen(va, clock)
        clock.advance(7.9)
        a = va.process_chunk(_PCM)
        assert "submit" not in _kinds(a) and "greet" not in _kinds(a)
        assert va.mode == "listen"

    def test_question_submit_after_2s_silence(self):
        clock = _FakeClock()
        va, sessions = _make_assistant(
            [[("confirmed_start", None)], [("segment", b"x")]], ["司母戊鼎"], clock=clock)
        self._enter_listen(va, clock)
        clock.advance(3.0)
        va.process_chunk(_PCM)            # confirmed_start：开口 → deadline 清 None
        assert va._deadline is None
        a = va.process_chunk(_PCM)        # segment：累积问题 + deadline=+2s
        assert va._question == "司母戊鼎"
        assert va._deadline == clock() + 2.0
        assert any(aa.kind == "msg" and "司母戊鼎" in aa.text for aa in a)
        clock.advance(2.1)
        a = va.process_chunk(_PCM)        # 2s 无新语音 → 提交
        submits = [aa for aa in a if aa.kind == "submit"]
        assert submits and submits[0].text == "司母戊鼎"
        assert va.mode == "await_broadcast"

    def test_extend_timer_loops_on_continued_speech(self):
        """2s 内再开口 → 续接为同一问题（循环延长）。"""
        clock = _FakeClock()
        va, _ = _make_assistant(
            [[("confirmed_start", None)], [("segment", b"x")],
             [("confirmed_start", None)], [("segment", b"y")]],
            ["司母戊鼎", "有多重"], clock=clock)
        self._enter_listen(va, clock)
        clock.advance(1.0)
        va.process_chunk(_PCM); va.process_chunk(_PCM)   # 段1 → "司母戊鼎", +2s
        clock.advance(1.5)                                # 1.5s < 2s 内
        va.process_chunk(_PCM)                            # 段2 confirmed → deadline None
        assert va._deadline is None
        va.process_chunk(_PCM)                            # 段2 结束 → 问题接续
        assert va._question == "司母戊鼎有多重"
        clock.advance(2.1)
        a = va.process_chunk(_PCM)
        assert [aa.text for aa in a if aa.kind == "submit"] == ["司母戊鼎有多重"]

    def test_partial_text_shown_during_speech(self):
        clock = _FakeClock()
        va, _ = _make_assistant([[("confirmed_start", None)], []], ["x"], clock=clock)
        self._enter_listen(va, clock)
        va.process_chunk(_PCM)
        a = va.process_chunk(_PCM)        # 语音进行中：实时部分结果进输入框
        assert any(aa.kind == "msg" and "部分识别" in aa.text for aa in a)


class TestBargeIn:
    def test_barge_in_during_broadcast(self):
        clock = _FakeClock()
        va, sessions = _make_assistant(
            [[("confirmed_start", None)], [("segment", b"x")]], ["换个问题"], clock=clock)
        va.notify_broadcast(True)
        assert va.mode == "broadcast"
        a = va.process_chunk(_PCM)        # 播报中确认语音 → 打断
        assert "barge_in" in _kinds(a)
        assert any(aa.kind == "status" and "⚡" in aa.text for aa in a)
        assert va.mode == "listen" and va._deadline is None
        assert sessions and sessions[0].fed  # 该段语音直接送 ASR（作新问题）
        va.process_chunk(_PCM)            # 段结束 → 问题入累积
        assert va._question == "换个问题"
        clock.advance(2.1)
        a = va.process_chunk(_PCM)
        assert [aa.text for aa in a if aa.kind == "submit"] == ["换个问题"]

    def test_broadcast_end_without_barge_enters_listen(self):
        clock = _FakeClock()
        va, _ = _make_assistant([], [], clock=clock)
        va.notify_broadcast(True)
        a = va.notify_broadcast(False)
        assert va.mode == "listen"
        assert any(aa.kind == "status" and "提问" in aa.text for aa in a)

    def test_broadcast_end_after_barge_not_double_listen(self):
        """打断后播报收尾的 notify(False) 不得覆盖 listen 态的计时。"""
        clock = _FakeClock()
        va, _ = _make_assistant([[("confirmed_start", None)], []], ["x"], clock=clock)
        va.notify_broadcast(True)
        va.process_chunk(_PCM)            # 打断 → listen（说话中，deadline None）
        clock.advance(0.5)
        a = va.notify_broadcast(False)    # 被打断的播报收尾到达
        assert va.mode == "listen" and va._deadline is None and not a


class TestAwaitBroadcast:
    def test_await_timeout_falls_back_standby(self):
        clock = _FakeClock()
        va, _ = _make_assistant(
            [[("confirmed_start", None)], [("segment", b"x")]], ["你好小虎"], clock=clock)
        va.process_chunk(_PCM); va.process_chunk(_PCM)   # 唤醒 → await_broadcast
        assert va.mode == "await_broadcast"
        clock.advance(13.0)               # 播报迟迟未注册（异常）→ 回待机
        a = va.process_chunk(_PCM)
        assert va.mode == "standby"
        assert any(aa.kind == "status" for aa in a)

    def test_await_to_broadcast_on_notify(self):
        va, _ = _make_assistant([], [])
        va._mode = "await_broadcast"
        va._await_since = 0.0
        va.notify_broadcast(True)
        assert va.mode == "broadcast"



def _make_wav_bytes(rate, seconds):
    """合成正弦波 wav 字节（测试夹具用）。"""
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        n = int(rate * seconds)
        w.writeframes(b"".join(
            struct.pack("<h", int(2000 * math.sin(2 * math.pi * 220 * i / rate)))
            for i in range(n)))
    return buf.getvalue()


# ============ T4: app.py 接线（assist 路径 / 注册表 / 自动提交 / 欢迎语） ============

def _assist_settings(monkeypatch, tmp_path, **over):
    import app as app_mod
    from src.config import Settings
    s = Settings(_env_file=None)
    s.project_root = tmp_path
    s.voice_assist_enabled = True
    s.xfyun_app_id, s.xfyun_api_key, s.xfyun_api_secret = "a", "k", "s"
    s.asr_dict_dir = tmp_path
    for k, v in over.items():
        setattr(s, k, v)
    monkeypatch.setattr(app_mod, "settings", s)
    return s


class _FakeAssistant:
    """记录调用、按脚本逐块吐 VoiceAction 的假状态机。"""

    def __init__(self, script):
        self.script = [list(x) for x in script]
        self.notifies = []
        self.closed = False

    def notify_broadcast(self, active):
        self.notifies.append(active)
        return []

    def process_chunk(self, pcm):
        return self.script.pop(0) if self.script else []

    def close(self):
        self.closed = True


class TestAssistDispatch:
    def test_manual_mode_passthrough_arity5(self, monkeypatch):
        """关闭开关 → 退回手动路径，输出补 2 个 no-op（事件输出元数恒为 5）。"""
        import app as app_mod
        from src.config import Settings
        s = Settings(_env_file=None)  # 无讯飞密钥 → 手动路径直接提示
        monkeypatch.setattr(app_mod, "settings", s)
        results = list(app_mod.voice_stream_dispatch(None, None, "", None))
        assert len(results[0]) == 5
        assert "未配置讯飞密钥" in results[0][2]["value"]

    def test_assist_action_translation(self, monkeypatch, tmp_path):
        import app as app_mod
        from src.voice_assistant import VoiceAction
        _assist_settings(monkeypatch, tmp_path)
        fake = _FakeAssistant([
            [VoiceAction("msg", "司母戊鼎"), VoiceAction("status", "🎙 识别中…")],
            [VoiceAction("submit", "司母戊鼎有多重")],
            [VoiceAction("greet")],
            [VoiceAction("barge_in"), VoiceAction("status", "⚡ 已打断播报")],
        ])
        monkeypatch.setattr(app_mod, "_create_voice_assistant", lambda pid: fake)
        monkeypatch.setattr(app_mod, "_to_pcm16k", lambda b, r: b)
        chunk = tmp_path / "c.wav"
        chunk.write_bytes(b"pcm-block")

        r = list(app_mod.voice_stream_dispatch(str(chunk), None, "", None))
        state, msg, status, auto_q, greet = r[-1]
        assert msg["value"] == "司母戊鼎"
        assert "识别中" in status["value"]
        assert "value" not in auto_q and "value" not in greet
        assert fake.notifies == [False]  # 无播报 → notify(False)

        r = list(app_mod.voice_stream_dispatch(str(chunk), state, "", None))
        state = r[-1][0]
        # 修复轮2b：触发器改 gr.State（前端 diff 串线免疫）+ 问题文本走 pending 存储
        assert r[-1][3] == 1  # State 原值输出（递增 nonce 触发 .change）
        assert app_mod._pending_questions.get("anon") == "司母戊鼎有多重"

        r = list(app_mod.voice_stream_dispatch(str(chunk), state, "", None))
        state = r[-1][0]
        assert r[-1][4] == 2  # greet 触发（State nonce）
        assert "anon" in app_mod._pending_greet

        # 注册一个播报 → 打断动作应取消它
        tok = app_mod._register_broadcast(None, "answer")
        r = list(app_mod.voice_stream_dispatch(str(chunk), state, "", None))
        assert tok.cancel.is_set()
        assert "⚡" in r[-1][2]["value"]

    def test_assist_vad_failure_degrades(self, monkeypatch, tmp_path):
        import app as app_mod
        _assist_settings(monkeypatch, tmp_path)
        monkeypatch.setattr(app_mod, "_create_voice_assistant", lambda pid: None)
        monkeypatch.setattr(app_mod, "_to_pcm16k", lambda b, r: b)
        chunk = tmp_path / "c.wav"
        chunk.write_bytes(b"x")
        r = list(app_mod.voice_stream_dispatch(str(chunk), None, "", None))
        assert "VAD" in r[-1][2]["value"]
        r2 = list(app_mod.voice_stream_dispatch(str(chunk), r[-1][0], "", None))
        assert "value" not in r2[-1][2]  # 后续块不再重复刷错误


class TestBroadcastRegistry:
    def test_register_replaces_and_cancels_old(self):
        import app as app_mod
        t1 = app_mod._register_broadcast(None, "answer")
        t2 = app_mod._register_broadcast(None, "answer")
        assert t1.cancel.is_set() and not t2.cancel.is_set()

    def test_active_view_skips_done_and_cancelled(self):
        import app as app_mod
        tok = app_mod._register_broadcast(None, "answer")
        assert app_mod._active_broadcast(None) is tok
        tok.cancel.set()
        assert app_mod._active_broadcast(None) is None  # 打断中 → 视为非激活
        tok2 = app_mod._register_broadcast(None, "answer")
        tok2.done.set()
        assert app_mod._active_broadcast(None) is None


class TestAutoRespond:
    def test_pending_question_delegates_clean_text(self, monkeypatch):
        """问题文本来自服务端 pending 存储（不经组件值）→ 永远干净、无 nonce。"""
        import app as app_mod
        seen = {}

        def fake_respond(message, chat_history, stream, project, tts_enabled, request=None):
            seen["message"] = message
            yield "", chat_history, "[]", gr.update(), gr.update(), gr.update()

        monkeypatch.setattr(app_mod, "respond", fake_respond)
        app_mod._pending_questions["anon"] = "司母戊鼎有多重"
        out = list(app_mod.auto_respond("3", [], True, "museum", True, None))
        assert seen["message"] == "司母戊鼎有多重"
        assert out
        assert "anon" not in app_mod._pending_questions  # 消费一次性

    def test_no_pending_noop(self, monkeypatch):
        import app as app_mod
        monkeypatch.setattr(app_mod, "respond", lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应调用")))
        app_mod._pending_questions.pop("anon", None)
        out = list(app_mod.auto_respond("", [], True, "", True, None))
        assert len(out) == 1 and len(out[0]) == 6



class TestRespondBargeIn:
    """respond 播报打断（audit-ASR 需求4）：cancel → 停止发布/收尾标记/资源释放。"""

    CHARS = ("司母戊鼎是商代晚期的青铜礼器，形制宏大。" * 6)

    def _harness(self, monkeypatch, tmp_path, full):
        import app as app_mod
        import threading as _th
        from src.config import Settings
        s = Settings(_env_file=None)
        s.project_root = tmp_path
        monkeypatch.setattr(app_mod, "settings", s)

        import time as _time

        class FakeHandle:
            def __init__(self, on_audio):
                self.on_audio = on_audio
                self.done = _th.Event()
                self.error = None
                self.cancelled = False
                self.fed = []

            def feed(self, text):
                self.fed.append(text)
                self.on_audio(b"\x00" * int(24000 * 0.2) * 2)  # 每喂 0.2s PCM

            def finish(self):
                self.done.set()

            def cancel(self):
                self.cancelled = True
                self.done.set()

        holder = {}

        class FakeTTS:
            def start_stream(self, on_audio):
                h = FakeHandle(on_audio)
                holder["handle"] = h
                return h

        monkeypatch.setattr(app_mod, "_init_tts", lambda: FakeTTS())

        def fake_answer(q, h, stream, project):
            step = max(1, len(full) // 8)
            for i in range(1, 9):
                part = full[: step * i]
                yield (h + [{"role": "user", "content": q},
                            {"role": "assistant", "content": part}], "[]", part)

        monkeypatch.setattr(app_mod, "answer_question", fake_answer)
        return holder

    @staticmethod
    def _cancel_after_first_feed(app_mod, holder):
        """守望线程：等首个 TTS 片段真正喂入（会话已建）再置打断标记 —— 确定性时序。"""
        import threading as _th
        import time as _time

        def _watch():
            for _ in range(500):
                h = holder.get("handle")
                if h is not None and h.fed:
                    tok = app_mod._active_broadcast(None)
                    if tok is not None:
                        tok.cancel.set()
                        return
                _time.sleep(0.01)

        _th.Thread(target=_watch, daemon=True).start()

    def test_cancel_stops_broadcast(self, monkeypatch, tmp_path):
        import app as app_mod
        holder = self._harness(monkeypatch, tmp_path, self.CHARS)
        self._cancel_after_first_feed(app_mod, holder)
        results = list(app_mod.respond("q", [], True, "", True, None))
        # 打断后：有"已打断"状态；音频批远少于完整播放；句柄被 cancel；token done
        statuses = [r[5].get("value", "") for r in results if isinstance(r[5], dict)]
        assert any("已打断" in s for s in statuses), f"缺少打断状态: {statuses}"
        audio_batches = [r for r in results if isinstance(r[3], bytes)]
        assert len(audio_batches) <= 4, f"打断后仍发布大量音频: {len(audio_batches)}"
        assert holder["handle"].cancelled is True
        assert app_mod._active_broadcast(None) is None  # 已收尾（done）

    def test_normal_run_registers_and_completes(self, monkeypatch, tmp_path):
        """无打断正常播放：token 注册→完成置 done（回归：不影响既有播报）。"""
        import app as app_mod
        self._harness(monkeypatch, tmp_path, self.CHARS[:16])
        captured = {}
        orig_register = app_mod._register_broadcast

        def spy(request, kind):
            captured["tok"] = orig_register(request, kind)
            return captured["tok"]

        monkeypatch.setattr(app_mod, "_register_broadcast", spy)
        # 不触发打断：fake_answer 里 tok.cancel 找不到（spy 包装后仍注册）——改为不打断
        def fake_answer(q, h, stream, project):
            yield (h + [{"role": "user", "content": q},
                        {"role": "assistant", "content": self.CHARS[:16]}], "[]", self.CHARS[:16])

        monkeypatch.setattr(app_mod, "answer_question", fake_answer)
        results = list(app_mod.respond("q", [], True, "", True, None))
        assert captured["tok"].done.is_set()
        assert any(isinstance(r[3], bytes) for r in results)  # 音频正常发布


class TestPlayGreeting:
    def test_greeting_publishes_cached_pcm(self, monkeypatch, tmp_path):
        import app as app_mod
        _assist_settings(monkeypatch, tmp_path)
        monkeypatch.setattr(app_mod, "_greeting_pcm",
                            lambda project: b"\x01\x00" * 24000)  # 1s 假 PCM
        app_mod._pending_greet.add("anon")  # 门闩前提（修复轮2b）
        results = list(app_mod.play_greeting("#1", "museum", True, None))
        # 优化轮3：PCM 就绪走预置静态文件直播 → 服务端不发 HLS 段
        audio = [r[0] for r in results if isinstance(r[0], bytes)]
        assert not audio, "优化轮3：欢迎语不走 HLS 发布（前端 JS 直播预置文件）"
        final_status = [r[2].get("value", "") for r in results if isinstance(r[2], dict)]
        assert any("小虎" in s or "请提问" in s or "唤醒" in s for s in final_status)
        assert app_mod._active_broadcast(None) is None  # token 已 done

    def test_greeting_tts_off_still_completes(self, monkeypatch, tmp_path):
        import app as app_mod
        _assist_settings(monkeypatch, tmp_path)
        app_mod._pending_greet.add("anon")
        results = list(app_mod.play_greeting("#1", "museum", False, None))
        assert app_mod._active_broadcast(None) is None
        assert results  # 至少有一次状态输出

    def test_greeting_pcm_synthesized_once_and_cached(self, monkeypatch, tmp_path):
        import app as app_mod
        _assist_settings(monkeypatch, tmp_path)
        calls = {"n": 0}

        class FakeTTS:
            def synthesize_sentence(self, text):
                calls["n"] += 1
                return _make_wav_bytes(24000, 0.5)

        monkeypatch.setattr(app_mod, "_init_tts", lambda: FakeTTS())
        app_mod._greeting_pcm.cache_clear() if hasattr(app_mod._greeting_pcm, "cache_clear") else None
        p1 = app_mod._greeting_pcm("museum")
        p2 = app_mod._greeting_pcm("museum")
        assert p1 and p1 == p2
        assert calls["n"] == 1, "欢迎语应只合成一次（内存/磁盘缓存）"



# ============ T6: 前端补丁（麦克风 AEC + 语音助手 head JS） ============

class TestMicAecPatch:
    def test_applies_and_idempotent(self):
        """getUserMedia 约束补丁：audio:true → 强制 AEC/降噪/增益（一体机外放防回串）。"""
        from src.audio_bootstrap import patch_gradio_mic_aec
        assert patch_gradio_mic_aec() is True
        assert patch_gradio_mic_aec() is True  # 幂等

    def test_marker_on_disk(self):
        import glob
        import os
        import gradio
        from src.audio_bootstrap import patch_gradio_mic_aec
        patch_gradio_mic_aec()
        assets = os.path.join(os.path.dirname(os.path.abspath(gradio.__file__)),
                              "templates", "frontend", "assets")
        files = glob.glob(os.path.join(assets, "record.esm-*.js"))
        assert files, "未找到 record.esm-*.js"
        src = open(files[0], encoding="utf-8").read()
        assert "echoCancellation:!0" in src
        assert "noiseSuppression:!0" in src

    def test_verify_covers_mic_patch(self):
        """启动自检扩展到麦克风补丁标记。"""
        from src.audio_bootstrap import patch_gradio_mic_aec, verify_frontend_patches
        patch_gradio_mic_aec()
        assert verify_frontend_patches() is True


class TestVoiceAssistHead:
    def test_head_markers(self):
        import app as app_mod
        head = app_mod._voice_assist_head()
        assert "__voiceAssistAutoRecord" in head   # 自动点录音
        assert "__voiceAssistBargeIn" in head      # 打断强停
        # 按钮匹配须覆盖本地化（gradio 6 按浏览器语言渲染：zh-CN=「录制」）
        assert "record" in head and "录制" in head
        assert "voice_audio" in head and "voice_status" in head
        assert "tts_audio" in head and "⚡" in head



# ============ 修复轮：VAD 初始化失败诊断（原因上屏 + 启动自检） ============

class TestVadDiagnostics:
    def test_create_vad_raises_actionable_reason(self):
        """create_vad 抛异常且原因可操作；try_create_vad 契约不变（None）。"""
        from src.vad import create_vad, try_create_vad
        with pytest.raises(FileNotFoundError) as ei:
            create_vad(model_path="nope/missing.onnx")
        assert "silero_vad" in str(ei.value)
        assert try_create_vad(model_path="nope/missing.onnx") is None

    def test_assist_status_shows_failure_reason(self, monkeypatch, tmp_path):
        """降级提示必须带原因（用户不用翻日志就知道修什么）。"""
        import app as app_mod
        _assist_settings(monkeypatch, tmp_path)
        app_mod._assist_init_error = "No module named 'onnxruntime'"
        monkeypatch.setattr(app_mod, "_create_voice_assistant", lambda pid: None)
        monkeypatch.setattr(app_mod, "_to_pcm16k", lambda b, r: b)
        chunk = tmp_path / "c.wav"
        chunk.write_bytes(b"x")
        r = list(app_mod.voice_stream_dispatch(str(chunk), None, "", None))
        assert "onnxruntime" in r[-1][2]["value"]

    def test_startup_probe(self, monkeypatch, tmp_path):
        """启动自检：assist 关→跳过(True)；开但模型路径坏→False；开且正常→True。"""
        import app as app_mod
        s = _assist_settings(monkeypatch, tmp_path)
        s.voice_assist_enabled = False
        assert app_mod._voice_assist_startup_probe() is True
        s.voice_assist_enabled = True
        s.silero_vad_model_path = "nope/missing.onnx"
        assert app_mod._voice_assist_startup_probe() is False
        s.silero_vad_model_path = ""
        assert app_mod._voice_assist_startup_probe() is True



# ============ 修复轮2：常驻状态行 + 倾听态唤醒 ============

class TestPersistentStatus:
    def test_standby_line_emitted_once(self):
        """待机态常驻状态行：首块出现，内容不变不重复刷。"""
        va, _ = _make_assistant([], [])
        a1 = va.process_chunk(_PCM)
        s1 = [a.text for a in a1 if a.kind == "status"]
        assert s1 and "待机" in s1[0] and "你好小虎" in s1[0]
        a2 = va.process_chunk(_PCM)
        assert not [a for a in a2 if a.kind == "status"]  # 无变化不重复

    def test_listen_countdown_updates(self):
        clock = _FakeClock()
        va, _ = _make_assistant([], [], clock=clock)
        va.notify_broadcast(True)
        va.notify_broadcast(False)          # 进 listen（8s）
        clock.advance(1.0)
        a = va.process_chunk(_PCM)
        s = [a.text for a in a if a.kind == "status"]
        assert s and "倾听" in s[0] and "7" in s[0]  # 剩余 ~7s

    def test_broadcast_line_persistent(self):
        va, _ = _make_assistant([], [])
        a = va.notify_broadcast(True)
        s = [x.text for x in a if x.kind == "status"]
        assert s and "播报中" in s[0] and "打断" in s[0]


class TestWakeInListen:
    def test_wake_word_in_listen_triggers_greet_not_submit(self):
        """倾听态整句即唤醒词 → 重新唤醒应答（不当问题提交）。"""
        clock = _FakeClock()
        va, _ = _make_assistant(
            [[("confirmed_start", None)], [("segment", b"x")]], ["你好，小虎"], clock=clock)
        va.notify_broadcast(True); va.notify_broadcast(False)   # 进 listen
        va.process_chunk(_PCM)
        a = va.process_chunk(_PCM)
        assert "greet" in _kinds(a) and "submit" not in _kinds(a)
        assert va.mode == "await_broadcast"
        assert va._question == ""

    def test_wake_prefix_stripped_as_question(self):
        """"你好小虎，司母戊鼎有多高" → 前缀剥离，余下作问题累积。"""
        clock = _FakeClock()
        va, _ = _make_assistant(
            [[("confirmed_start", None)], [("segment", b"x")]],
            ["你好小虎，司母戊鼎有多高"], clock=clock)
        va.notify_broadcast(True); va.notify_broadcast(False)
        va.process_chunk(_PCM)
        a = va.process_chunk(_PCM)
        assert "greet" not in _kinds(a)
        assert va._question == "司母戊鼎有多高"
        clock.advance(2.1)
        a = va.process_chunk(_PCM)
        assert [x.text for x in a if x.kind == "submit"] == ["司母戊鼎有多高"]

    def test_standby_substring_match_unchanged(self):
        """待机态保持子串匹配（回归）。"""
        va, _ = _make_assistant(
            [[("confirmed_start", None)], [("segment", b"x")]], ["那个你好小虎"])
        va.process_chunk(_PCM)
        assert "greet" in _kinds(va.process_chunk(_PCM))



# ============ 修复轮2b：流式收尾 KeyError guard + greet 伪触发门闩 ============

class TestStreamEndstreamGuard:
    def test_final_none_without_opened_stream_no_keyerror(self):
        """流式输出从未开流 + 末趟 None → 不抛 KeyError，值降级为空 update。"""
        import asyncio
        import app as app_mod  # noqa: F401（import 即应用 patch）
        import gradio as gr

        demo = gr.Blocks()
        audio = gr.Audio(streaming=True)

        class _FN:
            outputs = [audio]

        data = [None]
        # 未打 patch 的 gradio 6.22 此处必抛 KeyError（E2E 实证）
        asyncio.run(Blocks_hso(demo, _FN(), data, session_hash="s1", run=999, final=True))
        assert data[0] == {"__type__": "update"}

    def test_patch_idempotent(self):
        from src.audio_bootstrap import patch_gradio_stream_endstream_guard
        assert patch_gradio_stream_endstream_guard() is True
        assert patch_gradio_stream_endstream_guard() is True


def Blocks_hso(demo, fn, data, session_hash, run, final):
    from gradio.blocks import Blocks
    return Blocks.handle_streaming_outputs(
        demo, fn, data, session_hash=session_hash, run=run, final=final)


class TestGreetGate:
    def test_spurious_trigger_without_pending_discarded(self, monkeypatch, tmp_path):
        """组件值串线（'[]' 等伪触发）→ 无 pending → no-op，不播欢迎语。"""
        import app as app_mod
        _assist_settings(monkeypatch, tmp_path)
        app_mod._pending_greet.discard("anon")
        results = list(app_mod.play_greeting("[]", "museum", True, None))
        assert len(results) == 1
        assert all(not isinstance(r[0], bytes) for r in results)  # 无音频

    def test_real_trigger_with_pending_plays(self, monkeypatch, tmp_path):
        import app as app_mod
        _assist_settings(monkeypatch, tmp_path)
        monkeypatch.setattr(app_mod, "_greeting_pcm", lambda project: b"\x01\x00" * 2400)
        app_mod._pending_greet.add("anon")
        results = list(app_mod.play_greeting("#9", "museum", True, None))
        statuses = [r[2].get("value", "") for r in results if isinstance(r[2], dict)]
        assert any("应答中" in s or "已唤醒" in s for s in statuses)  # 快速通道状态输出
        assert "anon" not in app_mod._pending_greet  # 消费一次性



# ============ 优化轮3：唤醒提速（提前命中+预置音频）与转写提速 ============

class TestEarlyWakeOnPartial:
    """待机态：部分结果含唤醒词即提前唤醒，不等 VAD 端点（省 ~1s）。"""

    class _PartialASR:
        """current_text 逐步增长的假 ASR 会话。"""
        def __init__(self, partials, final=""):
            self.partials = list(partials)
            self.final = final
            self.closed = False

        def feed(self, b):
            pass

        @property
        def current_text(self):
            return self.partials.pop(0) if self.partials else ""

        def finish(self):
            return self.final

        def close(self):
            self.closed = True

    def test_wake_on_partial_before_segment_end(self):
        from src.voice_assistant import VoiceAssistant
        # 首个部分结果在 confirmed_start 当块即被读取 → 第1项对应块1
        asr = self._PartialASR(["", "你好，小", "你好，小虎"])
        script = [[("confirmed_start", None)], [], []]  # 无 segment：语音仍在进行
        va = VoiceAssistant(_ScriptedVAD(script), lambda: asr,
                            wake_words=["你好小虎"], greeting="您好，我是小虎")
        va.process_chunk(_PCM)   # confirmed_start，开会话
        a2 = va.process_chunk(_PCM)  # partial "你好，小" → 未命中
        assert "greet" not in _kinds(a2)
        a3 = va.process_chunk(_PCM)  # partial "你好，小虎" → 提前命中
        assert "greet" in _kinds(a3)
        assert va.mode == "await_broadcast"
        assert asr.closed  # 提前唤醒后当前会话立即关闭

    def test_early_wake_ignores_trailing_segment(self):
        """提前唤醒后，同段的 segment 事件不得重复触发/覆盖。"""
        from src.voice_assistant import VoiceAssistant
        asr = self._PartialASR(["", "你好小虎"], final="你好小虎")
        script = [[("confirmed_start", None)], [], [("segment", b"x")], []]
        va = VoiceAssistant(_ScriptedVAD(script), lambda: asr,
                            wake_words=["你好小虎"], greeting="g")
        va.process_chunk(_PCM)
        a = va.process_chunk(_PCM)  # 提前命中
        assert "greet" in _kinds(a)
        a2 = va.process_chunk(_PCM)  # segment 到达：应忽略，不二次 greet
        assert "greet" not in _kinds(a2)
        assert va.mode == "await_broadcast"

    def test_partial_without_wake_no_greet(self):
        from src.voice_assistant import VoiceAssistant
        asr = self._PartialASR(["今天天气", "今天天气怎么样"], final="今天天气怎么样")
        script = [[("confirmed_start", None)], [], [("segment", b"x")], []]
        va = VoiceAssistant(_ScriptedVAD(script), lambda: asr,
                            wake_words=["你好小虎"], greeting="g")
        va.process_chunk(_PCM)
        va.process_chunk(_PCM)
        a = va.process_chunk(_PCM)  # segment 落定：非唤醒词
        assert "greet" not in _kinds(a)
        assert va.mode == "standby"  # 仍在待机


class TestGreetingFastPath:
    """play_greeting 快速通道：PCM 就绪 → 不发 HLS 段，token 等待音频时长后收尾。"""

    def test_token_wait_mode_no_hls_segments(self, monkeypatch, tmp_path):
        import app as app_mod
        _assist_settings(monkeypatch, tmp_path)
        # 0.2s 假 PCM（24k*0.2*2 字节）
        monkeypatch.setattr(app_mod, "_greeting_pcm", lambda project: b"\x01\x00" * 4800)
        app_mod._pending_greet.add("anon")
        t0 = time.time()
        results = list(app_mod.play_greeting("#1", "museum", True, None))
        audio = [r[0] for r in results if isinstance(r[0], bytes)]
        assert not audio, "PCM 就绪时不走 HLS 发布（前端 JS 直播预置文件）"
        assert 0.15 <= time.time() - t0 < 3.0, "token 等待≈音频时长后收尾"
        assert app_mod._active_broadcast(None) is None

    def test_pcm_unavailable_status_only(self, monkeypatch, tmp_path):
        import app as app_mod
        _assist_settings(monkeypatch, tmp_path)
        monkeypatch.setattr(app_mod, "_greeting_pcm", lambda project: None)
        app_mod._pending_greet.add("anon")
        results = list(app_mod.play_greeting("#1", "museum", True, None))
        assert not [r[0] for r in results if isinstance(r[0], bytes)]
        assert app_mod._active_broadcast(None) is None


class TestGreetingAudioEndpoint:
    """预置应答音频端点：GET /__voice_greeting → wav 字节（前端 JS 预加载直播）。"""

    def test_serves_wav_when_pcm_ready(self, monkeypatch, tmp_path):
        import asyncio
        import app as app_mod
        _assist_settings(monkeypatch, tmp_path)
        monkeypatch.setattr(app_mod, "_greeting_pcm", lambda project: b"\x01\x00" * 4800)
        mw = app_mod._GreetingAudioMiddleware(app=None)
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            sent.append(msg)

        asyncio.run(mw({"type": "http", "path": "/__voice_greeting", "method": "GET"}, receive, send))
        assert sent[0]["status"] == 200
        assert sent[1]["body"][:4] == b"RIFF"

    def test_204_when_unavailable(self, monkeypatch, tmp_path):
        import asyncio
        import app as app_mod
        _assist_settings(monkeypatch, tmp_path)
        monkeypatch.setattr(app_mod, "_greeting_pcm", lambda project: None)
        mw = app_mod._GreetingAudioMiddleware(app=None)
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            sent.append(msg)

        asyncio.run(mw({"type": "http", "path": "/__voice_greeting", "method": "GET"}, receive, send))
        assert sent[0]["status"] == 204


class TestSpeedConfig:
    def test_vad_min_silence_default_500(self):
        """提速：段端点 800→500ms（2s 延长计时保证问题完整性，端点激进安全）。"""
        from src.config import Settings
        assert Settings().vad_min_silence_ms == 500

    def test_assist_head_has_greeting_preload(self):
        import app as app_mod
        head = app_mod._voice_assist_head()
        assert "/__voice_greeting" in head and "已唤醒" in head
