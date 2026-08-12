"""audit-TTS：首句 ≤1s + 全程无停顿 —— 单会话流式合成的测试。

根因与方案（详见 README 更新日志 / code_review_report_v3.md）：
- 首播 3s 的根因：分段独立合成 + 等整段合成完成（2s/段）+ 首播攒批门（5 chunk/2s 等待）。
  实测 dashscope 流式首音频块延迟仅 ~0.6s 且与文本长度无关 → 改为**每回答单个
  流式会话**：LLM 文本增量喂入（首段 12 字即喂），音频块边产边播（首播批次 0.25s）。
- 中途停顿的根因：段间会话边界（WS 连接+首块 0.6s）与合成抖动经 hls.js 1s 缓冲放大。
  单会话音频天然连续（无段间边界）；客户端缓冲加深至 60s 吸收残余抖动。
- 第 2 轮无声：前端 patch 补原生 HLS 分支 + /assets/*.js no-cache 强制 revalidate。

本文件全部离线可跑（dashscope SDK 一律 mock）。
"""
import asyncio
import struct
import sys
import threading
import time
import wave
import io
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PCM_RATE = 24000
PCM_BYTES_PER_SEC = PCM_RATE * 2  # 16bit mono


def _pcm(seconds: float, value: int) -> bytes:
    # 16bit mono：每帧 2 字节，rate 帧/秒
    return struct.pack("<h", value) * int(PCM_RATE * seconds)


def _wav_seconds(wav: bytes) -> float:
    with wave.open(io.BytesIO(wav), "rb") as w:
        return w.getnframes() / w.getframerate()


# ---------- 假 dashscope 流式合成器 ----------

class _FakeSynth:
    """模拟 dashscope SpeechSynthesizer 的流式行为（回调线程发音频块）。"""

    instances = []

    def __init__(self, chunks=(), err=None, hang=False, emit_on="finish", **kwargs):
        self.kwargs = kwargs
        self.callback = kwargs.get("callback")
        self.fed = []
        self.chunks = list(chunks)      # finish 时回吐的 PCM 块
        self.err = err
        self.hang = hang                # True → 永不回调（看门狗测试）
        self.emit_on = emit_on          # "finish" | "first_feed"（首喂即吐一块，其余 finish）
        self.cancelled = False
        self.completed = False
        _FakeSynth.instances.append(self)

    def streaming_call(self, text):
        self.fed.append(text)
        if self.hang or self.err:
            return
        if self.emit_on == "first_feed" and len(self.fed) == 1 and self.chunks:
            self.callback.on_data(self.chunks.pop(0))

    def streaming_complete(self):
        if self.hang:
            return
        if self.err:
            self.callback.on_error(self.err)
            return
        self.completed = True
        for c in self.chunks:
            self.callback.on_data(c)
        self.callback.on_complete()

    def streaming_cancel(self):
        self.cancelled = True
        self.callback.on_close()

    def close(self):
        pass

    @staticmethod
    def get_first_package_delay():
        return 600.0

    get_last_request_id = lambda self: "fake"


class TestStreamSession:
    """CosyVoiceTTS.start_stream：单会话增量喂文本 + 音频块回调。"""

    def _start(self, monkeypatch, fake):
        from src.tts import CosyVoiceTTS

        def factory(**kw):
            fake.kwargs = kw                    # 记录构造参数（格式断言）
            fake.callback = kw.get("callback")  # 注入句柄回调
            return fake

        monkeypatch.setattr("dashscope.audio.tts_v2.SpeechSynthesizer", factory)
        tts = CosyVoiceTTS.__new__(CosyVoiceTTS)  # 跳过 __init__ 的 key/ffmpeg 检查
        tts.model, tts.voice = "m", "v"
        tts.speech_rate = 1.1
        received = []
        handle = tts.start_stream(received.append)
        return handle, received

    def test_feed_and_finish_streams_pcm(self, monkeypatch):
        chunks = [_pcm(0.3, 100), _pcm(0.3, 200)]
        self._fake = _FakeSynth(chunks=chunks)
        handle, received = self._start(monkeypatch, self._fake)
        handle.feed("第一句。")
        handle.feed("第二句。")
        handle.finish()
        assert handle.done.wait(timeout=2)
        assert b"".join(received) == b"".join(chunks), "音频块应原样按序回调"
        assert self._fake.fed == ["第一句。", "第二句。"]
        assert handle.error is None

    def test_uses_pcm_format(self, monkeypatch):
        """流式会话必须用 PCM 格式（无 WAV 头，直接可攒批）。"""
        from dashscope.audio.tts_v2 import AudioFormat

        fake = _FakeSynth()
        handle, _ = self._start(monkeypatch, fake)
        assert fake.kwargs.get("format") == AudioFormat.PCM_24000HZ_MONO_16BIT
        handle.cancel()

    def test_error_sets_done_and_error(self, monkeypatch):
        handle, _ = self._start(monkeypatch, _FakeSynth(err="invalid text"))
        handle.feed("x")
        handle.finish()
        assert handle.done.wait(timeout=2)
        assert handle.error and "invalid" in handle.error

    def test_first_audio_at_recorded(self, monkeypatch):
        handle, received = self._start(
            monkeypatch, _FakeSynth(chunks=[_pcm(0.2, 100)], emit_on="first_feed"))
        handle.feed("好的，")
        time.sleep(0.05)
        assert handle.first_audio_at is not None, "首音频块时间应记录（首播延迟度量）"
        handle.finish()
        handle.done.wait(timeout=2)


