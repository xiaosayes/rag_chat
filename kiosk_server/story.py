"""AI 故事绘本（web-050 起）：意图/脚本/插图/缓存/编排。冻结内核零改动。"""
from __future__ import annotations

import hashlib
import json
import logging
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from .chat import BroadcastSession          # noqa: F401  web-055（Task 7 播报复用）
from .tts_clean import clean_for_broadcast  # noqa: F401  （Task 7 播报路径使用）

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


# ==================== web-074：意图分层闸（泛化性加强） ====================

# 负向元问题（问能力/定义/内容，不是点故事）——最先拦，不进 LLM
_META_QUESTION_RE = re.compile(
    # web-074 评审修复：会不会/能不能 须邻接讲/说（防误杀「丑小鸭会不会变天鹅」类故事题）；
    # 什么是 须句首（防误杀「讲一个什么是友谊的绘本」）
    r"(会不会|能不能|可以不可以|可不可以).{0,4}(讲|说)|"
    r"(会|能|可以)(讲|说)(故事|绘本|童话)吗?$|"
    r"^(你|湘小图)?什么是|"
    r"(故事|绘本|童话).{0,6}(讲了什么|是什么|有哪些|怎么样)|"
    r"(都|一共)?(会|能)讲(什么|哪些)(故事|绘本|童话)"
)
# 安全问答信号（几乎不会出现在故事请求里）——命中直接走问答，省 LLM 延迟
_SAFE_QA_RE = re.compile(
    r"几点|多少|为什么|怎么样|怎么办|什么意思|哪些|哪里|哪儿|哪个|开放|预约|展览|活动|时间"
)

INTENT_SYSTEM_PROMPT = (
    "你是少儿图书馆一体机的意图分类器。判断用户的话是否为「想听故事/绘本」的请求，"
    "只输出 JSON：{\"intent\":\"story\",\"theme\":\"主题\"} 或 {\"intent\":\"qa\",\"theme\":\"\"}。"
    "判定 story：只要用户想听故事，无论怎么表达（我想听嫦娥奔月／给我讲讲后羿射日／"
    "来一个三只小猪／有没有关于恐龙的故事）；theme 提取故事主题（人名/故事名/主题词），"
    "没有主题则留空。特别注意：「你会讲/能讲 + 具体故事名 + 吗」（如你会讲西游记吗）"
    "是想听这个故事，判 story 并提取该故事名。"
    "判定 qa：知识问答、时间地点开放信息、闲聊、问你会不会讲故事（没有点名具体故事）、"
    "询问某个故事的内容。"
)


def classify_intent_llm(text: str, *, model: str, timeout: float = 6.0) -> dict:
    """LLM 意图分类+主题提取（web-074 兜底路径）。超时/异常向上抛（调用方回退）。"""
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        fut = pool.submit(_generation_call, model=model,
                          messages=[{"role": "system", "content": INTENT_SYSTEM_PROMPT},
                                    {"role": "user", "content": text}],
                          result_format="message", max_tokens=80,
                          enable_thinking=False)
        rsp = fut.result(timeout=timeout)
    finally:
        pool.shutdown(wait=False)
    payload = _extract_payload(rsp)
    intent = str(payload.get("intent") or "").strip()
    if intent not in ("story", "qa"):
        raise StoryScriptError(f"意图分类返回非法 intent: {intent!r}")
    return {"intent": intent, "theme": str(payload.get("theme") or "").strip()}


def resolve_story_intent(text: str, classify: Callable[[str], dict] | None = None) -> str | None:
    """分层闸：①meta 元问题拦截 → ②正则快路径（零延迟）→ ③安全问答信号（省 LLM）
    → ④LLM 兜底分类（模糊表达泛化识别，顺带提取主题）。LLM 故障回退 None（走问答）。"""
    t = (text or "").strip()
    if not t or len(t) > 50:
        return None
    if _META_QUESTION_RE.search(t):
        return None
    theme = parse_story_intent(t)
    if theme:
        return theme
    if _SAFE_QA_RE.search(t):
        return None
    if classify is None:
        return None
    try:
        res = classify(t)
    except Exception as e:
        logger.warning("意图分类失败，回退问答（%s）: %s", t, e)
        return None
    if res.get("intent") == "story":
        theme = (res.get("theme") or "").strip(_THEME_STRIP)
        if 2 <= len(theme) <= 20:
            return theme
    return None


