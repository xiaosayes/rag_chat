"""语音功能 UI 集成测试（bug-121）：mock ASR/TTS，不依赖真实 API"""
import json
from pathlib import Path

import pytest


class _FakeASR:
    """模拟 IflytekASR：满足 app 层调用（feed/correct/current_text/is_final/finish）。"""

    def __init__(self, *a, **kw):
        self.fed = []
        self._current = ""

    def feed(self, pcm):
        self.fed.append(pcm)
        self._current = "你好"
        return self._current

    def correct(self, text):
        return text

    @property
    def current_text(self):
        return self._current

    def is_final(self):
        return False

    def finish(self):
        return self._current


class TestAsrStreamChunk:
    def test_no_keys_returns_message(self, monkeypatch):
        from app import asr_stream_chunk
        from src.config import Settings

        s = Settings(_env_file=None)
        monkeypatch.setattr("app.settings", s)  # 全部为空 → 未配置提示
        results = list(asr_stream_chunk(None, None, ""))
        state, msg_update, status = results[0]
        assert "未配置讯飞密钥" in status["value"]

    def test_new_block_feeds_incremental(self, monkeypatch, tmp_path):
        """gradio 6.22 每 0.5s 发一个独立 wav 增量块（size 恒定）→ 每块独立转 PCM 追加、增量 feed。

        用户要求边说边出字：每次 feed 后实时把 wpgs 部分结果填入输入框。
        """
        from app import asr_stream_chunk
        from src.config import Settings

        s = Settings(_env_file=None)
        s.xfyun_app_id = "a"
        s.xfyun_api_key = "k"
        s.xfyun_api_secret = "s"
        s.asr_dict_dir = tmp_path
        monkeypatch.setattr("app.settings", s)
        monkeypatch.setattr("app.IflytekASR", _FakeASR)
        monkeypatch.setattr("app._to_pcm16k", lambda b, r: b)
        chunk = tmp_path / "chunk.wav"
        chunk.write_bytes(b"block-1")

        results = list(asr_stream_chunk(str(chunk), None, ""))
        state, msg_update, status = results[0]
        assert state["session"] is not None
        assert state["fed"] is True
        assert state["finalized"] is False          # 录音中：不提前完成
        assert len(state["session"].fed) == 1
        assert "识别中" in status["value"]
        assert "你好" in msg_update["value"]        # 实时部分结果填入输入框（边说边出字）

        # 第二块（内容不同）→ 追加并增量 feed，实时文本继续更新
        chunk.write_bytes(b"block-2")
        results2 = list(asr_stream_chunk(str(chunk), state, ""))
        st2, msg_update2, _ = results2[0]
        assert len(st2["session"].fed) == 2
        assert st2["sent_bytes"] == len(b"block-1") + len(b"block-2")
        assert "你好" in msg_update2["value"]

    def test_same_block_repeated_finalizes(self, monkeypatch, tmp_path):
        """相同块重复（录音已停止，value 稳定）→ finish 完成识别。"""
        from app import asr_stream_chunk
        from src.config import Settings

        s = Settings(_env_file=None)
        s.xfyun_app_id = "a"
        s.xfyun_api_key = "k"
        s.xfyun_api_secret = "s"
        s.asr_dict_dir = tmp_path
        monkeypatch.setattr("app.settings", s)
        monkeypatch.setattr("app.IflytekASR", _FakeASR)
        monkeypatch.setattr("app._to_pcm16k", lambda b, r: b)
        chunk = tmp_path / "chunk.wav"
        chunk.write_bytes(b"same-block")

        first = list(asr_stream_chunk(str(chunk), None, ""))
        st1 = first[0][0]
        assert st1["finalized"] is False
        fed_once = len(st1["session"].fed)

        # 相同块再次回调 → 不重复 feed，改为完成识别
        second = list(asr_stream_chunk(str(chunk), st1, ""))
        st2, msg_update, status2 = second[0]
        assert st2["finalized"] is True
        assert len(st2["session"].fed) == fed_once
        assert "你好" in msg_update["value"]

        # 已 finalized 后再回调：msg 不更新，voice_status 置空（避免空 update 到 Markdown 报错）
        third = list(asr_stream_chunk(str(chunk), st2, ""))
        _, msg3, status3 = third[0]
        assert "value" not in msg3
        assert status3.get("value") == ""