class TestAdtsStreamer:
    """_AdtsStreamer：单编码器持续 AAC 流 + 帧界切片（音质修复）。

    背景：逐批独立编码的 AAC 段每段带 ~43ms priming 静音 + 拖尾 padding，
    0.9s 段即每 0.9s 一个接口吞音（用户实测“听不清楚”）。单编码器持续流下，
    段只是同一 AAC 流的帧界切片，接口处编码状态连续 → 无缝。
    """

    def _sine_pcm(self, seconds, freq=440, rate=24000):
        import math

        return b"".join(
            struct.pack("<h", int(10000 * math.sin(2 * math.pi * freq * i / rate)))
            for i in range(int(rate * seconds)))

    def _decode(self, adts):
        import shutil
        import subprocess

        out = subprocess.run(
            [shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error",
             "-f", "aac", "-i", "pipe:0", "-f", "s16le", "-ar", "24000", "pipe:1"],
            input=adts, capture_output=True, timeout=30)
        assert out.returncode == 0
        return struct.unpack(f"<{len(out.stdout) // 2}h", out.stdout)

    def test_segments_are_seamless_at_joints(self):
        """切片段解码拼接：接口处无静音坑（RMS 不塌）、非首段无 leading 静音。"""
        from app import _AdtsStreamer

        streamer = _AdtsStreamer(rate=PCM_RATE, seg_seconds=0.9,
                                 first_seg_seconds=0.4, ramp_seconds=(0.6, 0.8))
        streamer.feed(self._sine_pcm(2.0))
        streamer.feed(self._sine_pcm(1.6))
        streamer.finish()
        segs = streamer.collect_all(timeout=10)
        assert len(segs) >= 3, f"3.6s 音频应切 ≥3 段，实际 {len(segs)}"
        # 段时长：爬坡 0.4/0.6/0.8 之后 0.9（AAC 帧量化 ±0.05s）
        from src.audio_bootstrap import _adts_duration

        durs = [_adts_duration(s, PCM_RATE) for s in segs]
        assert abs(durs[0] - 0.4) < 0.1, f"首播段 {durs[0]}"
        assert all(d > 0.3 for d in durs[:-1]), f"非尾段应足额: {durs}"
        assert 3.6 < sum(durs) < 3.9, f"总时长 {sum(durs)}（源 3.6s + 单帧 priming）"
        # 解码拼接：中间段的开头不应有静音坑（连续编码无 priming）；
        # 跳过尾段（ffmpeg 收尾 padding 落在流尾，播放结束处无听感影响）
        decoded = [self._decode(s) for s in segs]
        for k, samples in enumerate(decoded[1:-1], start=1):
            first_big = next((i for i, x in enumerate(samples) if abs(x) > 800), None)
            assert first_big is not None and first_big / 24 < 8.0, \
                f"段{k} 开头仍有 {first_big / 24 if first_big else '?'}ms 静音（priming 未消除）"
        # 接口 ±30ms 能量与整体相当
        joined = [x for part in decoded for x in part]
        rms_full = (sum(x * x for x in joined) / len(joined)) ** 0.5
        pos = 0
        for k, part in enumerate(decoded[:-1]):
            pos += len(part)
            win = joined[max(0, pos - 720): pos + 720]
            rms = (sum(x * x for x in win) / len(win)) ** 0.5
            assert rms > 0.7 * rms_full, \
                f"接口{k} RMS={rms:.0f} vs 整体 {rms_full:.0f}（有吞音）"

    def test_finish_flushes_tail_and_marks_done(self):
        from app import _AdtsStreamer

        streamer = _AdtsStreamer(rate=PCM_RATE, seg_seconds=0.9,
                                 first_seg_seconds=0.4, ramp_seconds=(0.6,))
        streamer.feed(self._sine_pcm(1.23))
        streamer.finish()
        segs = streamer.collect_all(timeout=10)
        total = sum(len(s) for s in segs)
        assert total > 0 and streamer.done.is_set()

    def test_collect_timeout_when_empty(self):
        from app import _AdtsStreamer

        streamer = _AdtsStreamer(rate=PCM_RATE, seg_seconds=0.9,
                                 first_seg_seconds=0.4, ramp_seconds=(0.6,))
        t0 = time.time()
        assert streamer.collect(timeout=0.3) == []
        assert 0.25 < time.time() - t0 < 1.0
        streamer.close()


import contextlib


