"""
答案文本清洗（TTS + 字幕展示）单元测试（bug-115）

覆盖 src.utils.clean_text_for_tts：
  - 用户验收示例（原样断言）
  - Markdown：标题/粗体/斜体/删除线/引用/代码块/行内代码/分隔线/链接/图片/表格
  - HTML：标签去属性、块级转段落、script/style 删除
  - LaTeX：简单公式转口语、复杂公式删除、货币 $ 不误判
  - 特殊符号：零宽/控制字符/制表符/emoji/波浪号
  - 保留字符：中文/英文标点、数字、%、货币、°C、版本号、商标符号
  - 规范化：空格压缩、段落间最多一个空行、列表序号补空格、句末标点、标题行不加标点
  - 边界：空串/None/纯空白/纯 ASCII
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.utils import clean_text_for_tts


# ========== 用户验收示例 ==========

class TestUserExample:
    def test_example(self):
        raw = (
            '# 退款流程\n\n'
            '1. 在**订单页**点击"申请退款"\n'
            '2.金额将在3~5个工作日退回。温馨提示：请勿重复提交'
        )
        expected = (
            '退款流程\n'
            '1. 在订单页点击"申请退款"。\n'
            '2. 金额将在 3 到 5 个工作日退回。温馨提示：请勿重复提交。'
        )
        assert clean_text_for_tts(raw) == expected


# ========== Markdown ==========

class TestMarkdown:
    def test_headers(self):
        assert clean_text_for_tts("# 青铜器简介\n正文内容。") == "青铜器简介\n正文内容。"

    def test_headers_multilevel(self):
        assert clean_text_for_tts("### 三级标题\n内容。") == "三级标题\n内容。"

    def test_bold(self):
        assert clean_text_for_tts("这是**重点**内容。") == "这是重点内容。"

    def test_italic(self):
        assert clean_text_for_tts("这是*斜体*内容。") == "这是斜体内容。"

    def test_strikethrough(self):
        assert clean_text_for_tts("这是~~划掉~~的文字。") == "这是划掉的文字。"

    def test_underscore_emphasis(self):
        assert clean_text_for_tts("这是_强调_内容。") == "这是强调内容。"

    def test_underscore_snake_case_kept(self):
        # 下划线强调不误伤标识符
        assert clean_text_for_tts("配置项 model_name 有效。") == "配置项 model_name 有效。"

    def test_blockquote(self):
        # 引用内容也是完整句子，补句号
        assert clean_text_for_tts("> 引用的话\n正文。") == "引用的话。\n正文。"

    def test_code_block_deleted(self):
        raw = "示例：\n```python\nprint(1)\n```\n结束。"
        # 代码块整块删除，遗留空行合并为最多一个空行
        assert clean_text_for_tts(raw) == "示例。\n\n结束。"

    def test_inline_code_command_deleted(self):
        assert clean_text_for_tts("请运行 `pip install openpyxl` 安装。") == "请运行 安装。"

    def test_inline_code_path_deleted(self):
        assert clean_text_for_tts("路径 `C:\\Users\\admin\\file.txt` 不存在。") == "路径 不存在。"

    def test_inline_code_normal_word_kept(self):
        assert clean_text_for_tts("参数 `True` 时生效。") == "参数 True 时生效。"

    def test_inline_code_version_kept(self):
        assert clean_text_for_tts("版本 `v3.2` 支持。") == "版本 v3.2 支持。"

    def test_horizontal_rule(self):
        assert clean_text_for_tts("上文。\n\n---\n\n下文。") == "上文。\n\n下文。"

    def test_link(self):
        assert clean_text_for_tts("查看[官方文档](https://example.com)。") == "查看官方文档。"

    def test_image(self):
        assert clean_text_for_tts("![示意图](assets/pic.png) 如下。") == "示意图 如下。"

    def test_table(self):
        raw = "| 名称 | 朝代 |\n| --- | --- |\n| 司母戊鼎 | 商代 |"
        assert clean_text_for_tts(raw) == "名称，朝代。\n司母戊鼎，商代。"

    def test_unordered_list(self):
        assert clean_text_for_tts("- 苹果\n- 香蕉") == "苹果。\n香蕉。"


# ========== HTML ==========

class TestHTML:
    def test_tags_removed(self):
        assert clean_text_for_tts("<p>段落一</p><p>段落二</p>") == "段落一。\n\n段落二。"

    def test_attributes_removed(self):
        raw = '<a href="https://x.com" target="_blank">链接文字</a>'
        assert clean_text_for_tts(raw) == "链接文字。"

    def test_br_becomes_newline(self):
        assert clean_text_for_tts("第一行<br>第二行") == "第一行。\n第二行。"

    def test_script_style_deleted(self):
        raw = "<style>body{}</style>正文<script>alert(1)</script>。"
        assert clean_text_for_tts(raw) == "正文。"


# ========== LaTeX ==========

class TestLatex:
    def test_square(self):
        assert clean_text_for_tts("速度 $x^2$ 表示。") == "速度 x 的平方 表示。"

    def test_cube(self):
        assert clean_text_for_tts("体积是 $x^3$。") == "体积是 x 的立方。"

    def test_power_n(self):
        assert clean_text_for_tts("记为 $x^n$。") == "记为 x 的 n 次方。"

    def test_subscript(self):
        assert clean_text_for_tts("元素 $x_1$ 表示。") == "元素 x 下标 1 表示。"

    def test_fraction(self):
        assert clean_text_for_tts("概率为 $\\frac{1}{2}$。") == "概率为 2 分之 1。"

    def test_simple_arithmetic(self):
        assert clean_text_for_tts("表达式 $a+b$ 成立。") == "表达式 a 加 b 成立。"

    def test_complex_formula_deleted(self):
        assert clean_text_for_tts("公式 $E=\\int_0^1 f(x)dx$ 无意义。") == "公式 无意义。"

    def test_paren_latex(self):
        assert clean_text_for_tts("公式 \\(x^2\\) 表示。") == "公式 x 的平方 表示。"

    def test_currency_dollar_not_latex(self):
        # $ 后为数字 → 货币，保留
        assert clean_text_for_tts("价格 $5 和 $10。") == "价格 $5 和 $10。"

    def test_currency_symbol_kept(self):
        assert clean_text_for_tts("成本 ¥100，美元 $50。") == "成本 ¥100，美元 $50。"


# ========== 特殊符号 ==========

class TestSpecialChars:
    def test_zero_width(self):
        assert clean_text_for_tts("文\u200b字") == "文字。"

    def test_control_char(self):
        assert clean_text_for_tts("a\x00b") == "ab。"

    def test_tab(self):
        assert clean_text_for_tts("a\tb。") == "a b。"

    def test_emoji(self):
        assert clean_text_for_tts("你好😊") == "你好。"

    def test_fullwidth_tilde_range(self):
        assert clean_text_for_tts("活动 3～5 天。") == "活动 3 到 5 天。"

    def test_date_range(self):
        assert clean_text_for_tts("展期 2024-08-06~2024-08-07 开放。") == "展期 2024-08-06 到 2024-08-07 开放。"


# ========== 保留字符 ==========

class TestKeptChars:
    def test_chinese_punctuation(self):
        text = "中文标点，。！？；：「」『』（）——……"
        assert clean_text_for_tts(text) == text

    def test_ascii_punctuation(self):
        text = "English punctuation, . ! ? ; : ( ) - + @"
        assert clean_text_for_tts(text) == text + "。"

    def test_percent_currency_temp_version(self):
        text = "完成率 100%，价格 ¥5.5，温度 25°C，版本 v3.2。"
        assert clean_text_for_tts(text) == text

    def test_trademark_symbol(self):
        assert clean_text_for_tts("品牌 ABC™ 与 ABC® 均保留。") == "品牌 ABC™ 与 ABC® 均保留。"

    def test_decimal_not_sentence_end(self):
        # 小数 3.5 不被当作句末标点
        assert clean_text_for_tts("价格 3.5 元") == "价格 3.5 元。"


# ========== 规范化 ==========

class TestNormalization:
    def test_collapse_spaces(self):
        assert clean_text_for_tts("a  b   c") == "a b c。"

    def test_paragraph_blank_line_capped(self):
        assert clean_text_for_tts("第一段。\n\n\n第二段。") == "第一段。\n\n第二段。"

    def test_numbered_list_spacing(self):
        assert clean_text_for_tts("1. 第一\n2.第二") == "1. 第一。\n2. 第二。"

    def test_sentence_end_period_added(self):
        assert clean_text_for_tts("没有标点") == "没有标点。"

    def test_sentence_end_question_kept(self):
        assert clean_text_for_tts("你确定吗？") == "你确定吗？"

    def test_heading_no_period(self):
        assert clean_text_for_tts("# 标题") == "标题"

    def test_heading_blank_line_removed(self):
        assert clean_text_for_tts("# 标题\n\n正文。") == "标题\n正文。"

    def test_colon_stripped(self):
        assert clean_text_for_tts("注意：\n正文。") == "注意。\n正文。"

    def test_trailing_comma_stripped(self):
        assert clean_text_for_tts("先做A，\n再做B。") == "先做A。\n再做B。"


# ========== 边界 ==========

class TestEdgeCases:
    def test_empty(self):
        assert clean_text_for_tts("") == ""

    def test_none(self):
        assert clean_text_for_tts(None) == ""

    def test_whitespace_only(self):
        assert clean_text_for_tts("   \n  ") == ""

    def test_clean_text_unchanged(self):
        text = "这是干净的文本。"
        assert clean_text_for_tts(text) == text

    def test_ascii_only(self):
        assert clean_text_for_tts("Hello world") == "Hello world。"

# ========== 业务接线（app.answer_question 展示前清洗，bug-115） ==========

class TestAppIntegration:
    """验证 clean_text_for_tts 已接入 Web UI 问答链路（answer_question 展示层）"""

    def test_non_stream_answer_cleaned(self):
        """非流式：pipeline 返回含 Markdown 的答案，history 中展示文本应为清洗后纯文本"""
        from unittest.mock import patch, MagicMock
        from app import answer_question, _GRADIO_MAJOR

        fake_pipe = MagicMock()
        fake_pipe._is_built = True
        fake_pipe.query.return_value = {
            "answer": "# 推荐\n**司母戊鼎**是国宝，价格 3~5 万元。",
            "retrieved_chunks": [],
            "timing": {"total": 100},
        }
        with patch("app.init_pipeline", return_value=fake_pipe):
            results = list(answer_question("推荐文物", [], use_stream=False, project_id="museum"))
        history = results[-1][0]
        content = history[-1]["content"] if _GRADIO_MAJOR >= 6 else history[-1][1]
        assert "**司母戊鼎**" not in content, "粗体标记应被清洗"
        assert "# 推荐" not in content, "标题标记应被清洗"
        assert "3 到 5" in content, "数字区间应转为'到'"
        assert "司母戊鼎是国宝" in content, "正文内容应保留"

    def test_stream_answer_cleaned(self):
        """流式：逐 token 累积的答案在最终展示时被清洗"""
        from unittest.mock import patch, MagicMock
        from app import answer_question, _GRADIO_MAJOR

        fake_pipe = MagicMock()
        fake_pipe._is_built = True

        def fake_stream(question, top_k, rerank, conversation_history):
            yield {"type": "meta", "from_kb": True, "query_type": "factual", "chunks": [], "timing": {}}
            yield "**司母戊鼎**是"
            yield "国宝，价格 3~5 万元。"

        fake_pipe.query_stream.side_effect = fake_stream
        with patch("app.init_pipeline", return_value=fake_pipe):
            results = list(answer_question("推荐文物", [], use_stream=True, project_id="museum"))
        history = results[-1][0]
        content = history[-1]["content"] if _GRADIO_MAJOR >= 6 else history[-1][1]
        assert "**司母戊鼎**" not in content, "粗体标记应被清洗"
        assert "3 到 5" in content, "数字区间应转为'到'"
        assert "司母戊鼎是国宝" in content, "正文内容应保留"

    def test_retrieval_source_markers_kept(self):
        """检索来源的 **名称** 加粗结构由 format_answer 保留，不被清洗"""
        from unittest.mock import patch, MagicMock
        from app import answer_question, _GRADIO_MAJOR

        fake_pipe = MagicMock()
        fake_pipe._is_built = True
        fake_pipe.query.return_value = {
            "answer": "**司母戊鼎**是国宝。",
            "retrieved_chunks": [{"artifact_name": "司母戊鼎", "score": 0.8, "chunk_type": "detail"}],
            "timing": {},
        }
        with patch("app.init_pipeline", return_value=fake_pipe):
            results = list(answer_question("推荐文物", [], use_stream=False, project_id="museum"))
        history = results[-1][0]
        content = history[-1]["content"] if _GRADIO_MAJOR >= 6 else history[-1][1]
        assert "**[检索来源]**" in content, "检索来源标题应保留"
        assert "**司母戊鼎**" in content, "检索来源的加粗名称应保留（UI 结构）"
        # 答案正文本体的加粗已被清洗，仅检索来源部分保留
        assert content.count("**司母戊鼎**") == 1