# ==================== web-052：分镜脚本（qwen-plus，固定云端） ====================

# web-070：脚本换型 deepseek-v4-flash-0731 + 强忠实条款（「严格按照大家熟知的主流版本」——
# 实测农夫与蛇主线全对：蛇咬农夫/寓意不反转）+ images 画面描述字段（15~25 字纯画面短句——
# 实测整段叙述 prose 喂图会被图像模型当文字渲染=插图乱码根因）。
# prompt 原文逐字取自探测稿 scripts/_diag_ds_script.py NEW_PROMPT（实测 8.7s 出全量脚本）。
SCRIPT_SYSTEM_PROMPT = (
    "你是湘小图，湖南省少年儿童图书馆里给小朋友讲故事的亲切姐姐。"
    "请把用户给出的主题改编成一个适合 3~8 岁儿童聆听的绘本故事，"
    "语气亲切温暖、句子简短口语化、内容健康积极，不要列表、不要 Markdown、不要英文术语。"
    "若主题出自已有的寓言、成语、神话或童话故事（例如龟兔赛跑、守株待兔、嫦娥奔月、农夫与蛇），"
    "必须严格按照大家熟知的主流版本讲述：主要角色、关键情节、结局和寓意都与原著一致，"
    "不得自由发挥、不得添加或删改原著的主要情节与角色、不得反转寓意"
    "（例如农夫与蛇的结局必须是蛇咬了农夫，寓意是不能怜悯恶人）；"
    "儿童化只体现在用词和语气上，可在不改动情节主线与结局的前提下做适龄化柔化。"
    "把整个故事拆成 8 到 10 个分镜，每个分镜是一段 40 到 80 个字的叙述，合起来情节完整连贯。"
    "同时用一句话提炼主要角色的形象特征（年龄感、发型、服饰、颜色），供插画师保持角色一致。"
    "再给每个分镜配一句 15 到 25 字的画面描述：只写角色、动作、场景（谁、在哪里、做什么），"
    "是对画面内容的客观描述，不要对话、不要心理描写、不要引号、不要书名号。"
    "只输出 JSON，格式：{\"title\":\"故事标题\",\"characters\":\"角色形象描述\","
    "\"scenes\":[\"分镜1\",\"分镜2\",...],"
    "\"images\":[\"画面1\",\"画面2\",...]}，images 与 scenes 一一对应，不要输出任何其他文字。"
)


def _clean_image_desc(text: str) -> str:
    """web-070：画面描述防御性清洗——剥引语（防对话框文字渲染）+ 去书名号（防标题书法渲染）。"""
    return strip_dialogue_for_image(text).replace("《", "").replace("》", "").strip()


class StoryScriptError(Exception):
    code = "script_failed"


class StoryModerationError(StoryScriptError):
    code = "moderation"