@contextlib.contextmanager
def _capture_loguru(level="WARNING"):
    """loguru 日志捕获（loguru 不走 stdlib logging，caplog 拿不到）。"""
    from loguru import logger

    msgs = []
    sink = logger.add(lambda m: msgs.append(str(m)), level=level)
    try:
        yield msgs
    finally:
        logger.remove(sink)


class TestStallBeacon:
    """客户端停顿遥测（audit-TTS）：head 探针 + /__tts_stall 中间件。"""

    def _run_asgi(self, mw, scope, body=b""):
        import asyncio

        messages = []

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(msg):
            messages.append(msg)

        asyncio.run(mw(scope, receive, send))
        return messages

    def test_beacon_path_logs_and_204(self):
        from app import _TtsStallBeaconMiddleware

        async def dummy_app(scope, receive, send):
            raise AssertionError("不应透传")

        mw = _TtsStallBeaconMiddleware(dummy_app)
        scope = {"type": "http", "path": "/__tts_stall", "method": "POST"}
        with _capture_loguru() as logs:
            msgs = self._run_asgi(mw, scope,
                                  b'{"stall_s":2.3,"pos":45.1,"ahead":0.0}')
        assert msgs[0]["status"] == 204
        assert any("客户端停顿上报" in m and "45.1" in m for m in logs), logs

    def test_other_paths_pass_through(self):
        from app import _TtsStallBeaconMiddleware

        called = []

        async def dummy_app(scope, receive, send):
            called.append(scope["path"])
            await send({"type": "http.response.start", "status": 200, "headers": []})

        mw = _TtsStallBeaconMiddleware(dummy_app)
        scope = {"type": "http", "path": "/assets/x.js", "method": "GET"}
        msgs = self._run_asgi(mw, scope)
        assert called == ["/assets/x.js"] and msgs[0]["status"] == 200

    def test_bad_body_still_204(self):
        from app import _TtsStallBeaconMiddleware

        async def dummy_app(scope, receive, send):
            raise AssertionError("不应透传")

        mw = _TtsStallBeaconMiddleware(dummy_app)
        scope = {"type": "http", "path": "/__tts_stall", "method": "POST"}
        with _capture_loguru() as logs:
            msgs = self._run_asgi(mw, scope, b"not-json")
        assert msgs[0]["status"] == 204
        assert any("解析失败" in m for m in logs)

    def test_probe_head_contains_beacon_and_threshold(self):
        from app import _TTS_STALL_PROBE_HEAD

        assert "/__tts_stall" in _TTS_STALL_PROBE_HEAD
        assert "waiting" in _TTS_STALL_PROBE_HEAD and "playing" in _TTS_STALL_PROBE_HEAD
        assert "0.4" in _TTS_STALL_PROBE_HEAD, "停顿上报阈值 0.4s（第五轮从 0.8 下调）"
        assert "stalled" in _TTS_STALL_PROBE_HEAD, "补抓 stalled 事件"
        assert "seeking" in _TTS_STALL_PROBE_HEAD and "seeked" in _TTS_STALL_PROBE_HEAD, \
            "补抓 hls.js 跳缝（seek 跳变）"
        assert "readyState" in _TTS_STALL_PROBE_HEAD, "负载带 readyState/networkState 供定性"


class TestFeedUnits:
    """喂入单元切分（第五轮断句修复）：streaming_call 边界烙停顿 → 只切自然边界。"""

    # ---- _take_first_unit（首播单元） ----
    def test_first_unit_prefers_sentence_end(self):
        from app import _take_first_unit

        seg, rest = _take_first_unit("大家好。这件展品是清代" + "的" * 40)
        assert seg == "大家好。" and rest.startswith("这件")

    def test_first_unit_comma_fallback_after_8(self):
        """逗号兜底 ≥8 字即生效（保首播速度；逗号处停顿自然）。"""
        from app import _take_first_unit

        seg, rest = _take_first_unit("这件青花瓷器，制作于清代乾隆年间" + "窑" * 20)
        assert seg == "这件青花瓷器，" and rest.startswith("制作于")

    def test_first_unit_waits_without_boundary(self):
        from app import _take_first_unit

        buf = "青" * 20  # 无标点且不足 80 → 等
        seg, rest = _take_first_unit(buf)
        assert seg == "" and rest == buf

    def test_first_unit_hard_cap(self):
        from app import _take_first_unit

        seg, rest = _take_first_unit("青" * 100)
        assert len(seg) == 80 and len(rest) == 20

    # ---- _take_feed_unit（后续批量单元） ----
    def test_feed_unit_batches_full_sentences(self):
        from app import _take_feed_unit

        buf = "第一句。第二句。第三句还没完"
        seg, rest = _take_feed_unit(buf, min_chars=8)
        assert seg == "第一句。第二句。" and rest.endswith("还没完")

    def test_feed_unit_waits_for_min_chars(self):
        from app import _take_feed_unit

        buf = "短句。下一句还没完"
        seg, rest = _take_feed_unit(buf, min_chars=60)
        assert seg == "" and rest == buf, "不足批量且未断粮 → 等"

    def test_feed_unit_starve_guard(self):
        from app import _take_feed_unit

        buf = "短句。下一句还没完"
        seg, rest = _take_feed_unit(buf, min_chars=60, starve=True)
        assert seg == "短句。", "断粮守卫：有完整句即喂"

    def test_feed_unit_hard_cut_swallows_orphan_punct(self):
        from app import _take_feed_unit

        buf = "青" * 200 + "。下一句"
        seg, rest = _take_feed_unit(buf, min_chars=60, max_chars=200)
        assert len(seg) == 200 and rest == "下一句", "硬切应吞掉剩余缓冲开头的孤立标点"

    def test_feed_unit_waits_for_sentence_end(self):
        from app import _take_feed_unit

        buf = "青" * 150  # 无句末标点且不足 200 → 等
        seg, rest = _take_feed_unit(buf, min_chars=60, max_chars=200)
        assert seg == "" and rest == buf