class TestAsrStreamStop:
    def test_stop_finishes_and_clears(self, monkeypatch):
        """停止录音 = 结束 → 总是 finish（幂等）并清空会话。"""
        from app import asr_stream_stop
        from src.config import Settings

        s = Settings(_env_file=None)
        monkeypatch.setattr("app.settings", s)
        state = {"session": type("S", (), {"finish": lambda self: "最终文本"})(), "finalized": False}
        results = list(asr_stream_stop(state, ""))
        new_state, msg_update, status = results[0]
        assert new_state is None
        assert "最终文本" in msg_update["value"]
        assert "已识别完成" in status["value"]

    def test_stop_without_session_noop(self):
        from app import asr_stream_stop
        results = list(asr_stream_stop(None, ""))
        assert results[0][0] is None

class _FakeTTS:
    """模拟 CosyVoiceTTS：每句返回固定 wav。"""

    def __init__(self, *a, **kw):
        pass

    def split_sentences(self, text, max_chars=1000):
        from src.tts import CosyVoiceTTS
        return CosyVoiceTTS.split_sentences(text, max_chars)

    def synthesize_sentence(self, text):
        return b"fake-wav"


class TestTtsAfterAnswer:
    def test_disabled_skips(self):
        from app import tts_after_answer
        results = list(tts_after_answer([], False))
        assert len(results) == 1

    def test_no_key_shows_message(self, monkeypatch):
        from app import tts_after_answer
        from src.config import Settings

        s = Settings(_env_file=None)
        s.dashscope_api_key = ""  # 强制空 Key（Settings 仍会读 os.environ，测试需确定性）
        monkeypatch.setattr("app.settings", s)
        results = list(tts_after_answer([{"role": "user", "content": "q"},
                                         {"role": "assistant", "content": "答"}], True))
        assert "未配置百炼 Key" in results[0][2]["value"]

    def test_no_voice_shows_message(self, monkeypatch):
        from app import tts_after_answer
        from src.config import Settings

        s = Settings(_env_file=None)
        s.dashscope_api_key = "dummy"
        monkeypatch.setattr("app.settings", s)
        results = list(tts_after_answer([{"role": "user", "content": "q"},
                                         {"role": "assistant", "content": "答"}], True))
        assert "未配置 TTS 音色" in results[0][2]["value"]

    def test_streams_sentences_and_replay(self, monkeypatch, tmp_path):
        """句子级流式：每句合成完立即 yield（gradio HLS 流式播放）。"""
        from app import tts_after_answer
        from src.config import Settings

        s = Settings(_env_file=None)
        s.dashscope_api_key = "dummy"
        s.tts_chunk_chars = 1000
        s.tts_model = "cosyvoice-v3-flash"
        s.tts_voice = "v"
        monkeypatch.setattr("app.settings", s)
        monkeypatch.setattr("app.CosyVoiceTTS", _FakeTTS)
        monkeypatch.setattr("app._write_replay_wav",
                           lambda chunks: tmp_path / "replay.wav")
        history = [{"role": "user", "content": "q"},
                   {"role": "assistant", "content": "第一句。第二句！\n\n---\n\n**[检索来源]**\n1. **司母戊鼎**"}]
        results = list(tts_after_answer(history, True))
        # 每句一个流式 yield + 最终重播 yield
        assert len(results) == 3
        assert results[0][0] == b"fake-wav"  # 句子 1 流式（streaming 输出需直接 bytes 值）
        assert results[1][0] == b"fake-wav"  # 句子 2 流式
        assert "已播报" in results[2][2]["value"]
        assert results[2][1]["value"] == str(tmp_path / "replay.wav")

    def test_extract_last_answer_strips_sources(self):
        from app import _extract_last_answer_text
        history = [{"role": "user", "content": "q"},
                   {"role": "assistant", "content": "正文内容\n\n---\n\n**[检索来源]**\n1. **x**"}]
        assert _extract_last_answer_text(history) == "正文内容"


