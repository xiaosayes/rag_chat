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
        assert "已识别完成" in status2["value"]
        assert "你好" in msg_update["value"]


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
        assert results[0][0]["value"] == b"fake-wav"  # 句子 1 流式
        assert results[1][0]["value"] == b"fake-wav"  # 句子 2 流式
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
