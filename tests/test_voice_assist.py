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