def _is_moderation(err: Exception) -> bool:
    """审核拦截判定（web-052 补强 I-1）：HTTP 包装路径（LLM HTTP 400:
    DataInspectionFailed）与异常路径共用；审核错误不可重试。"""
    msg = str(err).lower()
    return "inspection" in msg or "filter" in msg


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
            # web-067：enable_thinking=False——qwen-plus 默认开隐式推理，
            # 实测脚本时延 21.2s→10.9s（用户验收要求首屏 ≤10s 级）
            fut = pool.submit(_generation_call, model=self._model, messages=messages,
                              result_format="message", max_tokens=self._max_tokens,
                              enable_thinking=False)
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
                raw_scenes = [str(s) for s in scenes]
                # web-070：images 画面描述字段——与 scenes 逐对 clamp 对齐（防下标错位）；
                # 缺失/非列表/长度不符 → None 回退（worker 侧用 scene 剥引语兑底）
                raw_images = payload.get("images")
                images: list[str] | None = None
                if isinstance(raw_images, list) and len(raw_images) == len(raw_scenes):
                    pairs = []
                    for sc, im in list(zip(raw_scenes, raw_images))[:10]:
                        clamped = _clamp_scenes([sc], 80, 1)
                        if clamped:
                            pairs.append((clamped[0],
                                          _clean_image_desc(str(im)) or clamped[0]))
                    scenes = [p[0] for p in pairs]
                    images = [p[1] for p in pairs]
                else:
                    scenes = _clamp_scenes(raw_scenes, 80, 10)
                if len(scenes) < 6:
                    raise StoryScriptError(f"分镜过少: {len(scenes)}")
                # web-064：短分镜（<40 字）播报快于插图生成——首轮校验不合格重试，
                # 重试后仍短则接受+告警（自动翻页等图机制兑底体验，不判失败）
                shorts = [s for s in scenes if len(s) < 40]
                if shorts and attempt == 0:
                    raise StoryScriptError(f"{len(shorts)} 个分镜不足 40 字")
                if shorts:
                    logger.warning("分镜 %d 段不足 40 字（重试后仍短，接受）", len(shorts))
                return {"title": str(payload.get("title") or theme).strip() or theme,
                        "characters": str(payload.get("characters") or "").strip(),
                        "scenes": scenes, "images": images}
            except StoryScriptError as e:
                if _is_moderation(e):                # 审核不重试（web-052 补强 I-1：HTTP 路径）
                    raise StoryModerationError(str(e)) from e
                last_err = e
                msgs = msgs + [{"role": "user", "content":
                                f"上次输出不合格（{e}），请严格按 JSON 格式重出，8~10 个分镜、"
                                "每个 40~80 字，并给每个分镜配 15~25 字 images 画面描述"}]
            except Exception as e:
                if _is_moderation(e):
                    raise StoryModerationError(str(e)) from e
                last_err = StoryScriptError(str(e))
        raise last_err or StoryScriptError("生成失败")


# ======================= web-053：插图生成（qwen-image-3.0） =======================

IMAGE_STYLE_PREFIX = (
    "中国传统绘本插画，水彩淡彩，色调柔和温暖，儿童读物风格，画面简洁干净。"
)
IMAGE_NEGATIVE_SUFFIX = "画面中不要出现任何文字、水印、标志；不要恐怖、阴暗元素。"
# web-069：专用 negative_prompt 参数（第二层防线；第一层=引语剥除——实测引语入
# prompt 必被渲染成对话框文字，prompt 内否定句与 negative_prompt 单独均压不住）
IMAGE_NEGATIVE_PROMPT = (
    "文字，汉字，字母，拼音，数字，符号，水印，标志，字幕，标题，"
    "畸形，多余的肢体，多余的耳朵，五官错位，肢体融合，恐怖，阴暗")


_QUOTED_SPEECH_RE = re.compile(
    r"[说喊问道答叫嚷唱讲][:：]?\s*[「『“”‘’\"][^「」『』“”‘’\"]{0,120}[」』“”‘’\"][。！？]?")
_QUOTE_SPAN_RE = re.compile(r"[「『“”‘’\"][^「」『』“”‘’\"]{0,120}[」』“”‘’\"]")


def strip_dialogue_for_image(scene: str) -> str:
    """web-069：剥除引语——图像模型见到「…」/‘…’引文必渲染对话框（乱码文字根因）；
    引语连同「说/喊/问」谓语整段剥除仅留动作（「说着话」改写实测仍诱发空气泡+符号），
    剥光则回退原文（不为空 prompt）。"""
    t = _QUOTED_SPEECH_RE.sub("，", scene)        # 引语连同「说/喊/问」谓语整段剥除（实测残留「说着话」仍会诱发空对话框/符号）
    t = _QUOTE_SPAN_RE.sub("", t)
    t = re.sub(r"[，,]{2,}", "，", t)
    t = re.sub(r"，\s*([。！？；])", r"\1", t)
    t = re.sub(r"，\s*$", "。", t)
    t = t.strip().lstrip("，,").strip()
    return t or scene


