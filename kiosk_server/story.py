"""AI 故事绘本（web-050 起）：意图/脚本/插图/缓存/编排。冻结内核零改动。"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

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


# ==================== web-052：分镜脚本（qwen-plus，固定云端） ====================

SCRIPT_SYSTEM_PROMPT = (
    "你是湘小图，湖南省少年儿童图书馆里给小朋友讲故事的亲切姐姐。"
    "请把用户给出的主题改编成一个适合 3~8 岁儿童聆听的绘本故事，"
    "语气亲切温暖、句子简短口语化、内容健康积极，不要列表、不要 Markdown、不要英文术语。"
    "把整个故事拆成 8 到 10 个分镜，每个分镜是一句不超过 80 个字的叙述，合起来情节完整连贯。"
    "同时用一句话提炼主要角色的形象特征（年龄感、发型、服饰、颜色），供插画师保持角色一致。"
    "只输出 JSON，格式：{\"title\":\"故事标题\",\"characters\":\"角色形象描述\","
    "\"scenes\":[\"分镜1\",\"分镜2\",...]}，不要输出任何其他文字。"
)


class StoryScriptError(Exception):
    code = "script_failed"


class StoryModerationError(StoryScriptError):
    code = "moderation"


def _generation_call(**kw):          # 薄封装便于 mock（web-052）
    import dashscope
    from src.config import settings
    dashscope.api_key = settings.dashscope_api_key
    from dashscope import Generation
    rsp = Generation.call(**kw)
    if getattr(rsp, "status_code", 0) != 200:
        raise StoryScriptError(f"LLM HTTP {rsp.status_code}: {getattr(rsp, 'code', '')}")
    return rsp


def _extract_payload(rsp) -> dict:
    content = rsp.output.choices[0].message.content
    if isinstance(content, list):                     # 兼容多段 content
        content = "".join(c.get("text", "") for c in content)
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end <= start:
        raise StoryScriptError("响应无 JSON")
    return json.loads(content[start:end + 1])


def _clamp_scenes(scenes, max_chars: int, max_n: int) -> list[str]:
    out = []
    for s in scenes[:max_n]:
        s = (s or "").strip()
        if len(s) > max_chars:                        # 句边界截断（web-049 同款思路）
            cut = s[:max_chars]
            for i in range(len(cut) - 1, -1, -1):
                if cut[i] in "。！？!?":
                    cut = cut[:i + 1]
                    break
            s = cut
        if s:
            out.append(s)
    return out


class ScriptClient:
    def __init__(self, model: str, max_tokens: int, timeout_s: float):
        self._model, self._max_tokens, self._timeout = model, max_tokens, timeout_s

    def _call_llm(self, messages):
        # 显式 shutdown(wait=False)：超时后线程池退出不再等挂起调用，
        # 60s 超时（web-052/D8）才真实生效；测试行为不变
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            fut = pool.submit(_generation_call, model=self._model, messages=messages,
                              result_format="message", max_tokens=self._max_tokens)
            return fut.result(timeout=self._timeout)
        finally:
            pool.shutdown(wait=False)

    def generate(self, theme: str) -> dict:
        msgs = [{"role": "system", "content": SCRIPT_SYSTEM_PROMPT},
                {"role": "user", "content": f"故事主题：{theme}"}]
        last_err: Exception | None = None
        for attempt in (0, 1):                         # 校验失败带修正意见重试 1 次
            try:
                rsp = self._call_llm(msgs)
                payload = _extract_payload(rsp)
                scenes = payload.get("scenes") or []
                if not isinstance(scenes, list):
                    raise StoryScriptError("scenes 非列表")
                scenes = _clamp_scenes([str(s) for s in scenes], 80, 10)
                if len(scenes) < 6:
                    raise StoryScriptError(f"分镜过少: {len(scenes)}")
                return {"title": str(payload.get("title") or theme).strip() or theme,
                        "characters": str(payload.get("characters") or "").strip(),
                        "scenes": scenes}
            except StoryScriptError as e:
                last_err = e
                msgs = msgs + [{"role": "user", "content":
                                f"上次输出不合格（{e}），请严格按 JSON 格式重出，8~10 个分镜、每个≤80 字"}]
            except Exception as e:
                if "inspection" in str(e).lower() or "filter" in str(e).lower():
                    raise StoryModerationError(str(e)) from e
                last_err = StoryScriptError(str(e))
        raise last_err or StoryScriptError("生成失败")