class TestPauseCompressor:
    """_PauseCompressor：>cap 的静默窗丢弃，≤cap 的原样保留（喂入边界烙停顿的修复）。"""

    def _tone(self, seconds, amp=8000):
        import math

        return b"".join(
            struct.pack("<h", int(amp * math.sin(2 * math.pi * 440 * i / PCM_RATE)))
            for i in range(int(PCM_RATE * seconds)))

    def test_long_silence_compressed_to_cap(self):
        from app import _PauseCompressor, _audit_silence

        c = _PauseCompressor(rate=PCM_RATE, cap_s=0.35)
        src = self._tone(1.0) + _pcm(1.2, 0) + self._tone(1.0)
        out = c.feed(src) + c.flush()
        dur = len(out) / 2 / PCM_RATE
        assert abs(dur - (2.0 + 0.35)) < 0.05, f"压缩后 {dur}s（应≈2.35s）"
        assert _audit_silence(out) == [], "压缩后不应有 ≥0.6s 静默"
        assert c.dropped_s > 0.7, "应丢弃 ~0.85s"

    def test_short_silence_untouched(self):
        from app import _PauseCompressor

        c = _PauseCompressor(rate=PCM_RATE, cap_s=0.35)
        src = self._tone(0.5) + _pcm(0.2, 0) + self._tone(0.5)
        out = c.feed(src) + c.flush()
        assert abs(len(out) / 2 / PCM_RATE - 1.2) < 0.05, "0.2s 气口不应压缩"

    def test_continuous_tone_passthrough(self):
        from app import _PauseCompressor

        c = _PauseCompressor(rate=PCM_RATE)
        src = self._tone(3.0)
        out = c.feed(src) + c.flush()
        assert abs(len(out) - len(src)) < PCM_RATE * 2 * 0.03

    def test_chunked_feed_equals_one_shot(self):
        """SDK 回调分片大小不定：乱序分片喂入应与一次喂入结果一致。"""
        from app import _PauseCompressor

        src = self._tone(0.5) + _pcm(1.0, 0) + self._tone(0.5)
        c1 = _PauseCompressor(rate=PCM_RATE, cap_s=0.35)
        one = c1.feed(src) + c1.flush()
        c2 = _PauseCompressor(rate=PCM_RATE, cap_s=0.35)
        parts = []
        i = 0
        for step in (777, 3, 2048, 99, 5000):
            parts.append(c2.feed(src[i:i + step]))
            i += step
        parts.append(c2.feed(src[i:]) + c2.flush())
        assert b"".join(parts) == one

    def test_all_silence_capped(self):
        from app import _PauseCompressor

        c = _PauseCompressor(rate=PCM_RATE, cap_s=0.35)
        out = c.feed(_pcm(5.0, 0)) + c.flush()
        assert len(out) / 2 / PCM_RATE <= 0.4


class TestSilenceAudit:
    """_audit_silence：播报内容静默审计（客户端零上报但有停顿时给内容侧定性）。"""

    def _tone(self, seconds, amp=8000):
        import math

        return b"".join(
            struct.pack("<h", int(amp * math.sin(2 * math.pi * 440 * i / PCM_RATE)))
            for i in range(int(PCM_RATE * seconds)))

    def test_detects_baked_silence_with_positions(self):
        from app import _audit_silence

        pcm = (self._tone(2.0) + _pcm(1.2, 0) + self._tone(1.0)
               + _pcm(0.3, 0) + self._tone(2.0))  # 0.3s 静默不达阈值
        runs = _audit_silence(pcm)
        assert len(runs) == 1, f"只应报 1.2s 那处: {runs}"
        a, b = runs[0]
        assert abs(a - 2.0) < 0.05 and abs(b - 3.2) < 0.05, runs

    def test_continuous_tone_no_runs(self):
        from app import _audit_silence

        assert _audit_silence(self._tone(5.0)) == []

    def test_head_tail_silence_ignored(self):
        from app import _audit_silence

        pcm = _pcm(1.0, 0) + self._tone(3.0) + _pcm(1.0, 0)  # 首尾静默属起音/拖尾
        assert _audit_silence(pcm) == []

    def test_short_audio_skipped(self):
        from app import _audit_silence

        assert _audit_silence(_pcm(0.5, 0)) == []