class StoryImageError(Exception):
    pass


def _is_rate_limit(err: Exception) -> bool:
    """web-067：限流判定（429 Throttling.RateQuota）——实测并发>2 即触发（0.2s 秒拒），
    此类错误立即重发只会再撞限流，必须退避后重试（见 ImageClient.generate_to）。"""
    msg = str(err).lower()
    return ("throttl" in msg or "ratequota" in msg or "rate_limit" in msg
            or "rate limit" in msg or "429" in msg)


def build_image_prompt(characters: str, scene: str) -> str:
    parts = [IMAGE_STYLE_PREFIX]
    if characters:
        parts.append(f"主要角色保持统一形象：{characters}。")
    parts.append(f"本页画面：{strip_dialogue_for_image(scene)}")   # web-069：剥引语防对话框文字
    parts.append(IMAGE_NEGATIVE_SUFFIX)
    return "".join(parts)


def build_first_image_prompt(theme: str) -> str:
    """web-067：首页并行预生成 prompt——脚本 ~11s 期间用主题先生成首页插画
    （角色锚未定稿，以主题场景为主；失败由页 1 worker 落回 scene prompt 重生成）。"""
    return (IMAGE_STYLE_PREFIX
            + f"儿童绘本故事《{theme}》的开篇插画，主角登场、点明故事场景："
            + f"{strip_dialogue_for_image(theme)}。"
            + IMAGE_NEGATIVE_SUFFIX)


def _mmconversation_call(**kw):        # 薄封装便于 mock（web-053）
    import dashscope
    from src.config import settings
    dashscope.api_key = settings.dashscope_api_key
    from dashscope import MultiModalConversation
    rsp = MultiModalConversation.call(**kw)
    if getattr(rsp, "status_code", 0) != 200:
        raise StoryImageError(f"image HTTP {rsp.status_code}: {getattr(rsp, 'code', '')}")
    return rsp


def _download(url: str, path: Path) -> None:
    """web-063 终审 F2：原子落盘——先写同目录 .part 临时文件再 os.replace；
    任何失败清理临时文件，目标路径不留截断残文件（缓存命中 path.exists() 不误用半张图）。"""
    import os
    import urllib.request
    tmp = path.with_suffix(".part")
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = r.read()
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _extract_image_url(rsp) -> str:
    for item in rsp.output.choices[0].message.content:
        if isinstance(item, dict) and item.get("image"):
            return item["image"]
    raise StoryImageError("响应无图像")


