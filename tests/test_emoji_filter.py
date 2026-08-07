"""
输出答案 emoji 过滤（bug-114）单元测试

覆盖：
  - src/utils.strip_emoji：emoji/图标移除、中文标点/字母/数字/普通符号保留
  - BailianLLM.chat 非流式过滤（新生成 + 缓存命中旧内容）
  - BailianLLM.chat_stream 流式逐 token 过滤（含 ZWJ 组合）
  - app.format_answer 输出不再含 emoji/图标
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.utils import strip_emoji, EMOJI_PATTERN


# ========== strip_emoji 单元测试 ==========

class TestStripEmoji:
    @pytest.mark.parametrize("text,expected", [
        ("你好！😊", "你好！"),
        ("推荐🌟3个必看", "推荐3个必看"),
        ("❤️ 喜欢这个", " 喜欢这个"),
        ("✅ 已完成", " 已完成"),
        ("📚 检索来源", " 检索来源"),
        ("👨\u200d👩\u200d👧\u200d👦 家庭", " 家庭"),  # ZWJ 组合
        ("⏰ 提醒", " 提醒"),
        ("⭐ 好评", " 好评"),
        ("〰️ 波浪", " 波浪"),
    ])
    def test_removes_emoji(self, text, expected):
        assert strip_emoji(text) == expected

    @pytest.mark.parametrize("text", [
        "你好，你是谁？",
        "Hello World 123",
        "© 2024 保留",
        "→ 箭头保留 →",
        "CJK标点，。！？；：（）——…·",
        "A→B 和 C－D",
        "1. 第一项\n2. 第二项",
        "纯文本",
        "",
        None,
    ])
    def test_keeps_normal_chars(self, text):
        assert strip_emoji(text) == text

    def test_all_common_emoji_ranges_covered(self):
        """各类常见 emoji 均被覆盖"""
        samples = "😀😁😂🤣😊😍😘😜🤔🤗" \
                  "🚀🚗🏠💡🔥⭐✨✅❌❤️💔" \
                  "👍👎👏🙏💪" \
                  "🎉🎂🎁🎈🎊" \
                  "📚📖📝✏️" \
                  "🏛️🏫🏰" \
                  "⚽🏀🎾🏈" \
                  "☀️🌙⭐🌈☁️" \
                  "⏰⏳⌛" \
                  "➡️⬅️⬆️⬇️↗️" \
                  "▫️▪️◾◽" \
                  "♠️♣️♥️♦️" \
                  "☕🍵🍺🍻" \
                  "👨\u200d👩\u200d👧\u200d👦" \
                  "🇨🇳🇺🇸🇯🇵"
        assert strip_emoji(samples) == ""

    def test_pattern_has_no_plain_text_collision(self):
        """正则不应匹配纯 ASCII/CJK 文本"""
        for ch in "abcXYZ019，。！？；：（）——…·你好Hello":
            assert not EMOJI_PATTERN.search(ch), f"误匹配: {ch}"


# ========== BailianLLM 过滤测试 ==========

class _MockResp:
    def __init__(self, content, status_code=200):
        self.status_code = status_code
        self.message = "ok"
        msg = MagicMock()
        msg.content = content
        self.output = MagicMock(choices=[MagicMock(message=msg)])


class TestLLMEmojiFilter:
    @patch("src.llm.Generation.call")
    def test_chat_filters_emoji(self, mock_call, monkeypatch):
        from src.llm import BailianLLM
        mock_call.return_value = _MockResp("你好！😊 推荐🌟3个文物")
        llm = BailianLLM(max_retries=1, use_cache=False)
        answer = llm.chat([{"role": "user", "content": "推荐几个"}],
                          system_prompt="你是助手。")
        assert answer == "你好！ 推荐3个文物"
        assert "😊" not in answer
        assert "🌟" not in answer

    @patch("src.llm.Generation.call")
    def test_chat_cache_hit_filters_old_emoji(self, mock_call):
        """升级前缓存的内容含 emoji 仍会被过滤（缓存命中路径）"""
        from src.llm import BailianLLM
        from src.cache import llm_cache
        llm = BailianLLM(max_retries=1)
        # 手动向 llm_cache 写入含 emoji 的旧内容
        messages = [{"role": "user", "content": "旧问题"}]
        llm_cache.set_with_key(
            "旧回答😊含表情", "chat", llm.model,
            llm._build_messages(messages, None), llm.temperature,
            llm.max_tokens, llm.top_p, {"enable_search": False},
        )
        answer = llm.chat(messages, system_prompt=None)
        assert answer == "旧回答含表情"
        assert "😊" not in answer
        mock_call.assert_not_called()  # 命中缓存，未调 API

    @patch("src.llm.Generation.call")
    def test_chat_stream_filters_per_token(self, mock_call):
        from src.llm import BailianLLM
        tokens = ["你好", "！😊", "推荐🌟", "3个", "文物"]
        mock_call.return_value = [_MockResp(t) for t in tokens]
        llm = BailianLLM(max_retries=1)
        output = "".join(llm.chat_stream(
            [{"role": "user", "content": "推荐几个"}],
            system_prompt="你是助手。",
        ))
        assert output == "你好！推荐3个文物"
        assert "😊" not in output
        assert "🌟" not in output


# ========== app.format_answer 输出无 emoji ==========

class TestFormatAnswerNoEmoji:
    def test_format_answer_no_emoji(self):
        """format_answer 不新增任何 emoji（过滤职责在 LLM 层，此处验证拼接层无图标）"""
        from app import format_answer
        chunks = [
            {"artifact_name": "文物A", "score": 0.95, "chunk_type": "summary"},
            {"artifact_name": "文物B", "score": 0.50, "chunk_type": "detail"},
        ]
        result = format_answer("回答内容", chunks, {"total": 1234})
        from src.utils import strip_emoji
        assert strip_emoji(result) == result, "format_answer 输出不应含 emoji"
        assert "[检索来源]" in result
        assert "[高]" in result
        assert "[中]" in result
        assert "响应时间: 1234ms" in result

    def test_format_answer_rrf_scale_no_emoji(self):
        """RRF 融合分（0~0.1 量级）路径同样无 emoji（[高]/[中]/[低] 全覆盖）"""
        from app import format_answer
        chunks = [
            {"artifact_name": "A", "score": 0.03, "chunk_type": "summary"},
            {"artifact_name": "B", "score": 0.02, "chunk_type": "detail"},
            {"artifact_name": "C", "score": 0.01, "chunk_type": "detail"},
            {"artifact_name": "D", "score": 0.005, "chunk_type": "detail"},
        ]
        result = format_answer("回答", chunks)
        assert "[高]" in result
        assert "[中]" in result
        assert "[低]" in result
        from src.utils import strip_emoji
        assert strip_emoji(result) == result