class TestFrontendPatchVerify:
    """verify_frontend_patches：复读磁盘 StaticAudio-*.js 确认三标记落盘。"""

    def test_markers_present_after_patch(self):
        import app  # noqa: F401 —— 模块导入即应用 patch
        from src.audio_bootstrap import verify_frontend_patches

        assert verify_frontend_patches(), "本地 gradio 6.22 应三标记齐全"

    def test_missing_file_returns_false(self, monkeypatch):
        import glob

        from src.audio_bootstrap import verify_frontend_patches

        monkeypatch.setattr(glob, "glob", lambda *a, **k: [])
        with _capture_loguru("ERROR") as logs:
            assert verify_frontend_patches() is False
        assert any("自检" in m for m in logs)


class TestSpeechRate:
    """speech_rate（语速倍率）透传流式/非流式两条 SDK 路径（用户验收 1.1x）。"""

    def test_speech_rate_passed_to_sdk(self, monkeypatch):
        import dashscope.audio.tts_v2 as tts_v2
        from src.tts import CosyVoiceTTS

        captured = []

        class _FakeSynth:
            def __init__(self, **kw):
                captured.append(kw)
                self._kw = kw

            def call(self, text):  # 非流式路径：立即完成
                self._kw["callback"].on_complete()

        monkeypatch.setattr(tts_v2, "SpeechSynthesizer", _FakeSynth)
        tts = CosyVoiceTTS(voice="v", speech_rate=1.1)
        tts.start_stream(on_audio=lambda pcm: None)  # 流式路径构造 SpeechSynthesizer
        tts.synthesize_sentence("测试")             # 非流式路径
        assert len(captured) == 2, captured
        assert all(c.get("speech_rate") == 1.1 for c in captured), captured

    def test_default_speech_rate_1_1(self):
        """字段定义默认 1.1（用户验收值）；查类定义不受 .env 覆盖影响。"""
        from src.config import Settings

        default = Settings.model_fields["tts_speech_rate"].default
        assert abs(default - 1.1) < 1e-6


class TestWrapPcm:
    """_wrap_pcm：原始 PCM → wav（重播文件用）。"""

    def test_wrap_params(self):
        from app import _wrap_pcm

        w = _wrap_pcm(_pcm(0.3, 1), PCM_RATE)
        with wave.open(io.BytesIO(w), "rb") as fh:
            assert (fh.getframerate(), fh.getnchannels(), fh.getsampwidth()) == (PCM_RATE, 1, 2)
            assert abs(fh.getnframes() / PCM_RATE - 0.3) < 0.01


class TestStreamingStallSim:
    """单会话流式模型的停顿仿真（scripts/tts_stall_sim.py v2）：

    连续发布 ~2.6x 实时；中段注入 15s 挂起（看门狗阈值内）。
    客户端 1s 缓冲（未 patch）→ >2s 停顿（复现基线）；
    客户端 60s 缓冲（已 patch）→ 无停顿（验收）。
    """

    def test_shallow_buffer_stalls_on_mid_stream_hang(self):
        """修复前生产配置（1s 缓冲 + TD 蠕变）：中段挂起 → 长停顿（复现基线）。"""
        from scripts.tts_stall_sim import run_streaming_config

        r = run_streaming_config(total_audio=60.0, hang_at=20.0, hang_dur=15.0,
                                 buffer_cap=1.0, td_mode="growing")
        assert r["max_stall"] > 2.0, f"基线应复现停顿，实际 {r['max_stall']:.2f}s"

    def test_td_fix_alone_prevents_stall(self):
        """TD 修正是决定性的：即使客户端只有 1s 缓冲，poll 不再饿死 → 无停顿。"""
        from scripts.tts_stall_sim import run_streaming_config

        r = run_streaming_config(total_audio=60.0, hang_at=20.0, hang_dur=15.0,
                                 buffer_cap=1.0, td_mode="fixed")
        assert r["max_stall"] < 0.5, f"TD 修正后应无停顿，实际 {r['max_stall']:.2f}s"

    def test_deep_buffer_absorbs_mid_stream_hang(self):
        from scripts.tts_stall_sim import run_streaming_config

        r = run_streaming_config(total_audio=60.0, hang_at=20.0, hang_dur=15.0,
                                 buffer_cap=60.0)
        assert r["max_stall"] < 0.5, f"60s 缓冲应吸收挂起，实际 {r['max_stall']:.2f}s"

    def test_deep_buffer_alone_insufficient_without_td_fix(self):
        """仅加深缓冲不够：gradio TD 蠕变未修时，无更新重载延迟=TD/2 随段数膨胀，
        挂起后的恢复音频迟迟不被发现 → 仍长停顿（TD patch 必要性）。"""
        from scripts.tts_stall_sim import run_streaming_config

        r = run_streaming_config(total_audio=60.0, hang_at=20.0, hang_dur=15.0,
                                 buffer_cap=60.0, td_mode="growing")
        assert r["max_stall"] > 2.0, f"TD 蠕变未修应仍停顿，实际 {r['max_stall']:.2f}s"