class ImageClient:
    def __init__(self, model: str, size: str, timeout_s: float, rate_wait_s: float = 6.0):
        self._model, self._size, self._timeout = model, size, timeout_s
        self._rate_wait = rate_wait_s         # web-067：限流退避秒数

    def _once(self, path: Path, prompt: str) -> None:
        # 同 Task 3 修正：pool.shutdown(wait=False)，超时不被 shutdown 卡住。
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            fut = pool.submit(_mmconversation_call,
                              model=self._model,
                              messages=[{"role": "user", "content": [{"text": prompt}]}],
                              prompt_extend=False, size=self._size,
                              negative_prompt=IMAGE_NEGATIVE_PROMPT)   # web-069
            rsp = fut.result(timeout=self._timeout)
        finally:
            pool.shutdown(wait=False)
        _download(_extract_image_url(rsp), path)

    def generate_to(self, path: Path, prompt: str, should_stop=None) -> bool:
        """重试策略（web-067 重写）：
        - 限流错（429 Throttling.RateQuota）：可中断退避 rate_wait_s 后重试，≤3 次
          ——立即重发只会再撞限流（实测 0.2s 秒拒）；
        - 其他错误：立即重试 ≤1 次（web-053 原语义）；两类计数独立；超限返回 False。
        - should_stop（零参 callable）在每次尝试前与退避期间检查——True 立即返回 False
          （web-065：取消后不重试、不发起新调用，生图费用止血）。
        """
        rate_retries = 0
        plain_retried = False
        while True:
            if should_stop is not None and should_stop():
                logger.info("插图生成已取消，跳过尝试")
                return False
            try:
                self._once(Path(path), prompt)
                return True
            except Exception as e:
                if _is_rate_limit(e) and rate_retries < 3:
                    rate_retries += 1
                    logger.warning("插图限流，%.1fs 后重试（第 %d/3 次）: %s",
                                   self._rate_wait, rate_retries, e)
                    if self._interrupted(self._rate_wait, should_stop):
                        logger.info("插图限流退避期间被取消，放弃重试")
                        return False
                    continue
                if not _is_rate_limit(e) and not plain_retried:
                    plain_retried = True
                    logger.warning("插图生成失败，立即重试 1 次: %s", e)
                    continue
                logger.warning("插图生成最终失败: %s", e)
                return False

    @staticmethod
    def _interrupted(seconds: float, should_stop) -> bool:
        """0.5s tick 可中断退避：should_stop 翻 True 提前返回 True（取消尽快止血）。"""
        waited = 0.0
        while waited < seconds:
            if should_stop is not None and should_stop():
                return True
            step = min(0.5, seconds - waited)
            time.sleep(step)
            waited += step
        return should_stop is not None and should_stop()


# ==================== web-054：同名故事缓存 ====================


def _normalize_theme(theme: str) -> str:
    return re.sub(r"[\s，。！？、,.!?~…·]+", "", theme or "")


class StoryCache:
    """data/story/<story_id>/ 落盘缓存：meta.json + page_<n>.png。

    命中条件 = meta.json 存在且 scenes 非空（图片缺失容忍，Task 6 补生成）；
    容量超 max_mb 按 last_access LRU 整故事淘汰。
    """

    def __init__(self, root: str, max_mb: int):
        self._root = Path(root)
        self._max_bytes = int(max_mb) * 1024 * 1024

    @staticmethod
    def story_id(theme: str) -> str:
        return hashlib.sha1(_normalize_theme(theme).encode("utf-8")).hexdigest()[:12]

    def _dir(self, sid: str) -> Path:
        return self._root / sid

    def image_path(self, sid: str, n: int) -> Path:
        return self._dir(sid) / f"page_{n}.png"

    def load(self, theme: str) -> dict | None:
        meta = self._dir(self.story_id(theme)) / "meta.json"
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            if not data.get("scenes"):
                return None
            self.save_meta_touch(data["id"] if "id" in data else self.story_id(theme))
            data["id"] = data.get("id") or self.story_id(theme)
            return data
        except Exception:
            return None

    def save(self, theme: str, script: dict) -> str:
        sid = self.story_id(theme)
        self._dir(sid).mkdir(parents=True, exist_ok=True)
        now = time.time()
        meta = {"id": sid, "theme": theme, "title": script["title"],
                "characters": script.get("characters", ""), "scenes": script["scenes"],
                "images": script.get("images"),              # web-070：画面描述随脚本缓存
                "created": now, "last_access": now}
        (self._dir(sid) / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return sid

    def save_meta_touch(self, sid: str, last_access: float | None = None) -> None:
        meta = self._dir(sid) / "meta.json"
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            data["last_access"] = time.time() if last_access is None else last_access
            data.setdefault("id", sid)
            meta.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def evict_if_needed(self) -> None:
        if not self._root.exists():
            return
        dirs = [d for d in self._root.iterdir() if d.is_dir()]

        def size(d: Path) -> int:
            return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())

        def last_access(d: Path) -> float:
            try:
                return json.loads((d / "meta.json").read_text(encoding="utf-8"))["last_access"]
            except Exception:
                return 0.0

        total = sum(size(d) for d in dirs)
        if total <= self._max_bytes:
            return
        import shutil
        for d in sorted(dirs, key=last_access):
            if total <= self._max_bytes:
                break
            total -= size(d)
            shutil.rmtree(d, ignore_errors=True)
            logger.info("故事缓存 LRU 淘汰: %s", d.name)