class _FakeASRFinal(_FakeASR):
    """feed 后 is_final 为 True（模拟服务端 VAD 自动结束）。"""

    def is_final(self):
        return True


class _FakeASRError(_FakeASR):
    """feed 抛异常（模拟 Broken pipe / socket closed）。"""

    def feed(self, pcm):
        raise OSError("socket is already closed")


class TestAsrErrorHandling:
    def test_vad_auto_finalize(self, monkeypatch, tmp_path):
        """feed 后服务端已自动结束（is_final）→ 立即 finish 填入，不再等停止。"""
        from app import asr_stream_chunk
        from src.config import Settings

        s = Settings(_env_file=None)
        s.xfyun_app_id = "a"
        s.xfyun_api_key = "k"
        s.xfyun_api_secret = "s"
        s.asr_dict_dir = tmp_path
        monkeypatch.setattr("app.settings", s)
        monkeypatch.setattr("app.IflytekASR", _FakeASRFinal)
        monkeypatch.setattr("app._to_pcm16k", lambda b, r: b)
        chunk = tmp_path / "c.wav"
        chunk.write_bytes(b"data")

        results = list(asr_stream_chunk(str(chunk), None, ""))
        state, msg_update, status = results[0]
        assert state["finalized"] is True
        assert "已识别完成" in status["value"]
        assert "你好" in msg_update["value"]

    def test_error_clears_session(self, monkeypatch, tmp_path):
        """feed 异常（连接断开）→ 清理 session 并重置 state，避免循环报错。"""
        from app import asr_stream_chunk
        from src.config import Settings

        s = Settings(_env_file=None)
        s.xfyun_app_id = "a"
        s.xfyun_api_key = "k"
        s.xfyun_api_secret = "s"
        s.asr_dict_dir = tmp_path
        monkeypatch.setattr("app.settings", s)
        monkeypatch.setattr("app.IflytekASR", _FakeASRError)
        monkeypatch.setattr("app._to_pcm16k", lambda b, r: b)
        chunk = tmp_path / "c.wav"
        chunk.write_bytes(b"data")

        results = list(asr_stream_chunk(str(chunk), None, ""))
        state, _, status = results[0]
        assert state is None                     # 会话已清理，下次录音重建
        assert "识别出错" in status["value"]


class TestAsrGuards:
    def test_finalized_ignores_new_blocks(self, monkeypatch, tmp_path):
        """已识别完成后，即使前端继续发新块也不再 feed（防麦克风未停导致无限识别）。"""
        from app import asr_stream_chunk
        from src.config import Settings

        s = Settings(_env_file=None)
        s.xfyun_app_id = "a"
        s.xfyun_api_key = "k"
        s.xfyun_api_secret = "s"
        s.asr_dict_dir = tmp_path
        monkeypatch.setattr("app.settings", s)
        monkeypatch.setattr("app.IflytekASR", _FakeASR)
        monkeypatch.setattr("app._to_pcm16k", lambda b, r: b)
        chunk = tmp_path / "c.wav"
        chunk.write_bytes(b"first")
        st = list(asr_stream_chunk(str(chunk), None, ""))[0][0]
        assert st["finalized"] is False  # 新块：feed 中
        # 相同块重复 → 完成识别
        st = list(asr_stream_chunk(str(chunk), st, ""))[0][0]
        assert st["finalized"] is True
        fed = len(st["session"].fed)

        # finalized 后发新块（内容不同）→ 忽略，不 feed；voice_status 置空
        chunk.write_bytes(b"other-content")
        st2, msg2, status = list(asr_stream_chunk(str(chunk), st, ""))[0]
        assert len(st2["session"].fed) == fed
        assert "value" not in msg2
        assert status.get("value") == ""

    def test_timeout_auto_finalize(self, monkeypatch, tmp_path):
        """超过最长录音时长（asr_max_duration）→ 自动 finish 收尾（不依赖 stop 事件）。"""
        from app import asr_stream_chunk
        from src.config import Settings

        s = Settings(_env_file=None)
        s.xfyun_app_id = "a"
        s.xfyun_api_key = "k"
        s.xfyun_api_secret = "s"
        s.asr_dict_dir = tmp_path
        s.asr_max_duration = 0  # 立即超时
        monkeypatch.setattr("app.settings", s)

        class _S:
            def finish(self):
                return "超时文本"

        st = {"session": _S(), "started": 0, "finalized": False}
        chunk = tmp_path / "c.wav"
        chunk.write_bytes(b"data")
        results = list(asr_stream_chunk(str(chunk), st, ""))
        new_state, msg_update, status = results[0]
        assert new_state["finalized"] is True
        assert "超时文本" in msg_update["value"]
        assert "已识别完成" in status["value"]