class TestAudioTranscodePatch:
    """audio_bootstrap.patch_gradio_audio_transcode：单 ffmpeg 进程转码。
    实测（Windows）：pydub 双进程 ~0.7s/批 vs 单进程 ~0.23s/批 —— 发布吞吐决定停顿。"""

    def _wav(self, seconds=0.5):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(PCM_RATE)
            w.writeframes(_pcm(seconds, 100))
        return buf.getvalue()

    def test_patch_applies_idempotent_and_output_valid(self):
        from src.audio_bootstrap import patch_gradio_audio_transcode

        assert patch_gradio_audio_transcode() is True
        assert patch_gradio_audio_transcode() is True  # 幂等
        from gradio.components.audio import Audio

        adts, dur = Audio._convert_to_adts(self._wav(0.5))
        assert adts[:1] == b"\xff" and adts[1] & 0xF0 == 0xF0, "应为 ADTS 同步头"
        # EXTINF 必须是真实解码时长（AAC priming/padding → >源 wav 时长），
        # 否则 playlist 时间线漂移、MSE 出空洞（48ms/段，audit-TTS 实证）
        from src.audio_bootstrap import _adts_duration

        assert dur == _adts_duration(adts, PCM_RATE) > 0.5

    def test_adts_duration_counts_frames(self):
        from src.audio_bootstrap import _adts_duration

        adts, _ = __import__("gradio.components.audio", fromlist=["Audio"]).Audio._convert_to_adts(
            self._wav(2.0))
        dur = _adts_duration(adts, PCM_RATE)
        assert 2.0 < dur < 2.1, f"2s 源应得 ~2.048s（含 priming），实际 {dur}"
        assert _adts_duration(b"", PCM_RATE) == 0.0
        assert _adts_duration(b"garbage", PCM_RATE) == 0.0

    def test_fallback_when_ffmpeg_missing(self, monkeypatch):
        """ffmpeg 不在 PATH 时回退原 pydub 路径，输出仍合法。"""
        import src.audio_bootstrap as boot
        from gradio.components.audio import Audio

        assert boot.patch_gradio_audio_transcode() is True
        # patch 内为函数级 import shutil → 需打全局模块属性
        monkeypatch.setattr("shutil.which", lambda _cmd: None)
        adts, dur = Audio._convert_to_adts(self._wav(0.5))
        assert adts[:1] == b"\xff", "回退路径仍应产出 ADTS"
        assert 0.4 < dur < 0.65


class TestMediaStreamTargetDuration:
    """audio_bootstrap.patch_gradio_media_stream_targetduration：
    gradio MediaStream.max_duration 每段 +1 蠕变 → 修正为最大段时长（HLS 规范）。"""

    def test_max_duration_not_growing(self):
        # TD = clamp(ceil(最大段时长), 1, 5)：不蠕变、规范内、轮询与缓冲解耦
        from src.audio_bootstrap import patch_gradio_media_stream_targetduration

        assert patch_gradio_media_stream_targetduration() is True
        from gradio import route_utils

        s = route_utils.MediaStream()
        for _ in range(3):
            asyncio.run(s.add_segment({"data": b"x", "duration": 0.5, "extension": ".aac"}))
        assert s.max_duration == 1, f"≤1s 段 TD 应为 1（轮询与缓冲解耦），实际 {s.max_duration}"
        asyncio.run(s.add_segment({"data": b"x", "duration": 6.5, "extension": ".aac"}))
        assert s.max_duration == 5, f"TD 应 clamp 到 5，实际 {s.max_duration}"

    def test_patch_idempotent(self):
        from src.audio_bootstrap import patch_gradio_media_stream_targetduration

        assert patch_gradio_media_stream_targetduration() is True
        assert patch_gradio_media_stream_targetduration() is True