# ==================== web-055：StorySession 启动链路 ====================


class _StoryPagePipeline:
    """当页文本即「问题」：BroadcastSession 原编排零改动复用（web-055）。"""
    def query_stream(self, question, conversation_history=None):
        yield question


class StorySession:
    """绘本编排（web-055）：preparing→cache/script→story_begin→插图编排→指令循环。

    线程模型：start() 由调用方线程阻塞执行；on_page/on_finish/cancel 由 WS 线程
    非阻塞投递指令；插图生成页序提交、信号量限并发、总预算兜底。
    """

    def __init__(self, emit, script_client, image_client, cache, tts_factory, cfg, *,
                 clock=time.monotonic, speak_fn: Callable[[int], None] | None = None):
        self._emit = emit
        self._script = script_client
        self._image = image_client
        self._cache = cache
        self._tts_factory = tts_factory
        self._cfg = cfg
        self._clock = clock
        self._speak_fn = speak_fn                  # web-055 测试桩兼容：注入即接管播报
        self._tts: BroadcastSession | None = None  # web-056：绘本专用播报实例
        self._speaking_page = 0
        self._cmd: queue.Queue = queue.Queue()
        self._active = threading.Event()
        self._cancel = threading.Event()
        self._img_done = threading.Event()
        self._pages: list[dict] = []
        self._sid = ""
        self._title = ""
        self._characters = ""
        self._pregen: dict | None = None         # web-067：首页并行预生成状态

    @property
    def active(self) -> bool:
        return self._active.is_set()

    def wait_idle(self, timeout: float) -> bool:   # 测试辅助：有界等 start() 跑完
        end = self._clock() + timeout
        while self._active.is_set() and self._clock() < end:
            time.sleep(0.01)
        return not self._active.is_set()

    # ---------- 指令入口（WS 线程调用，非阻塞） ----------

    def on_page(self, n: int) -> None:
        self._cmd.put(("page", int(n)))

    def on_finish(self) -> None:
        self._cmd.put(("finish", None))

    def cancel(self) -> None:
        # web-063 终审 F5：同时置取消旗标——准备期（script 阻塞）取消不反弹，
        # 插图线程同步收尾（对齐 D7「取消未完成插图任务」）
        self._cancel.set()
        self._cmd.put(("cancel", None))

    def close(self) -> None:
        self.cancel()

    # ---------- 主流程（调用方线程阻塞执行） ----------

    def start(self, theme: str) -> None:
        self._active.set()
        try:
            self._emit({"type": "story_preparing", "theme": theme})
            # web-067：首页插图并行预生成——脚本 ~11s 期间用主题 prompt 先生成首页
            # （t≈7~10s 落地），首屏「文字+插画」合计 ≤12s；失败由页 1 worker 落回
            # scene prompt 重生成。事件统一由页 1 worker 在 story_begin 后发出——
            # 前端 begin 会清空 images 表，预生成线程直发（先到）会被抹掉。
            if getattr(self._cfg, "story_first_image_fast", False):
                sid0 = self._cache.story_id(theme)
                p1 = self._cache.image_path(sid0, 1)
                if not p1.exists():
                    self._pregen = {"done": threading.Event(), "ok": False}
                    threading.Thread(target=self._pregen_first_image,
                                     args=(theme, p1), daemon=True).start()
            cached = self._cache.load(theme)
            if cached:
                sid, title, characters, scenes, is_cached = (
                    cached["id"], cached["title"], cached.get("characters", ""),
                    cached["scenes"], True)
                images = cached.get("images")                # web-070：旧缓存无此字段→None 回退
            else:
                try:
                    script = self._script.generate(theme)
                except StoryScriptError as e:
                    logger.warning("故事脚本生成失败（%s）: %s", theme, e)   # web-070：留痕可诊断
                    self._emit({"type": "story_error",
                                "code": getattr(e, "code", "script_failed"),
                                "message": "这个故事我不太会讲，换一个试试吧"})
                    self._emit({"type": "story_end", "reason": "error"})
                    return
                sid = self._cache.save(theme, script)
                title, characters, scenes, is_cached = (
                    script["title"], script.get("characters", ""), script["scenes"], False)
                images = script.get("images")
            if self._cancel.is_set():
                # web-063 终审 F5：准备期取消——不发 story_begin 不开播，直接收尾
                self._emit({"type": "story_end", "reason": "cancelled"})
                return
            self._sid, self._title, self._characters = sid, title, characters
            # web-070：img=画面短句（生图 prompt 用）；story_begin 载荷保持 {n,text} 不泄漏
            self._pages = [{"n": i + 1, "text": t,
                            "img": images[i] if images and i < len(images) else None}
                           for i, t in enumerate(scenes)]
            self._emit({"type": "story_begin", "story_id": sid, "title": title,
                        "total": len(self._pages), "cached": is_cached,
                        "pages": [{"n": p["n"], "text": p["text"]} for p in self._pages]})
            self._start_image_workers()
            self._command_loop()
        finally:
            self._cancel.set()                     # 通知插图线程收尾
            if self._tts is not None:
                self._tts.close()                  # web-056：取消在途播报
            try:
                self._cache.evict_if_needed()        # web-063 终审 F1：500MB LRU 接线
            except Exception as e:
                logger.warning("故事缓存 LRU 淘汰异常: %s", e)
            self._active.clear()

    # ---------- 插图编排：页序提交、并发受限、预算兜底 ----------

    def _pregen_first_image(self, theme: str, p1: Path) -> None:
        """web-067：首页并行预生成线程——只生成落盘+记录结果，不发事件
        （事件由页 1 worker 在 story_begin 之后统一发出，避免被前端 begin 清表抹掉）。"""
        pregen = self._pregen
        try:
            p1.parent.mkdir(parents=True, exist_ok=True)
            pregen["ok"] = self._image.generate_to(
                p1, build_first_image_prompt(theme),
                should_stop=self._cancel.is_set)   # web-065：取消止血贯穿
        finally:
            pregen["done"].set()

    def _start_image_workers(self) -> None:
        sem = threading.Semaphore(self._cfg.story_image_concurrency)
        deadline = self._clock() + self._cfg.story_total_budget_s

        def worker(page: dict) -> None:
            n = page["n"]
            path = self._cache.image_path(self._sid, n)
            with sem:
                if self._cancel.is_set():
                    return
                if self._clock() > deadline:
                    # web-066：预算跳页也补 failed 事件——等图护栏（web-064）下
                    # 不冻结自动翻页（播到跳页时 imgDone 可达成，末页亦能收尾）
                    self._emit({"type": "story_page_img", "n": n, "url": None,
                                "failed": True})
                    return
                if n == 1 and self._pregen is not None:
                    # web-067：等首页预生成落地（页序提交页 1 先持信号量，无死锁；
                    # 等待有界——预生成线程内 should_stop/超时保证）。ok=True 直接用
                    # 预生成结果（不重生图）；ok=False 落回下面 scene prompt 重生成。
                    # 事件仍由本 worker 统一发（在 story_begin 之后，不被前端清表抹掉）。
                    self._pregen["done"].wait()
                    ok = bool(self._pregen["ok"])
                    if not ok and not self._cancel.is_set():
                        if self._clock() > deadline:
                            # web-068：wait 后重查预算——预生成全程限流退避等病态场景下
                            # 不再发起超预算 fallback，补 failed 事件了结页 1（防等图护栏冻结）
                            self._emit({"type": "story_page_img", "n": n, "url": None,
                                        "failed": True})
                            return
                        logger.info("首页预生成未果，落回 scene prompt 重生成")
                elif path.exists():
                    ok = True                       # 缓存/补生成跳过
                else:
                    ok = False
                if not ok and not self._cancel.is_set():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    ok = self._image.generate_to(
                        path, build_image_prompt(self._characters,
                                                 page.get("img") or page["text"]),  # web-070
                        should_stop=self._cancel.is_set)   # web-065：取消止血贯穿
            if ok and not self._cancel.is_set():
                self._emit({"type": "story_page_img", "n": n,
                            "url": f"/api/story/{self._sid}/img/{n}"})
            elif not ok and not self._cancel.is_set():
                # web-064：失败落地也发事件——前端据 failed 放行自动翻页（占位页不卡故事）
                self._emit({"type": "story_page_img", "n": n, "url": None, "failed": True})

        def run_all() -> None:
            threads = [threading.Thread(target=worker, args=(p,), daemon=True)
                       for p in self._pages]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self._img_done.set()

        threading.Thread(target=run_all, daemon=True).start()

    # ---------- 播报（复用 BroadcastSession：句边界/清洗/看门狗/打断串行化，web-056） ----------

    def _make_broadcast(self) -> BroadcastSession:
        self._speaking_page = 0

        def wrapped(ev: dict) -> None:
            t = ev.get("type")
            if t == "answer_start":
                self._emit({"type": "story_speak_start", "n": self._speaking_page})
                return
            if t == "answer_chunk":
                return                                 # 文本已在 story_begin 全量下发
            if t == "answer_end":
                self._emit({"type": "story_speak_end", "n": self._speaking_page,
                            "cancelled": bool(ev.get("cancelled"))})
                return
            self._emit(ev)                             # audio*/playback_cancel 透传

        return BroadcastSession(_StoryPagePipeline(), self._tts_factory, wrapped,
                                accum_chars=60, watchdog_s=15.0, first_floor_chars=12)

    def _speak(self, n: int, text: str | None = None) -> None:
        """播一页（新线程；BroadcastSession.ask 自带 web-029 打断串行化）。"""
        if self._speak_fn is not None:                 # web-055 测试桩兼容：注入即接管
            self._speaking_page = n
            self._speak_fn(n)
            return
        if self._tts is None:
            self._tts = self._make_broadcast()
        self._speaking_page = n
        body = text if text is not None else self._pages[n - 1]["text"]
        threading.Thread(target=self._tts.ask, args=(body,), daemon=True).start()
        # 等 busy 置位（有界）——否则紧随的 _wait_speak_done 会在新线程起跑前误判空转
        end = self._clock() + 2.0
        while not self._tts.busy and self._clock() < end:
            time.sleep(0.005)

    def _wait_speak_done(self, timeout: float) -> None:
        end = self._clock() + timeout
        while self._tts is not None and self._tts.busy and self._clock() < end:
            time.sleep(0.05)

    # ---------- 指令循环（web-056：开播第 1 页/翻页即切/收尾/cancel） ----------

    def _command_loop(self) -> None:
        reason = "done"
        if not self._cancel.is_set():
            self._speak(1)                           # story_begin 后自动开播第 1 页（F5：已取消不开播）
        while True:
            kind, payload = self._cmd.get()
            if kind == "cancel":
                reason = "cancelled"
                break
            if kind == "finish":
                # web-056 补强：先 barge+排空在播页（busy=False），再播收尾语——
                # 否则 _speak 的 busy 等待被旧轮 busy 蒙混、_wait_speak_done 在
                # BroadcastSession 串行化空窗（0.05s 轮询间隙）采样到 busy==False
                # 提前返回：story_end 抢跑、收尾语被 start() finally 的 close() 裁掉。
                if self._tts is not None:
                    self._tts.barge_in()
                    self._wait_speak_done(5.0)
                self._speak(0, self._cfg.story_closing)
                self._wait_speak_done(30.0)          # 收尾语播尽再发 story_end
                break
            if kind == "page" and 1 <= payload <= len(self._pages):
                if (self._tts is None or payload != self._speaking_page
                        or not self._tts.busy):
                    self._speak(payload)
        if reason == "cancelled":
            if self._tts is not None:
                self._tts.barge_in()
            self._wait_speak_done(5.0)
        self._emit({"type": "story_end", "reason": reason})
