# web-046：播报文本二次清洗（薄层）——内核 clean_text_for_tts 之后的保守补充。
# 实测内核残余：流式分段切断的未配对 **、有序列表前缀「1. 」「2、」、条目分隔「 - 」。
# 硬约束：不误伤小数/日期/区间/百分比/货币等必要符号，保证播报连续自然。
# 注：内核会对无句末标点的分段补「。」（播报断句需要），期望值含此行为。
import pytest

from kiosk_server.tts_clean import clean_for_broadcast


class TestBroadcastCleanStrip:
    @pytest.mark.parametrize("raw, want", [
        ("**意大利风格沙发", "意大利风格沙发。"),       # 未配对 **（段首）
        ("沙发展馆（精品）**", "沙发展馆（精品）。"),    # 未配对 **（段尾）
        ("**沙发馆（精品）**", "沙发馆（精品）。"),      # 成对（内核已处理，幂等）
        ("*斜体*", "斜体。"),                          # 未配对单 *（贴中文）
        ("1. 沙发生活馆", "沙发生活馆。"),              # 有序列表前缀「1. 」
        ("2、沙发馆", "沙发馆。"),                      # 「2、」
        ("12. 推荐理由", "推荐理由。"),                  # 两位序号
        ("沙发馆 - 位置：A区", "沙发馆，位置：A区。"),  # 条目分隔横杠 → 逗号
        ("**", ""),                                   # 清洗后仅剩标点 → 判空（调用方跳过）
        ("---", ""),                                  # 分隔线（内核清除）→ 判空
    ])
    def test_strip(self, raw, want):
        assert clean_for_broadcast(raw) == want

    def test_real_answer_segment(self):
        raw = "1. **沙发生活馆（精品）** - **位置**：A区 2.2号馆 - **推荐理由**：汇聚国内外知名品牌"
        assert clean_for_broadcast(raw) == \
            "沙发生活馆（精品），位置：A区 2.2号馆，推荐理由：汇聚国内外知名品牌。"


class TestBroadcastCleanPreserve:
    """不误伤必要符号（句末已带标点 → 内核不补句号，整体应原样保留）。"""

    @pytest.mark.parametrize("s", [
        "A区 2.2号馆。",            # 小数点（点后跟数字）
        "全长 1.5小时。",           # 小数在段首也不误删
        "12.5元特价。",             # 两位小数
        "2025年3月18日—21日。",     # 日期连接号
        "门票19.9元。",             # 价格
        "50%折扣。",                # 百分比
        "价格¥199元。",             # 货币
        "面积 3*5=15 平方米。",      # 乘法式（星号两侧非中文）保留
        "第2期展览开幕。",           # 期号
    ])
    def test_preserve(self, s):
        assert clean_for_broadcast(s) == s

    def test_kernel_behaviors_kept(self):
        assert clean_for_broadcast("3~5个") == "3 到 5 个。"      # 内核波浪号转「到」
        assert clean_for_broadcast("你好😀") == "你好。"           # 内核去 emoji+补句号
        assert clean_for_broadcast("## 标题") == "标题"           # 内核标题行不补句号