class TestAsrSilenceAutoStop:
    """静音自动结束：连续静音块 → 自动 finish（讯飞 vad 不主动结束，实测）。"""

    def _settings(self, monkeypatch, tmp_path):
        from src.config import Settings
        s = Settings(_env_file=None)
        s.xfyun_app_id = "a"
        s.xfyun_api_key = "k"
        s.xfyun_api_secret = "s"
        s.asr_dict_dir = tmp_path
        s.asr_silence_threshold = 500
        s.asr_silence_blocks = 2  # 2 块静音即结束（测试用短值）
        monkeypatch.setattr("app.settings", s)
        return s

    def test_silence_blocks_auto_finalize(self, monkeypatch, tmp_path):
        from app import asr_stream_chunk
        self._settings(monkeypatch, tmp_path)
        monkeypatch.setattr("app.IflytekASR", _FakeASR)

        def fake_to_pcm16k(b, r):
            return b"\x00\x00" * 100  # 全零 = 静音

        monkeypatch.setattr("app._to_pcm16k", fake_to_pcm16k)
        chunk = tmp_path / "c.wav"
        chunk.write_bytes(b"silence")

        # 第一块静音：计数 1，仍识别中
        st = list(asr_stream_chunk(str(chunk), None, ""))[0][0]
        assert st["finalized"] is False
        assert st["silent_blocks"] == 1
        # 第二块静音（不同内容也可，仍静音）：计数 2 ≥ 2 → 自动结束
        chunk.write_bytes(b"silence2")
        st2, msg_update, status = list(asr_stream_chunk(str(chunk), st, ""))[0]
        assert st2["finalized"] is True
        assert "已识别完成" in status["value"]
        assert "你好" in msg_update["value"]

    def test_speech_resets_silence_counter(self, monkeypatch, tmp_path):
        from app import asr_stream_chunk
        self._settings(monkeypatch, tmp_path)
        monkeypatch.setattr("app.IflytekASR", _FakeASR)

        def fake_to_pcm16k(b, r):
            return b"\x00\x00" * 100  # 静音

        monkeypatch.setattr("app._to_pcm16k", fake_to_pcm16k)
        chunk = tmp_path / "c.wav"
        chunk.write_bytes(b"silence1")
        st = list(asr_stream_chunk(str(chunk), None, ""))[0][0]
        assert st["silent_blocks"] == 1

        # 非静音块（小端 0x4000 = 16384，RMS 远超阈值）→ 计数清零
        def speech_pcm(b, r):
            return bytes([0x00, 0x40]) * 100

        monkeypatch.setattr("app._to_pcm16k", speech_pcm)
        chunk.write_bytes(b"speech")
        st2, _, _ = list(asr_stream_chunk(str(chunk), st, ""))[0]
        assert st2["silent_blocks"] == 0
        assert st2["finalized"] is False