class TestRespondStreaming:
    """respond 集成：单会话流式播报。"""

    CHARS = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥乾坤震巽坎离艮兑天地玄黄宇宙洪荒日月盈昃辰宿列张律吕调阳云腾致雨露结为霜金生丽水玉出昆冈"

    def _settings(self, monkeypatch, tmp_path, **overrides):
        import app as app_mod
        from src.config import Settings

        s = Settings(_env_file=None)
        s.project_root = tmp_path
        for k, v in overrides.items():
            setattr(s, k, v)
        monkeypatch.setattr(app_mod, "settings", s)
        return s

    def _fake_answer(self, full, chunks=8):
        def fake_answer(q, h, stream, project):
            step = max(1, len(full) // chunks)
            for i in range(1, chunks + 1):
                part = full[: step * i]
                yield (h + [{"role": "user", "content": q},
                            {"role": "assistant", "content": part}], "[]", part)
        return fake_answer

    class _FakeHandle:
        """模仿 start_stream 返回的句柄：按喂入文本量即时回吐 PCM（每字 0.2s 音频）。"""

        def __init__(self, on_audio, per_char=0.2, hang=False, err=None):
            self.on_audio = on_audio
            self.per_char = per_char
            self.hang = hang
            self.err = err
            self.fed = []
            self.cancelled = False
            self.first_audio_at = None
            self.done = threading.Event()
            self.error = None

        def feed(self, text):
            self.fed.append(text)
            if self.hang:
                return
            if self.err and len(self.fed) >= 2:
                self.error = self.err
                self.done.set()
                return
            if self.first_audio_at is None:
                self.first_audio_at = time.time()
            idx = len(self.fed) - 1
            self.on_audio(_pcm(len(text) * self.per_char, 1000 + idx))

        def finish(self):
            if not self.hang:  # 挂起会话永不完成（看门狗场景）
                self.done.set()

        def cancel(self):
            self.cancelled = True
            self.done.set()

    def _install_fake_tts(self, monkeypatch, handle_factory):
        import app as app_mod

        class FakeTTS:
            def start_stream(self, on_audio):
                self.handle = handle_factory(on_audio)
                return self.handle

        holder = {}

        class TTS(FakeTTS):
            def start_stream(self, on_audio):
                h = handle_factory(on_audio)
                holder.setdefault("handles", []).append(h)
                return h

        monkeypatch.setattr(app_mod, "_init_tts", lambda: TTS())
        return holder

    def test_first_audio_yielded_mid_stream(self, monkeypatch, tmp_path):
        """首播批必须在回答流中途产出（不等回答结束），且紧接首次喂文本。"""
        import app as app_mod

        self._settings(monkeypatch, tmp_path)
        full = self.CHARS[:80]
        holder = self._install_fake_tts(
            monkeypatch, lambda on_audio: self._FakeHandle(on_audio))
        monkeypatch.setattr(app_mod, "answer_question", self._fake_answer(full))

        t0 = time.time()
        results = list(app_mod.respond("q", [], True, "museum", True))
        batches = [(i, r[3]) for i, r in enumerate(results) if isinstance(r[3], bytes)]
        assert batches, "应有音频批"
        first_idx, first_batch = batches[0]
        assert first_idx < len(results) - 2, "首播批出现在结尾阶段，未实现边产边播"
        from src.audio_bootstrap import _adts_duration

        assert _adts_duration(first_batch, PCM_RATE) <= 0.5, "首播批次应小（快速开播）"
        # 假会话零延迟下，首播应紧随首个 LLM chunk（<1s，真机实测由 E2E 验收 ≤1s）
        assert time.time() - t0 < 5  # 流程不被 15s 看门狗拖住

    def test_replay_complete_and_ordered(self, monkeypatch, tmp_path):
        """重播文件 = 全部批次顺序拼接（PCM 常数编码段序可校验）。"""
        import app as app_mod

        self._settings(monkeypatch, tmp_path)
        full = self.CHARS[:60]
        holder = self._install_fake_tts(
            monkeypatch, lambda on_audio: self._FakeHandle(on_audio))
        monkeypatch.setattr(app_mod, "answer_question", self._fake_answer(full))

        results = list(app_mod.respond("q", [], True, "museum", True))
        replay = [r[4] for r in results if isinstance(r[4], dict) and r[4].get("value")]
        assert replay, "应生成重播文件"
        with wave.open(str(replay[-1]["value"]), "rb") as w:
            pcm = w.readframes(w.getnframes())
        values = struct.unpack(f"<{len(pcm) // 2}h", pcm)
        runs = []
        for v in values:
            if not runs or runs[-1] != v:
                runs.append(v)
        assert runs == sorted(runs), f"播报乱序: {runs}"
        # 完整性：重播时长 ≈ 喂入文本字数 × 0.2s
        handle = holder["handles"][0]
        total_fed = sum(len(t) for t in handle.fed)
        assert abs(len(pcm) / 2 / PCM_RATE - total_fed * 0.2) < 0.6

    def test_session_error_does_not_break_answer(self, monkeypatch, tmp_path):
        """会话中途报错：回答不受影响，已播音频保留，重播仍生成。"""
        import app as app_mod

        self._settings(monkeypatch, tmp_path)
        full = self.CHARS[:60]
        self._install_fake_tts(
            monkeypatch, lambda on_audio: self._FakeHandle(on_audio, err="stream broken"))
        monkeypatch.setattr(app_mod, "answer_question", self._fake_answer(full))

        results = list(app_mod.respond("q", [], True, "museum", True))
        chatbot_updates = [r[1] for r in results if isinstance(r[1], list)]
        assert chatbot_updates and self.CHARS[:10] in str(chatbot_updates[-1])
        status = [r[5] for r in results if isinstance(r[5], dict) and r[5].get("value")]
        assert status, "应有状态输出"

    def test_hang_triggers_watchdog_restart(self, monkeypatch, tmp_path):
        """会话挂起（无音频无完成）：看门狗超时 → cancel + 重建会话继续。"""
        import app as app_mod

        # 看门狗缩短到 0.3s 便于测试
        self._settings(monkeypatch, tmp_path, tts_stream_watchdog_seconds=0.3)
        full = self.CHARS[:40]
        calls = {"n": 0}

        def factory(on_audio):
            calls["n"] += 1
            if calls["n"] == 1:
                return self._FakeHandle(on_audio, hang=True)   # 首个会话挂起
            return self._FakeHandle(on_audio)                  # 重启后正常

        holder = self._install_fake_tts(monkeypatch, factory)
        monkeypatch.setattr(app_mod, "answer_question", self._fake_answer(full))

        t0 = time.time()
        results = list(app_mod.respond("q", [], True, "museum", True))
        assert time.time() - t0 < 10, "看门狗未及时介入"
        assert holder["handles"][0].cancelled, "挂起会话应被取消"
        assert len(holder["handles"]) >= 2, "应重建会话"
        batches = [r[3] for r in results if isinstance(r[3], bytes)]
        assert batches, "重启后应继续播报"


class TestFrontendPatchExtended:
    """audio_bootstrap.patch_gradio_hls_reuse 扩展：原生 HLS 分支 + 客户端缓冲加深。"""

    def _js(self):
        import glob
        import os

        import gradio

        assets = os.path.join(os.path.dirname(gradio.__file__),
                              "templates", "frontend", "assets")
        js = glob.glob(os.path.join(assets, "StaticAudio-*.js"))
        assert js, "找不到 gradio StaticAudio 前端 JS"
        return open(js[0], encoding="utf-8").read()

    def test_native_hls_branch_no_longer_once_only(self):
        """原生 HLS 分支（Safari/无 MSE）不得再用 Se||= 一次性赋值（第 2 轮无声）。
        且必须按 URL 去重（每批 yield 都触发 ke()，无条件重赋值会中断播放）。"""
        from src.audio_bootstrap import patch_gradio_hls_reuse

        assert patch_gradio_hls_reuse() is True
        src = self._js()
        assert "Se||=(M.src=e.url" not in src, "原生 HLS 分支仍是一次性赋值（第 2 轮无声）"
        assert "if(Se!==e.url)M.src=e.url" in src, "原生分支应按 URL 去重"

    def test_hls_recreate_only_on_url_change(self):
        """hls.js 分支：同 URL 直接 return（复用），新 URL 才 destroy 重建。
        audit-TTS 关键：前端 effect 在每个音频批 yield 都重跑 ke()——无条件重建会
        每 ~0.8s 销毁 MediaSource（缓冲清空重载），是中途停顿主因（E2E 实证）。"""
        src = self._js()
        assert "if(Se.url===e.url)return" in src, "ke() 同 URL 应直接返回（复用）"
        assert "Se.destroy(),Se=!1" in src, "ke() 新 URL 应 destroy 后重建"

    def test_hls_buffer_deepened(self):
        """hls.js 缓冲从 1s 加深（客户端只缓冲 1s 是停顿放大器）。"""
        src = self._js()
        assert "maxBufferLength:1," not in src, "hls.js 客户端缓冲仍为 1s"
        assert "maxBufferLength:60" in src

    def test_low_latency_mode_disabled(self):
        """lowLatencyMode 让播放器贴 live edge（前向缓冲恒~0s，E2E 实证）→ 必须关闭，
        深缓冲才能生效。"""
        from src.audio_bootstrap import patch_gradio_hls_reuse

        assert patch_gradio_hls_reuse() is True
        src = self._js()
        assert "maxBufferLength:60,maxMaxBufferLength:60,lowLatencyMode:!1" in src


class TestNoCacheAssetsMiddleware:
    """/assets/*.js 强制 revalidate：patch 原地改文件、哈希文件名不变，
    浏览器启发式缓存（≈10%×文件年龄）会让客户端长期跑 patch 前的旧 JS。"""

    def _headers(self, path):
        from app import _NoCacheAssetsMiddleware

        async def fake_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        mw = _NoCacheAssetsMiddleware(fake_app)
        sent = []

        async def send(msg):
            sent.append(msg)

        asyncio.run(mw({"type": "http", "path": path}, None, send))
        start = next(m for m in sent if m["type"] == "http.response.start")
        return {k.decode(): v.decode() for k, v in start["headers"]}

    def test_assets_js_no_cache(self):
        headers = self._headers("/assets/StaticAudio-DLiZOKt2.js")
        assert "no-cache" in headers.get("cache-control", "")

    def test_assets_js_no_cache_under_root_path(self):
        # 反向代理 root_path 部署下也应命中
        headers = self._headers("/root/assets/index-abc.js")
        assert "no-cache" in headers.get("cache-control", "")

    def test_other_paths_untouched(self):
        assert "cache-control" not in self._headers("/gradio_api/queue/data")
        assert "cache-control" not in self._headers("/assets/style.css")
