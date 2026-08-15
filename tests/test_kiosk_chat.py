# web-006：TTS 喂入切分 + 静默压缩（移植自 app.py audit-TTS，语义保真）
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
