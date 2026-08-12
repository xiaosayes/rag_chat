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
