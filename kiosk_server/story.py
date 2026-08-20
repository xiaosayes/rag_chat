"""AI 故事绘本（web-050 起）：意图/脚本/插图/缓存/编排。冻结内核零改动。"""
from __future__ import annotations

import re

# web-051：薄层拦截（宁漏勿抢）——前缀客套词 + 讲/说 + (一)?(个|段)? + 主题 + 的? + 故事|绘本
_PREFIX_RE = re.compile(r"^(?:请|请你|给我|给我们|你来|帮我|我想听|我要听|我想让你)+")
_STORY_RE = re.compile(r"(?:讲|说)(?:一)?(?:个|段)?(.+?)(?:的)?(?:故事|绘本)[吧吗呢啊呀！!。.~]*$")
# 「我想听/我要听 X 的故事」：无讲/说动词，但听故事意图明确——锚定整句单判，
# 不放宽讲/说分支（防「你听过这个故事吗」类误判，宁漏勿抢）。
_WANT_LISTEN_RE = re.compile(r"^(?:我想听|我要听)(.+?)(?:的)?(?:故事|绘本)[吧吗呢啊呀！!。.~]*$")
_THEME_STRIP = " 的一了个段下，,。.!！?"


def parse_story_intent(text: str) -> str | None:
    """命中返回故事主题（2~20 字），否则 None（含「讲个故事」无主题）。"""
    t = (text or "").strip()
    if not t or len(t) > 50:
        return None
    m = _WANT_LISTEN_RE.match(t)
    if not m:
        t = _PREFIX_RE.sub("", t)
        m = _STORY_RE.search(t)
    if not m:
        return None
    theme = m.group(1).strip(_THEME_STRIP)
    if len(theme) < 2 or len(theme) > 20:
        return None
    return theme
