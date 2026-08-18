"""播报文本二次清洗（web-046）：内核 clean_text_for_tts 之后的薄层保守补充。

背景（实测内核输出）：流式句边界切段会把成对 Markdown 标记切成未配对
（'**意大利' / '沙发**'），内核仅处理成对标记 → 残余 ** 进 TTS；
有序列表前缀（'1. ' '2、'）内核不处理 → 播报「一点/二点」生硬；
条目分隔「 - 」直接进合成。本模块只做保守补充，硬约束：不误伤小数
（2.2号馆/1.5小时，点后跟数字则不判为列表）、日期区间、百分比、货币、
乘法式（3*5，星号两侧非中文则保留）等必要符号。
"""
from __future__ import annotations

import re

from src.utils import clean_text_for_tts

# 有序列表前缀：段首 1~3 位数字 + 「.」或「、」；(?!\d) 保护小数（2.2 / 1.5 / 12.5）
_ORDERED_PREFIX = re.compile(r"^\s*\d{1,3}\s*[.、]\s*(?!\d)")
# 条目分隔横杠（两侧带空格）→ 逗号；不误伤 1.5-2（无空格）与 3月18日—21日（长横）
_SPACED_DASH = re.compile(r"\s+-\s+")
# 未配对单 *：仅剥贴中文侧的（*斜体 / 斜体*）；乘法式 3*5（两侧数字）保留
_STAR_CJK_LEFT = re.compile(r"\*(?=[\u4e00-\u9fff])")
_STAR_CJK_RIGHT = re.compile(r"(?<=[\u4e00-\u9fff])\*")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
# 中文标点前不留空格（剥标记后的残余空白）
_SPACE_BEFORE_CJK_PUNCT = re.compile(r"[ \t]+([，。！？；：、）】》…—])")


def clean_for_broadcast(text: str) -> str:
    """内核 clean_text_for_tts + 薄层补充清洗（幂等；空输入返回空串）。"""
    if not text:
        return ""
    t = clean_text_for_tts(text)
    t = t.replace("**", "")                    # 未配对 ** 残余（内核只处理成对的）
    t = _STAR_CJK_LEFT.sub("", t)              # 未配对单 *（贴中文）
    t = _STAR_CJK_RIGHT.sub("", t)
    t = _ORDERED_PREFIX.sub("", t)             # 有序列表前缀「1. 」「2、」
    t = _SPACED_DASH.sub("，", t)              # 条目分隔横杠 → 逗号（播报自然停顿）
    t = _MULTI_SPACE.sub(" ", t)
    t = _SPACE_BEFORE_CJK_PUNCT.sub(r"\1", t)
    t = t.strip()
    if not re.search(r"[\w\u4e00-\u9fff]", t):
        return ""      # 仅剩标点/空白（如 '**' 清洗后剩句号）→ 判空，调用方跳过
    return t
