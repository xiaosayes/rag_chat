"""TTS 语音合成测试（bug-121）：mock dashscope SpeechSynthesizer，不依赖真实 API"""


class TestTtsSplitting:
    def test_short_text_no_split(self):
        from src.tts import CosyVoiceTTS
        parts = CosyVoiceTTS.split_sentences("这是很短的一句话。", max_chars=1000)
        assert parts == ["这是很短的一句话。"]

    def test_split_by_sentence_boundary(self):
        from src.tts import CosyVoiceTTS
        parts = CosyVoiceTTS.split_sentences("第一句。第二句！第三句？", max_chars=100)
        assert parts == ["第一句。", "第二句！", "第三句？"]

    def test_merge_short_sentences_under_limit(self):
        from src.tts import CosyVoiceTTS
        parts = CosyVoiceTTS.split_sentences("你好。你好。你好。", max_chars=10)
        assert "".join(parts) == "你好。你好。你好。"
        assert all(len(p) <= 10 for p in parts)

    def test_overlong_sentence_hard_split(self):
        from src.tts import CosyVoiceTTS
        long = "长" * 50 + "。"
        parts = CosyVoiceTTS.split_sentences(long, max_chars=20)
        assert all(len(p) <= 20 for p in parts)
        assert "".join(parts) == long

    def test_empty_text(self):
        from src.tts import CosyVoiceTTS
        assert CosyVoiceTTS.split_sentences("", max_chars=1000) == []


class _FakeSynth:
    """模拟 dashscope SpeechSynthesizer：调用 call() 时触发 callback。"""

    def __init__(self, mode="ok"):
        self.mode = mode

    def call(self, text, timeout_millis=None):
        cb = self.callback
        cb.on_open()
        if self.mode == "error":
            cb.on_error("mock error")
            return
        cb.on_data(b"fake-wav-part-1")
        cb.on_data(b"fake-wav-part-2")
        cb.on_complete()
        cb.on_close()


class TestCosyVoiceSynthesize:
    def test_synthesize_sentence_returns_collected_bytes(self, monkeypatch):
        from src.tts import CosyVoiceTTS

        captured = {}

        def _fake_synthesizer(**kwargs):
            captured["model"] = kwargs.get("model")
            captured["voice"] = kwargs.get("voice")
            captured["callback"] = kwargs.get("callback")
            synth = _FakeSynth()
            synth.callback = kwargs.get("callback")
            return synth

        monkeypatch.setattr("dashscope.audio.tts_v2.SpeechSynthesizer", _fake_synthesizer)
        tts = CosyVoiceTTS(model="cosyvoice-v3-flash", voice="boy_voice")
        wav = tts.synthesize_sentence("你好")
        assert wav == b"fake-wav-part-1fake-wav-part-2"
        assert captured["model"] == "cosyvoice-v3-flash"
        assert captured["voice"] == "boy_voice"

    def test_synthesize_error_raises(self, monkeypatch):
        from src.tts import CosyVoiceTTS

        def _fake_synthesizer(**kwargs):
            synth = _FakeSynth(mode="error")
            synth.callback = kwargs.get("callback")
            return synth

        monkeypatch.setattr("dashscope.audio.tts_v2.SpeechSynthesizer", _fake_synthesizer)
        tts = CosyVoiceTTS()
        try:
            tts.synthesize_sentence("x")
            assert False, "应抛出 RuntimeError"
        except RuntimeError as e:
            assert "mock error" in str(e)

    def test_synthesize_stream_calls_per_sentence(self, monkeypatch):
        from src.tts import CosyVoiceTTS

        def _fake_synthesizer(**kwargs):
            captured_cb = kwargs.get("callback")
            return type("S", (), {
                "call": lambda self, text, timeout_millis=None: (
                    captured_cb.on_open(),
                    captured_cb.on_data(b"w"),
                    captured_cb.on_complete(),
                    captured_cb.on_close(),
                ),
            })()

        monkeypatch.setattr("dashscope.audio.tts_v2.SpeechSynthesizer", _fake_synthesizer)
        tts = CosyVoiceTTS()
        calls = []
        tts.synthesize_stream("第一句。第二句！", lambda s, w: calls.append((s, w)))
        assert [c[0] for c in calls] == ["第一句。", "第二句！"]
        assert all(c[1] == b"w" for c in calls)

    def test_write_wav(self, tmp_path):
        from src.tts import CosyVoiceTTS
        p = tmp_path / "a.wav"
        CosyVoiceTTS.write_wav(b"data", p)
        assert p.read_bytes() == b"data"
    def test_parens_not_split_into_orphan(self):
        """括号内标点不产生孤立闭合片段（如 "）。"），避免 CosyVoice 报 invalid text。"""
        from src.tts import CosyVoiceTTS
        text = "交通路线（地铁怎么坐？打车定位到哪儿？接驳车在哪上？）。"
        parts = CosyVoiceTTS.split_sentences(text, max_chars=1000)
        assert any("交通路线" in p for p in parts)
        assert all(p.strip() != "）。" for p in parts)
        assert not any(p.startswith("）。") or p == "）" for p in parts)
        assert "".join(parts).replace("\n", "") == text

    def test_balanced_parens_kept_intact(self):
        from src.tts import CosyVoiceTTS
        text = "请参考（详见指南。）。"
        parts = CosyVoiceTTS.split_sentences(text, max_chars=1000)
        assert any("详见指南" in p for p in parts)
        assert all(p.strip() != "）。" for p in parts)
