"""语音助手（audit-ASR）测试：VAD / 唤醒状态机 / 双计时 / 打断 / 词典新格式。

全离线：silero 模型为本地 ONNX（pip 包内置）；讯飞/dashscope 一律 fake。
"""
import json
import math
import struct

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
        assert s.vad_min_silence_ms == 800
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
        assert va.mode == "standby" and not _kinds(a1)  # 确认开始不产出动作
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
        assert not va.process_chunk(_PCM)
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
