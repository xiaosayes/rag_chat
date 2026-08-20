"""AI 故事绘本（web-050 起）：意图/脚本/插图/缓存/编排。冻结内核零改动。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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
                if _is_moderation(e):                # 审核不重试（web-052 补强 I-1：HTTP 路径）
                    raise StoryModerationError(str(e)) from e
                last_err = e
                msgs = msgs + [{"role": "user", "content":
                                f"上次输出不合格（{e}），请严格按 JSON 格式重出，8~10 个分镜、每个≤80 字"}]
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


class StoryImageError(Exception):
    pass


def build_image_prompt(characters: str, scene: str) -> str:
    parts = [IMAGE_STYLE_PREFIX]
    if characters:
        parts.append(f"主要角色保持统一形象：{characters}。")
    parts.append(f"本页画面：{scene}")
    parts.append(IMAGE_NEGATIVE_SUFFIX)
    return "".join(parts)


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
    import urllib.request
    with urllib.request.urlopen(url, timeout=30) as r, open(path, "wb") as f:
        f.write(r.read())


def _extract_image_url(rsp) -> str:
    for item in rsp.output.choices[0].message.content:
        if isinstance(item, dict) and item.get("image"):
            return item["image"]
    raise StoryImageError("响应无图像")


class ImageClient:
    def __init__(self, model: str, size: str, timeout_s: float):
        self._model, self._size, self._timeout = model, size, timeout_s

    def _once(self, path: Path, prompt: str) -> None:
        # 同 Task 3 修正：pool.shutdown(wait=False)，超时不被 shutdown 卡住。
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            fut = pool.submit(_mmconversation_call,
                              model=self._model,
                              messages=[{"role": "user", "content": [{"text": prompt}]}],
                              prompt_extend=False, size=self._size)
            rsp = fut.result(timeout=self._timeout)
        finally:
            pool.shutdown(wait=False)
        _download(_extract_image_url(rsp), path)

    def generate_to(self, path: Path, prompt: str) -> bool:
        """失败自动重试 1 次；仍失败记日志返回 False（调用方走占位图降级）。"""
        for attempt in (0, 1):
            try:
                self._once(Path(path), prompt)
                return True
            except Exception as e:
                logger.warning("插图生成失败（第 %d 次）: %s", attempt + 1, e)
        return False


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