class TestTtsFfmpegGuard:
    def test_no_ffmpeg_shows_message(self, monkeypatch):
        """服务器缺少 ffmpeg（static-ffmpeg 未安装）→ 明确提示而非静默失败。"""
        from app import tts_after_answer
        from src.config import Settings

        s = Settings(_env_file=None)
        s.dashscope_api_key = "dummy"
        s.tts_voice = "v"
        monkeypatch.setattr("app.settings", s)
        monkeypatch.setattr("app.ensure_ffmpeg", lambda: False)
        results = list(tts_after_answer(
            [{"role": "user", "content": "q"},
             {"role": "assistant", "content": "回答"}], True))
        assert "ffmpeg" in results[0][2]["value"]


class TestWriteReplayWav:
    def _make_wav(self, seconds: float, freq: int = 440) -> bytes:
        import io, wave
        rate = 8000
        n = int(rate * seconds)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            w.writeframes(b"\x00\x00" * n)
        return buf.getvalue()

    def test_merges_wav_chunks_without_double_headers(self, monkeypatch, tmp_path):
        """拼接多个 wav 时去掉重复头（否则后段 wav 头被当 PCM，播放到一半损坏）。"""
        from app import _write_replay_wav
        from src.config import Settings

        s = Settings(_env_file=None)
        s.project_root = tmp_path
        monkeypatch.setattr("app.settings", s)
        # 用真实 CosyVoiceTTS.write_wav（静态方法，不碰 API）
        from src.tts import CosyVoiceTTS

        wavs = [self._make_wav(1.0), self._make_wav(0.5, freq=880)]
        path = _write_replay_wav(wavs)
        assert path.exists()
        data = path.read_bytes()
        assert data[:4] == b"RIFF"
        # 合并后应只有 1 个 wav 头（RIFF 只出现一次）
        assert data.count(b"RIFF") == 1
        # 总时长 = 1.5s @8k = 12000 frames
        import wave, io
        with wave.open(io.BytesIO(data), "rb") as w:
            assert w.getnframes() == 12000


class TestGradioHlsReusePatch:
    """gradio 6.22 前端 bug 修复：同一 Audio 组件多轮流式值只创建一次 hls（Se 标记不重置），
    第 2 轮起自动播报无声。patch 需幂等、匹配后 JS 含 Se instanceof Il。"""

    def test_patch_is_idempotent(self):
        """patch 应用后重复执行应跳过（幂等），并返回 True。"""
        from src.audio_bootstrap import patch_gradio_hls_reuse

        assert patch_gradio_hls_reuse() is True

    def test_patch_marks_js_with_instance_check(self):
        """patch 生效后，gradio StaticAudio 前端 JS 应包含 Se instanceof Il（Se 存 hls 实例）。"""
        import glob
        import os

        import gradio

        assets = os.path.join(os.path.dirname(gradio.__file__), "templates", "frontend", "assets")
        js = glob.glob(os.path.join(assets, "StaticAudio-*.js"))
        assert js, "找不到 gradio StaticAudio 前端 JS"
        src = open(js[0], encoding="utf-8").read()
        assert "Se instanceof Il" in src, "patch 未生效：JS 缺少 Se instanceof Il"
        assert "Se=!0}else" not in src, "patch 未生效：旧 Se=!0 仍存在"


class TestTTSAccumDoubleBuffer:
    """bug-121 攒字缓冲区 + 语音播报双缓冲区逻辑。"""

    def _make_wav(self, seconds, freq=440):
        import struct
        import math
        import io
        import wave

        rate = 16000
        n = int(rate * seconds)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
            frames = b"".join(
                struct.pack("<h", int(3000 * math.sin(2 * math.pi * freq * i / rate)))
                for i in range(n)
            )
            w.writeframes(frames)
        return buf.getvalue()

    def test_take_sentence_prefers_boundary_else_hard_cut(self):
        from app import _take_sentence

        # 前 20 字内出现句尾标点 → 在标点处切
        text = "前面内容凑凑凑凑凑。第二句还有更多的内容在这里面"
        seg, rest = _take_sentence(text, 20)
        assert seg.endswith("。")
        assert "第二句" in rest
        # 无标点 → 硬切 20 字
        text2 = "啊" * 30
        seg2, rest2 = _take_sentence(text2, 20)
        assert len(seg2) == 20
        assert rest2 == "啊" * 10
        # 空输入
        assert _take_sentence("  ", 20) == ("", "")
        # 不足阈值 → 整段返回
        assert _take_sentence("短句。", 20) == ("短句。", "")

    # audit-TTS：_maybe_play_batch（分段合成的攒批门）已随单会话流式改造移除，
    # 首播/停顿语义由 tests/test_tts_broadcast.py 覆盖（首播批小、即产即播）。

    def test_merge_wavs_removes_duplicate_headers(self):
        import io
        import wave

        from app import _merge_wavs

        merged = _merge_wavs([self._make_wav(0.5), self._make_wav(0.5, freq=880)])
        assert merged.count(b"RIFF") == 1
        with wave.open(io.BytesIO(merged), "rb") as w:
            assert w.getnframes() == 16000  # 1.0s @16k

    def test_respond_accumulates_and_replays(self, monkeypatch, tmp_path):
        """respond 集成：mock answer_question 流式输出 + mock 流式 TTS 会话，
        验证增量喂文本合成、音频批发布、重播文件生成（audit-TTS 流式契约）。"""
        import threading

        import app as app_mod
        from src.config import Settings

        s = Settings(_env_file=None)
        s.project_root = tmp_path
        monkeypatch.setattr(app_mod, "settings", s)

        base = "这是第一段回答内容用于测试攒字功能。"
        calls = []

        class FakeTTS:
            def start_stream(self, on_audio):
                class H:
                    def __init__(self):
                        self.done = threading.Event()
                        self.error = None
                        self.first_audio_at = None
                        self.cancelled = False

                    def feed(self, text):
                        calls.append(text)
                        on_audio(b"\x01\x00" * 4800)  # 0.1s PCM(24k)

                    def finish(self):
                        self.done.set()

                    def cancel(self):
                        self.cancelled = True
                        self.done.set()

                return H()

        monkeypatch.setattr(app_mod, "_init_tts", lambda: FakeTTS())

        def fake_answer(q, h, stream, project):
            for i in (1, 2, 3):
                yield h + [("user", q), ("assistant", base * i)], "[]", base * i

        monkeypatch.setattr(app_mod, "answer_question", fake_answer)

        results = list(app_mod.respond("q", [], True, "museum", True))
        # 增量喂文本触发合成（base 15 字，3 段增量 → 至少 2 次喂入）
        assert len(calls) >= 2
        # 播报批输出（bytes）
        batches = [r[3] for r in results if isinstance(r[3], bytes)]
        assert batches, "应至少有一批播报音频"
        # 重播文件更新
        replay = [r[4] for r in results if isinstance(r[4], dict) and r[4].get("value")]
        assert replay
        assert Path(replay[-1]["value"]).exists()

    def test_respond_tts_failure_does_not_break_answer(self, monkeypatch, tmp_path):
        """TTS 流式会话持续异常（重建次数用尽）时回答流不受影响（事件不中断）。"""
        import threading

        import app as app_mod
        from src.config import Settings

        s = Settings(_env_file=None)
        s.project_root = tmp_path
        monkeypatch.setattr(app_mod, "settings", s)

        class BoomTTS:
            def start_stream(self, on_audio):
                class H:
                    def __init__(self):
                        self.done = threading.Event()
                        self.error = None
                        self.first_audio_at = None

                    def feed(self, text):
                        raise RuntimeError("合成失败")

                    def finish(self):
                        self.done.set()

                    def cancel(self):
                        self.done.set()

                return H()

        monkeypatch.setattr(app_mod, "_init_tts", lambda: BoomTTS())

        def fake_answer(q, h, stream, project):
            yield h + [("user", q), ("assistant", "回答一。")], "[]", "回答一。"
            yield h + [("user", q), ("assistant", "回答一。回答二。")], "[]", "回答一。回答二。"

        monkeypatch.setattr(app_mod, "answer_question", fake_answer)

        results = list(app_mod.respond("q", [], True, "museum", True))
        # 回答输出仍正常（chatbot 更新）
        chatbot_updates = [r[1] for r in results if isinstance(r[1], list)]
        assert chatbot_updates
        assert "回答一" in chatbot_updates[-1][-1][1]

    # audit-TTS：原 test_respond_first_play_at_5_llm_chunks（5 chunk 首播门）随
    # 单会话流式改造废弃；首播及时性由 test_tts_broadcast.py 覆盖（+E2E 验收 ≤1s）。

    def test_take_sentence_splits_on_comma_and_period(self):
        from app import _take_sentence

        # 前 20 字内有逗号（15 字处）→ 逗号处切，段含逗号
        t = "这是前面前面前面前面前面的内容，然后这里是后半句更多内容"
        seg, rest = _take_sentence(t, 20)
        assert seg.endswith("，")
        assert "后半句" in rest
        # 前 20 字无标点 → 向后找到逗号
        t2 = "没有标点的一段长文本内容一直没有标点直到这里，出现逗号了后面继续"
        seg2, rest2 = _take_sentence(t2, 20)
        assert seg2.endswith("，")
        assert "出现逗号" in rest2
        # 句号切分
        t3 = "第一句话的内容结束。第二句话的内容开始更多"
        seg3, rest3 = _take_sentence(t3, 20)
        assert seg3.endswith("。")
        assert "第二句" in rest3

    def test_take_sentence_hard_cut_swallows_trailing_punct(self):
        """硬切时切点后的标点被吞掉，剩余不以孤立标点开头（避免下次合成失败丢字）。"""
        from app import _take_sentence

        t = "啊" * 22 + "，这里开头的逗号应该被吞掉"
        seg, rest = _take_sentence(t, 20)
        assert rest and rest[0] != "，"
        assert "，这里" not in rest

    def test_respond_retries_failed_synthesis(self, monkeypatch, tmp_path):
        """首个流式会话喂入即失败（临时异常）→ 自动重建会话恢复，播报批仍正常
        （audit-TTS：原“分段失败结尾合并重试”语义由会话重建承接）。"""
        import threading

        import app as app_mod
        from src.config import Settings

        s = Settings(_env_file=None)
        s.project_root = tmp_path
        monkeypatch.setattr(app_mod, "settings", s)

        class FakeTTS:
            def __init__(self):
                self.n = 0

            def start_stream(self, on_audio):
                self.n += 1
                fail = self.n == 1  # 首个会话喂入即抛（模拟临时故障）

                class H:
                    def __init__(self):
                        self.done = threading.Event()
                        self.error = None
                        self.first_audio_at = None

                    def feed(self, text):
                        if fail:
                            raise RuntimeError("临时失败")
                        on_audio(b"\x01\x00" * 4800)  # 0.1s PCM(24k)

                    def finish(self):
                        self.done.set()

                    def cancel(self):
                        self.done.set()

                return H()

        tts = FakeTTS()
        monkeypatch.setattr(app_mod, "_init_tts", lambda: tts)

        base = "这是回答内容的流式片段。"
        def fake_answer(q, h, stream, project):
            for i in range(1, 7):
                yield h + [{"role": "user", "content": q},
                           {"role": "assistant", "content": base * i}], "[]", base * i

        monkeypatch.setattr(app_mod, "answer_question", fake_answer)

        results = list(app_mod.respond("q", [], True, "museum", True))
        batches = [r[3] for r in results if isinstance(r[3], bytes)]
        assert tts.n >= 2, "临时失败后应重建会话"
        assert batches, "重建会话后应仍有播报批"
        # 回答输出不因合成失败中断
        chatbot_updates = [r[1] for r in results if isinstance(r[1], list)]
        assert chatbot_updates
