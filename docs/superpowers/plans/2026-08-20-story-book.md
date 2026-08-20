# AI 故事绘本 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用户对一体机说「给我讲一个〈主题〉的故事」→ 翻页式图文绘本（8~10 页，一页=一图+≤80字）+ 逐页语音讲解，自动翻页、手动翻页即切播报。

**Architecture:** WS 单通道扩展（方案 A）：服务端正则拦截故事意图进 `StorySession`（新模块 `kiosk_server/story.py`），qwen-plus 出分镜脚本（JSON），qwen-image-3.0 异步并发 ≤4 出图落盘 `data/story/`，播报复用专用 `BroadcastSession` 实例（`StoryPagePipeline` 喂当页文本），前端 `StoryBook.vue` + `useStorySession` 页码主导翻页，`PcmPlayer.onEnded` 驱动自动推进。设计溯源：`docs/superpowers/specs/2026-08-20-story-book-design.md`（D1~D10 全部拍板）。

**Tech Stack:** Python/FastAPI/dashscope SDK 1.25.1（Generation + MultiModalConversation）；Vue3+Vite+TS+Pinia；pytest / vitest。

## Global Constraints

- 冻结区零改动：`src/`、`app.py`、`.env`——本轮不申请任何例外。
- Gradio 6.22 依赖钉：`starlette<1.4` + `fastapi<1.0`（不新增第三方依赖）。
- TDD：先失败测试后实现；外部 API（qwen-plus/qwen-image-3.0/讯飞/CosyVoice）一律 mock；真实 API 仅 Task 14 冒烟。
- 提交前缀 `feat(web):`；测试/注释标签 web-050~063（每任务一号）；每任务结束必须提交。
- `tests/`、`frontend/`、`docs/`、`data/` 被 gitignore：新文件必须 `git add -f`。
- 密钥（DASHSCOPE/XFYUN）只在服务端，永不进前端、永不打印。
- 基线必须保持全绿：`python -m pytest tests/ -q` = 772+新增 passed；`cd frontend && npx vitest run` = 74+新增 passed。
- 绘本全流程固定云端（不随 LLM_PROVIDER）；`qwen-image-3.0` 必须走 `MultiModalConversation.call`，`prompt_extend=False`、`size="1024*1024"`（实测 71s→12.9s）。
- 绘本播放全程无语音打断：故事态服务端丢弃所有上行音频帧；`barge_in` 忽略。

## 跨任务接口约定（后序任务只认这些签名）

```python
# kiosk_server/story.py
def parse_story_intent(text: str) -> str | None          # 命中返回主题，否则 None
class StoryScriptError(Exception): code = "script_failed"
class StoryModerationError(StoryScriptError): code = "moderation"
class StoryImageError(Exception): ...
class ScriptClient:                                      # qwen-plus 分镜脚本
    def __init__(self, model: str, max_tokens: int, timeout_s: float)
    def generate(self, theme: str) -> dict               # {"title":str,"characters":str,"scenes":[str]}
def build_image_prompt(characters: str, scene: str) -> str
class ImageClient:                                       # qwen-image-3.0
    def __init__(self, model: str, size: str, timeout_s: float)
    def generate_to(self, path, prompt: str) -> bool     # 失败自动重试 1 次；落盘成功 True
class StoryCache:
    def __init__(self, root: str, max_mb: int)
    @staticmethod
    def story_id(theme: str) -> str                      # sha1(归一化主题)[:12]
    def load(self, theme: str) -> dict | None            # {"id","title","characters","scenes":[str]}
    def save(self, theme: str, script: dict) -> str      # 落 meta.json，返回 story_id
    def image_path(self, sid: str, n: int) -> Path       # root/sid/page_<n>.png
    def evict_if_needed(self) -> None                    # 超 max_mb 按 last_access LRU 整目录淘汰
class StorySession:
    def __init__(self, emit, script_client, image_client, cache, tts_factory, cfg, clock=time.monotonic)
    active: bool
    def start(self, theme: str) -> None                  # 阻塞驱动指令循环（调用方在线程中跑）
    def on_page(self, n: int) -> None
    def on_finish(self) -> None
    def cancel(self) -> None
    def close(self) -> None
# VoiceSession 新增
def set_story_session(self, story_session_factory)       # voice_ws 注入（可测试）
def on_story_page(self, n: int) -> None
def on_story_finish(self) -> None
def on_story_cancel(self) -> None
```

下行事件（与 spec §4 一致）：`story_preparing{theme}` / `story_begin{story_id,title,total,cached,pages:[{n,text}]}` / `story_page_img{n,url}` / `story_speak_start{n}` / `story_speak_end{n,cancelled}` / `story_end{reason}` / `story_error{code,message}`；`audio_start/audio/audio_end/playback_cancel` 原样透传（驱动前端播放器）。

---

### Task 1: KioskConfig 故事配置族（web-050）

**Files:**
- Modify: `kiosk_server/config.py`
- Test: `tests/test_web050_story_config.py`

**Interfaces:**
- Produces: `KioskConfig` 新增字段（后续任务全部经 `cfg.story_*` 读取，不读环境变量）：
  `story_enabled=True, story_script_model="qwen-plus", story_script_max_tokens=1600, story_script_timeout_s=60.0, story_min_scenes=8, story_max_scenes=10, story_scene_max_chars=80, story_image_model="qwen-image-3.0", story_image_size="1024*1024", story_image_concurrency=4, story_image_timeout_s=90.0, story_total_budget_s=300.0, story_cache_dir="data/story", story_cache_max_mb=500, story_closing="故事讲完啦，还想听什么故事吗？"`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web050_story_config.py
# web-050：故事绘本配置族（KIOSK_STORY_*，默认零行为变化）
import os
from kiosk_server.config import KioskConfig


class TestStoryConfig:
    def test_defaults(self, monkeypatch):
        for k in list(os.environ):
            if k.startswith("KIOSK_STORY_"):
                monkeypatch.delenv(k)
        cfg = KioskConfig.from_env()
        assert cfg.story_enabled is True
        assert cfg.story_script_model == "qwen-plus"
        assert cfg.story_script_max_tokens == 1600
        assert cfg.story_script_timeout_s == 60.0
        assert cfg.story_min_scenes == 8 and cfg.story_max_scenes == 10
        assert cfg.story_scene_max_chars == 80
        assert cfg.story_image_model == "qwen-image-3.0"
        assert cfg.story_image_size == "1024*1024"
        assert cfg.story_image_concurrency == 4
        assert cfg.story_image_timeout_s == 90.0
        assert cfg.story_total_budget_s == 300.0
        assert cfg.story_cache_dir == "data/story"
        assert cfg.story_cache_max_mb == 500
        assert "故事讲完啦" in cfg.story_closing

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("KIOSK_STORY_ENABLED", "false")
        monkeypatch.setenv("KIOSK_STORY_IMAGE_CONCURRENCY", "2")
        monkeypatch.setenv("KIOSK_STORY_CACHE_MAX_MB", "100")
        cfg = KioskConfig.from_env()
        assert cfg.story_enabled is False
        assert cfg.story_image_concurrency == 2
        assert cfg.story_cache_max_mb == 100
```

- [ ] **Step 2: 跑测试确认失败** — `python -m pytest tests/test_web050_story_config.py -q`，预期 AttributeError。

- [ ] **Step 3: 实现**（`kiosk_server/config.py` dataclass 字段 + `from_env` 逐项 `os.getenv`，模式照抄既有字段）

- [ ] **Step 4: 跑测试确认通过 + 回归** — `python -m pytest tests/test_web050_story_config.py tests/test_kiosk_api.py -q`

- [ ] **Step 5: 提交** — `git add -f tests/test_web050_story_config.py && git add kiosk_server/config.py && git commit -m "feat(web): web-050 故事绘本——KioskConfig 配置族（KIOSK_STORY_*，默认零变化）"`

---

### Task 2: 故事意图正则（web-051）

**Files:**
- Create: `kiosk_server/story.py`（本轮仅意图部分）
- Test: `tests/test_web051_story_intent.py`

**Interfaces:**
- Produces: `parse_story_intent(text: str) -> str | None`（Task 8 的 VoiceSession.ask 唯一拦截点）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web051_story_intent.py
# web-051：故事意图薄层正则（宁漏勿抢：误判抢问答=事故，漏判进问答=无害）
from kiosk_server.story import parse_story_intent


class TestStoryIntent:
    def test_hit_patterns(self):
        assert parse_story_intent("给我讲一个霸王别姬的故事") == "霸王别姬"
        assert parse_story_intent("讲个嫦娥奔月的故事") == "嫦娥奔月"
        assert parse_story_intent("说一段后羿射日的故事吧") == "后羿射日"
        assert parse_story_intent("请给我讲一个三只小猪的故事") == "三只小猪"
        assert parse_story_intent("我想听孙悟空三打白骨精的故事") == "孙悟空三打白骨精"
        assert parse_story_intent("讲一个小红帽的绘本") == "小红帽"

    def test_miss_returns_none(self):
        assert parse_story_intent("讲个故事") is None            # 无主题不触发
        assert parse_story_intent("讲一下故事") is None
        assert parse_story_intent("霸王别姬是谁") is None         # 无讲/说动词
        assert parse_story_intent("图书馆几点关门") is None
        assert parse_story_intent("这个故事讲了什么") is None
        assert parse_story_intent("") is None
        assert parse_story_intent("   ") is None

    def test_theme_cleanup(self):
        assert parse_story_intent("给我讲一个 太空 的故事！") == "太空"
        assert parse_story_intent("讲一个龟兔赛跑的故事。") == "龟兔赛跑"
```

- [ ] **Step 2: 跑测试确认失败** — `python -m pytest tests/test_web051_story_intent.py -q`，预期 ImportError。

- [ ] **Step 3: 实现**（`kiosk_server/story.py` 首版）

```python
"""AI 故事绘本（web-050 起）：意图/脚本/插图/缓存/编排。冻结内核零改动。"""
from __future__ import annotations

import re

# web-051：薄层拦截（宁漏勿抢）——前缀客套词 + 讲/说 + (一)?(个|段)? + 主题 + 的? + 故事|绘本
_PREFIX_RE = re.compile(r"^(?:请|请你|给我|给我们|你来|帮我|我想让你)+")
_STORY_RE = re.compile(r"(?:讲|说)(?:一)?(?:个|段)?(.+?)(?:的)?(?:故事|绘本)[吧吗呢啊呀！!。.~]*$")
# 「我想听/我要听 X 的故事」无讲/说动词——锚定分支（Task 2 实施实测修正：
# 前缀剥离后无动词永不命中，测试为权威）；「我想听故事」无主题仍不触发。
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
```

- [ ] **Step 4: 跑测试确认通过** — `python -m pytest tests/test_web051_story_intent.py -q`

- [ ] **Step 5: 提交** — `git add -f tests/test_web051_story_intent.py && git add kiosk_server/story.py && git commit -m "feat(web): web-051 故事绘本——意图正则（宁漏勿抢，无主题不触发）"`

---

### Task 3: ScriptClient 分镜脚本（web-052）

**Files:**
- Modify: `kiosk_server/story.py`
- Test: `tests/test_web052_story_script.py`

**Interfaces:**
- Consumes: Task 1 配置（构造参数传入，不读 cfg）。
- Produces: `StoryScriptError(code="script_failed")`、`StoryModerationError(code="moderation")`、`ScriptClient.generate(theme)->dict`、`SCRIPT_PROMPT`（冒烟 A/B 复用）。script dict：`{"title","characters","scenes"}`。

- [ ] **Step 1: 写失败测试**（dashscope 全 mock）

```python
# tests/test_web052_story_script.py
# web-052：分镜脚本——JSON 解析/校验/重试 1 次/确定性钳制/审核与失败分类
import json
import pytest
from kiosk_server import story
from kiosk_server.story import ScriptClient, StoryModerationError, StoryScriptError


def _ok_rsp(payload: dict):
    class R:
        status_code = 200
        output = type("O", (), {"choices": [
            type("C", (), {"message": type("M", (), {
                "content": json.dumps(payload, ensure_ascii=False)})})]})
    return R()


def _script(n=9, chars=30):
    return {"title": "霸王别姬", "characters": "虞姬：年轻女子，梳高髻，穿红色戏服",
            "scenes": ["第%d幕。" % i + "x" * chars for i in range(n)]}


class TestScriptGenerate:
    def test_parses_fenced_json(self, monkeypatch):
        calls = []
        def fake_call(**kw):
            calls.append(kw)
            return _ok_rsp(_script())
        monkeypatch.setattr(story, "_generation_call", fake_call)
        s = ScriptClient("qwen-plus", 1600, 60).generate("霸王别姬")
        assert s["title"] == "霸王别姬" and len(s["scenes"]) == 9
        assert "虞姬" in s["characters"]
        assert calls[0]["max_tokens"] == 1600 and calls[0]["model"] == "qwen-plus"

    def test_retry_once_on_bad_json(self, monkeypatch):
        seq = [ValueError("bad json"), _ok_rsp(_script())]
        def fake_call(**kw):
            r = seq.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        monkeypatch.setattr(story, "_generation_call", fake_call)
        s = ScriptClient("qwen-plus", 1600, 60).generate("t")
        assert len(s["scenes"]) == 9

    def test_clamp_overlong_scene_and_count(self, monkeypatch):
        bad = _script(n=12, chars=120)     # 12 段且每段超 80 字
        monkeypatch.setattr(story, "_generation_call", lambda **kw: _ok_rsp(bad))
        s = ScriptClient("qwen-plus", 1600, 60).generate("t")
        assert len(s["scenes"]) == 10                       # 段数切 10
        assert all(len(x) <= 80 for x in s["scenes"])       # 句边界截 80

    def test_too_few_scenes_fails(self, monkeypatch):
        monkeypatch.setattr(story, "_generation_call", lambda **kw: _ok_rsp(_script(n=3)))
        with pytest.raises(StoryScriptError):
            ScriptClient("qwen-plus", 1600, 60).generate("t")

    def test_moderation_classified(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError("data inspection failed: content filter")
        monkeypatch.setattr(story, "_generation_call", boom)
        with pytest.raises(StoryModerationError):
            ScriptClient("qwen-plus", 1600, 60).generate("t")

    def test_prompt_rules(self):
        p = story.SCRIPT_SYSTEM_PROMPT
        for kw in ("儿童", "8", "10", "80", "JSON", "characters", "健康"):
            assert kw in p
```

- [ ] **Step 2: 跑测试确认失败** — 预期 ImportError/AttributeError。

- [ ] **Step 3: 实现**（追加到 `kiosk_server/story.py`）

```python
import json
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

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
        # 注意：不能用 with 块——超时后 __exit__ 的 shutdown(wait=True) 会卡在
        # 挂死的 LLM 调用上，使超时失效（Task 3 实施实测修正）。
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
```

- [ ] **Step 4: 跑测试确认通过** — `python -m pytest tests/test_web052_story_script.py -q`

- [ ] **Step 5: 提交** — `git add -f tests/test_web052_story_script.py && git add kiosk_server/story.py && git commit -m "feat(web): web-052 故事绘本——qwen-plus 分镜脚本（JSON 校验/重试/钳制/审核分类）"`

---

### Task 4: ImageClient 插图生成（web-053）

**Files:**
- Modify: `kiosk_server/story.py`
- Test: `tests/test_web053_story_image.py`

**Interfaces:**
- Consumes: `StoryImageError`。
- Produces: `build_image_prompt(characters, scene)->str`、`ImageClient.generate_to(path, prompt)->bool`（内部重试 1 次，单次 ≤timeout_s，线程超时包裹）。`IMAGE_STYLE_PREFIX`/`IMAGE_NEGATIVE_SUFFIX` 常量（冒烟 A/B 复用）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web053_story_image.py
# web-053：插图客户端——prompt 模板/严格遵循（prompt_extend=False）/重试 1 次/超时/落盘
import pytest
from kiosk_server import story
from kiosk_server.story import ImageClient, build_image_prompt


def _img_rsp(url="https://oss.example/p.png"):
    class R:
        status_code = 200
        output = type("O", (), {"choices": [
            type("C", (), {"message": type("M", (), {
                "content": [{"image": url}]})})]})
    return R()


class TestBuildPrompt:
    def test_assembly(self):
        p = build_image_prompt("虞姬：高髻红衣", "虞姬在帐中舞剑")
        assert "虞姬：高髻红衣" in p and "虞姬在帐中舞剑" in p
        assert "绘本" in p and "文字" in p          # 风格锚 + 负向约束
    def test_no_characters(self):
        p = build_image_prompt("", "森林里的小鹿")
        assert "森林里的小鹿" in p


class TestGenerateTo:
    def test_success_downloads(self, monkeypatch, tmp_path):
        monkeypatch.setattr(story, "_mmconversation_call", lambda **kw: _img_rsp())
        monkeypatch.setattr(story, "_download", lambda url, path: path.write_bytes(b"PNG"))
        ok = ImageClient("qwen-image-3.0", "1024*1024", 90).generate_to(
            tmp_path / "page_1.png", "prompt")
        assert ok and (tmp_path / "page_1.png").read_bytes() == b"PNG"

    def test_params_pinned(self, monkeypatch, tmp_path):
        seen = {}
        def fake(**kw):
            seen.update(kw)
            return _img_rsp()
        monkeypatch.setattr(story, "_mmconversation_call", fake)
        monkeypatch.setattr(story, "_download", lambda u, p: p.write_bytes(b"x"))
        ImageClient("qwen-image-3.0", "1024*1024", 90).generate_to(tmp_path / "a.png", "p")
        assert seen["model"] == "qwen-image-3.0"
        assert seen["prompt_extend"] is False and seen["size"] == "1024*1024"
        assert seen["messages"][0]["content"][0]["text"] == "p"

    def test_retry_once_then_fail(self, monkeypatch, tmp_path):
        calls = []
        def boom(**kw):
            calls.append(1)
            raise RuntimeError("oss 抖动")
        monkeypatch.setattr(story, "_mmconversation_call", boom)
        ok = ImageClient("qwen-image-3.0", "1024*1024", 90).generate_to(tmp_path / "a.png", "p")
        assert ok is False and len(calls) == 2       # 重试 1 次后放弃（不抛）

    def test_retry_once_then_success(self, monkeypatch, tmp_path):
        seq = [RuntimeError("x"), _img_rsp()]
        def flaky(**kw):
            r = seq.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        monkeypatch.setattr(story, "_mmconversation_call", flaky)
        monkeypatch.setattr(story, "_download", lambda u, p: p.write_bytes(b"x"))
        assert ImageClient("m", "s", 90).generate_to(tmp_path / "a.png", "p") is True
```

- [ ] **Step 2: 跑测试确认失败** — 预期 AttributeError。

- [ ] **Step 3: 实现**（追加到 `kiosk_server/story.py`）

```python
from pathlib import Path

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
```

- [ ] **Step 4: 跑测试确认通过** — `python -m pytest tests/test_web053_story_image.py -q`

- [ ] **Step 5: 提交** — `git add -f tests/test_web053_story_image.py && git add kiosk_server/story.py && git commit -m "feat(web): web-053 故事绘本——qwen-image-3.0 插图客户端（prompt 模板/重试 1 次/落盘）"`

---

### Task 5: StoryCache 同名故事缓存（web-054）

**Files:**
- Modify: `kiosk_server/story.py`
- Test: `tests/test_web054_story_cache.py`

**Interfaces:**
- Produces: `StoryCache`（签名见头部约定）。meta.json：`{"theme","title","characters","scenes":[str],"created","last_access"}`。命中条件 = meta.json 存在且 scenes 非空（图片缺失容忍，Task 6 补生成）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web054_story_cache.py
# web-054：同名故事缓存——主题归一化/落盘命中/500MB LRU 整故事淘汰
import json
from kiosk_server.story import StoryCache


class TestStoryCache:
    def test_story_id_normalized(self):
        assert StoryCache.story_id("霸王别姬") == StoryCache.story_id(" 霸王别姬！")
        assert StoryCache.story_id("霸王别姬") != StoryCache.story_id("嫦娥奔月")

    def test_save_and_load(self, tmp_path):
        c = StoryCache(str(tmp_path), 500)
        sid = c.save("霸王别姬", {"title": "霸王别姬", "characters": "虞姬",
                                  "scenes": ["a", "b"]})
        hit = c.load(" 霸王别姬。")
        assert hit and hit["id"] == sid and hit["scenes"] == ["a", "b"]
        assert (tmp_path / sid / "meta.json").exists()

    def test_miss(self, tmp_path):
        assert StoryCache(str(tmp_path), 500).load("不存在") is None

    def test_image_path(self, tmp_path):
        c = StoryCache(str(tmp_path), 500)
        assert c.image_path("abc", 3).name == "page_3.png"

    def test_lru_eviction(self, tmp_path):
        c = StoryCache(str(tmp_path), 1)          # 1MB 上限
        for i in range(3):
            sid = c.save(f"主题{i}", {"title": "t", "characters": "", "scenes": ["x"]})
            (tmp_path / sid / "page_1.png").write_bytes(b"x" * 600 * 1024)
            c.save_meta_touch(sid, last_access=float(i))   # 测试注入访问时刻
        c.evict_if_needed()
        remaining = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
        assert remaining == [StoryCache.story_id("主题2")]   # 最旧两个被淘汰
```

- [ ] **Step 2: 跑测试确认失败** — 预期 AttributeError。

- [ ] **Step 3: 实现**（追加到 `kiosk_server/story.py`）

```python
import hashlib
import time


def _normalize_theme(theme: str) -> str:
    return re.sub(r"[\s，。！？、,.!?~…·]+", "", theme or "")


class StoryCache:
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
```

- [ ] **Step 4: 跑测试确认通过** — `python -m pytest tests/test_web054_story_cache.py -q`

- [ ] **Step 5: 提交** — `git add -f tests/test_web054_story_cache.py && git add kiosk_server/story.py && git commit -m "feat(web): web-054 故事绘本——同名故事缓存（归一化/落盘/500MB LRU）"`

---

### Task 6: StorySession 启动链路（web-055）

**Files:**
- Modify: `kiosk_server/story.py`
- Test: `tests/test_web055_story_session_start.py`

**Interfaces:**
- Consumes: Task 1~5 全部；`BroadcastSession`（`kiosk_server/chat.py`，**不改它**——StorySession 用注入的 pipeline 实例化自己的 BroadcastSession）。
- Produces: `StorySession`（签名见头部约定）中 start 的前半段：preparing→cache/script→story_begin→插图编排→`story_page_img`；`active` 属性；`_speak` 由 Task 7 补全（本任务用 `speak_fn` 注入桩隔离）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web055_story_session_start.py
# web-055：StorySession 启动链路——preparing→begin（缓存命中/新生成）→逐图事件
import threading
from kiosk_server import story
from kiosk_server.story import StoryCache, StoryScriptError, StorySession


class _Cfg:
    story_min_scenes = 8; story_max_scenes = 10; story_scene_max_chars = 80
    story_image_concurrency = 4; story_total_budget_s = 300.0
    story_closing = "故事讲完啦，还想听什么故事吗？"


class _FakeScript:
    def __init__(self, script=None, err=None):
        self._script, self._err, self.calls = script, err, []
    def generate(self, theme):
        self.calls.append(theme)
        if self._err:
            raise self._err
        return self._script


def _script(n=8):
    return {"title": "霸王别姬", "characters": "虞姬",
            "scenes": [f"第{i}幕。" for i in range(1, n + 1)]}


class _FakeImage:
    def __init__(self, ok=True):
        self.ok, self.prompts, self._lock = ok, [], threading.Lock()
    def generate_to(self, path, prompt):
        with self._lock:
            self.prompts.append(prompt)
        if self.ok:
            from pathlib import Path
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"PNG")
        return self.ok


def _make(events, script=None, img=None, tmp_path=None, speak=None):
    cache = StoryCache(str(tmp_path), 500)
    s = StorySession(events.append, script or _FakeScript(_script()),
                     img or _FakeImage(), cache, tts_factory=None, cfg=_Cfg(),
                     speak_fn=speak or (lambda n: None))
    return s, cache


def _types(events):
    return [e["type"] for e in events]


class TestStart:
    def test_fresh_flow(self, tmp_path):
        events = []
        s, _ = _make(events, tmp_path=tmp_path)
        threading.Thread(target=s.start, args=("霸王别姬",), daemon=True).start()
        s.wait_idle(5.0)
        t = _types(events)
        assert t[0] == "story_preparing" and events[0]["theme"] == "霸王别姬"
        begin = events[t.index("story_begin")]
        assert begin["total"] == 8 and begin["cached"] is False
        assert begin["pages"][0] == {"n": 1, "text": "第1幕。"}
        imgs = [e for e in events if e["type"] == "story_page_img"]
        assert len(imgs) == 8
        assert all(e["url"].startswith(f"/api/story/{begin['story_id']}/img/") for e in imgs)
        assert events[-1]["type"] == "story_end" and events[-1]["reason"] == "done"

    def test_cached_replay_skips_llm_and_images(self, tmp_path):
        events = []
        s, cache = _make(events, tmp_path=tmp_path)
        threading.Thread(target=s.start, args=("霸王别姬",), daemon=True).start()
        s.wait_idle(5.0)
        # 第二轮：同主题 → 命中缓存（LLM 不再调用、图片全已在盘 → 直接发 img 事件）
        script2 = _FakeScript(_script())
        img2 = _FakeImage()
        events2 = []
        s2, _ = _make(events2, script=script2, img=img2, tmp_path=tmp_path)
        threading.Thread(target=s2.start, args=(" 霸王别姬！",), daemon=True).start()
        s2.wait_idle(5.0)
        assert script2.calls == []                       # 跳 LLM
        assert img2.prompts == []                        # 已落盘的图跳生成
        begin = [e for e in events2 if e["type"] == "story_begin"][0]
        assert begin["cached"] is True
        assert len([e for e in events2 if e["type"] == "story_page_img"]) == 8

    def test_partial_cache_backfills_missing_images(self, tmp_path):
        events = []
        s, cache = _make(events, tmp_path=tmp_path)
        threading.Thread(target=s.start, args=("霸王别姬",), daemon=True).start()
        s.wait_idle(5.0)
        sid = StoryCache.story_id("霸王别姬")
        cache.image_path(sid, 3).unlink()                # 制造缺图缓存
        img2 = _FakeImage()
        events2 = []
        s2, _ = _make(events2, img=img2, tmp_path=tmp_path)
        threading.Thread(target=s2.start, args=("霸王别姬",), daemon=True).start()
        s2.wait_idle(5.0)
        assert len(img2.prompts) == 1                    # 只补第 3 页

    def test_failed_image_no_img_event(self, tmp_path):
        events = []
        img = _FakeImage(ok=False)
        s, _ = _make(events, img=img, tmp_path=tmp_path)
        threading.Thread(target=s.start, args=("霸王别姬",), daemon=True).start()
        s.wait_idle(5.0)
        assert not [e for e in events if e["type"] == "story_page_img"]
        assert [e for e in events if e["type"] == "story_end"]   # 照常讲完

    def test_script_failure_emits_error(self, tmp_path):
        events = []
        s, _ = _make(events, script=_FakeScript(err=StoryScriptError("x")),
                     tmp_path=tmp_path)
        s.start("霸王别姬")
        t = _types(events)
        assert "story_error" in t and events[t.index("story_error")]["code"] == "script_failed"
        assert "story_begin" not in t

    def test_image_prompt_carries_characters(self, tmp_path):
        events = []
        img = _FakeImage()
        s, _ = _make(events, img=img, tmp_path=tmp_path)
        threading.Thread(target=s.start, args=("霸王别姬",), daemon=True).start()
        s.wait_idle(5.0)
        assert all("虞姬" in p for p in img.prompts)     # 跨图一致性：角色锚定每张携带
```

- [ ] **Step 2: 跑测试确认失败** — 预期 TypeError（StorySession 不存在/签名不符）。

- [ ] **Step 3: 实现**（追加到 `kiosk_server/story.py`）

```python
import queue
import threading
from typing import Callable

from .chat import BroadcastSession
from .tts_clean import clean_for_broadcast    # noqa: F401  （Task 7 播报路径使用）


class _StoryPagePipeline:
    """当页文本即「问题」：BroadcastSession 原编排零改动复用（web-055）。"""
    def query_stream(self, question, conversation_history=None):
        yield question


class StorySession:
    def __init__(self, emit, script_client, image_client, cache, tts_factory, cfg, *,
                 clock=time.monotonic, speak_fn: Callable[[int], None] | None = None):
        self._emit = emit
        self._script = script_client
        self._image = image_client
        self._cache = cache
        self._tts_factory = tts_factory
        self._cfg = cfg
        self._clock = clock
        self._speak_fn = speak_fn                  # Task 7 缺省 None → 内部播报实现
        self._cmd: queue.Queue = queue.Queue()
        self._active = threading.Event()
        self._cancel = threading.Event()
        self._img_done = threading.Event()
        self._pages: list[dict] = []
        self._sid = ""
        self._title = ""
        self._characters = ""

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
        self._cmd.put(("cancel", None))

    def close(self) -> None:
        self.cancel()

    # ---------- 主流程（调用方线程阻塞执行） ----------

    def start(self, theme: str) -> None:
        self._active.set()
        try:
            self._emit({"type": "story_preparing", "theme": theme})
            cached = self._cache.load(theme)
            if cached:
                sid, title, characters, scenes, is_cached = (
                    cached["id"], cached["title"], cached.get("characters", ""),
                    cached["scenes"], True)
            else:
                try:
                    script = self._script.generate(theme)
                except StoryScriptError as e:
                    self._emit({"type": "story_error",
                                "code": getattr(e, "code", "script_failed"),
                                "message": "这个故事我不太会讲，换一个试试吧"})
                    self._emit({"type": "story_end", "reason": "error"})
                    return
                sid = self._cache.save(theme, script)
                title, characters, scenes, is_cached = (
                    script["title"], script.get("characters", ""), script["scenes"], False)
            self._sid, self._title, self._characters = sid, title, characters
            self._pages = [{"n": i + 1, "text": t} for i, t in enumerate(scenes)]
            self._emit({"type": "story_begin", "story_id": sid, "title": title,
                        "total": len(self._pages), "cached": is_cached,
                        "pages": self._pages})
            self._start_image_workers()
            self._command_loop()
        finally:
            self._cancel.set()                     # 通知插图线程收尾
            self._active.clear()

    # ---------- 插图编排：页序提交、并发受限、预算兜底 ----------

    def _start_image_workers(self) -> None:
        sem = threading.Semaphore(self._cfg.story_image_concurrency)
        deadline = self._clock() + self._cfg.story_total_budget_s

        def worker(page: dict) -> None:
            n = page["n"]
            path = self._cache.image_path(self._sid, n)
            with sem:
                if self._cancel.is_set() or self._clock() > deadline:
                    return
                if path.exists():
                    ok = True                       # 缓存/补生成跳过
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    ok = self._image.generate_to(
                        path, build_image_prompt(self._characters, page["text"]))
            if ok and not self._cancel.is_set():
                self._emit({"type": "story_page_img", "n": n,
                            "url": f"/api/story/{self._sid}/img/{n}"})

        def run_all() -> None:
            threads = [threading.Thread(target=worker, args=(p,), daemon=True)
                       for p in self._pages]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self._img_done.set()

        threading.Thread(target=run_all, daemon=True).start()

    # ---------- 指令循环（Task 7 补播报动作；本任务 finish/cancel 仅收尾事件） ----------

    def _command_loop(self) -> None:
        while True:
            kind, payload = self._cmd.get()
            if kind == "cancel":
                self._emit({"type": "story_end", "reason": "cancelled"})
                break
            if kind == "finish":
                self._emit({"type": "story_end", "reason": "done"})
                break
            if kind == "page" and self._speak_fn and 1 <= payload <= len(self._pages):
                self._speak_fn(payload)
```

- [ ] **Step 4: 跑测试确认通过** — `python -m pytest tests/test_web055_story_session_start.py -q`
  （实施注意：测试线程时序统一为 `threading.Thread(target=s.start, ...).start()` →
  `time.sleep(0.05)` 让启动链路先跑 → `s.on_finish()` → `s.wait_idle(5.0)` 后断言；
  `story_end{done}` 由本任务 `_command_loop` 的 finish 分支发出，Task 7 再扩展为
  「播收尾语→播尽→story_end」。）

- [ ] **Step 5: 提交** — `git add -f tests/test_web055_story_session_start.py && git add kiosk_server/story.py && git commit -m "feat(web): web-055 故事绘本——StorySession 启动链路（缓存命中/插图并发编排/逐图事件）"`

---

### Task 7: StorySession 播报状态机（web-056）

**Files:**
- Modify: `kiosk_server/story.py`
- Test: `tests/test_web056_story_speak.py`

**Interfaces:**
- Consumes: Task 6 全部 + `BroadcastSession`（注入 `StoryPagePipeline` 实例 + tts_factory + 包装 emit）。
- Produces: `_speak(n)` 内部实现（`speak_fn` 缺省时的真身）：事件改名 `answer_start→story_speak_start{n}`、`answer_chunk→抑制`、`answer_end→story_speak_end{n,cancelled}`、`audio*/playback_cancel→透传`；翻页串行化=直接 `broadcast.ask`（web-029 语义原生继承）；finish=播收尾语+`story_end{done}`；cancel=barge+`story_end{cancelled}`。

- [ ] **Step 1: 写失败测试**（真 BroadcastSession + 假 TTS，对齐既有 chat 测试范式）

```python
# tests/test_web056_story_speak.py
# web-056：绘本播报状态机——逐页 speak/事件改名/翻页即切/收尾/cancel
import threading
import time
from kiosk_server.story import StorySession, StoryCache


class _Cfg:
    story_min_scenes = 8; story_max_scenes = 10; story_scene_max_chars = 80
    story_image_concurrency = 4; story_total_budget_s = 300.0
    story_closing = "故事讲完啦，还想听什么故事吗？"


class _FakeTTSHandle:
    def __init__(self, on_audio):
        self._on_audio = on_audio
        self.error = None
        self.done = threading.Event()
        self.fed = []
    def feed(self, text):
        self.fed.append(text)
        self._on_audio(b"\x01\x02" * 480)      # 每喂入回 20ms PCM
    def finish(self):
        self.done.set()
    def cancel(self):
        self.done.set()


class _FakeTTS:
    def __init__(self):
        self.handles = []
    def start_stream(self, on_audio):
        h = _FakeTTSHandle(on_audio)
        self.handles.append(h)
        return h


class _FakeScript:
    def generate(self, theme):
        return {"title": "t", "characters": "", "scenes": [f"第{i}幕。" for i in range(1, 9)]}


class _NoopImage:
    def generate_to(self, path, prompt):
        return False                              # 本任务不关心图


def _start(events, tmp_path, tts):
    s = StorySession(events.append, _FakeScript(), _NoopImage(),
                     StoryCache(str(tmp_path), 500), tts_factory=lambda: _FakeTTS(),
                     cfg=_Cfg())
    th = threading.Thread(target=s.start, args=("霸王别姬",), daemon=True)
    th.start()
    return s, th


def _wait(events, pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred(events):
            return True
        time.sleep(0.01)
    return False


class TestSpeak:
    def test_page1_auto_speak_and_events(self, tmp_path):
        events = []
        s, th = _start(events, tmp_path, _FakeTTS())
        assert _wait(events, lambda e: any(x["type"] == "story_speak_start" and x["n"] == 1 for x in e))
        assert _wait(events, lambda e: any(x["type"] == "story_speak_end" and x["n"] == 1
                                           and x["cancelled"] is False for x in e))
        assert any(x["type"] == "audio_start" for x in events)        # 音频事件透传
        assert not any(x["type"] == "answer_chunk" for x in events)   # chunk 抑制
        s.cancel(); th.join(3)

    def test_manual_flip_barges_current_page(self, tmp_path):
        events = []
        s, th = _start(events, tmp_path, _FakeTTS())
        assert _wait(events, lambda e: any(x["type"] == "story_speak_start" for x in e))
        s.on_page(3)
        assert _wait(events, lambda e: any(x["type"] == "story_speak_start" and x["n"] == 3 for x in e))
        starts = [x["n"] for x in events if x["type"] == "story_speak_start"]
        assert starts[-1] == 3 and 2 not in starts        # 直达第 3 页，无中间页
        s.cancel(); th.join(3)

    def test_finish_speaks_closing_then_end(self, tmp_path):
        events = []
        s, th = _start(events, tmp_path, _FakeTTS())
        assert _wait(events, lambda e: any(x["type"] == "story_speak_start" for x in e))
        s.on_finish()
        assert _wait(events, lambda e: any(x["type"] == "story_end" and x["reason"] == "done" for x in e))
        fed = "".join(seg for h in s._tts.handles for seg in h.fed)
        assert "故事讲完啦" in fed
        th.join(3)

    def test_cancel_stops_and_emits(self, tmp_path):
        events = []
        s, th = _start(events, tmp_path, _FakeTTS())
        assert _wait(events, lambda e: any(x["type"] == "story_speak_start" for x in e))
        s.cancel()
        assert _wait(events, lambda e: any(x["type"] == "story_end"
                                           and x["reason"] == "cancelled" for x in e))
        th.join(3)

    def test_out_of_range_page_ignored(self, tmp_path):
        events = []
        s, th = _start(events, tmp_path, _FakeTTS())
        assert _wait(events, lambda e: any(x["type"] == "story_speak_start" for x in e))
        s.on_page(99)
        time.sleep(0.2)
        assert not any(x["type"] == "story_speak_start" and x["n"] == 99 for x in events)
        s.cancel(); th.join(3)
```

- [ ] **Step 2: 跑测试确认失败** — 预期 AttributeError（`s._tts` / speak 未实现）。

- [ ] **Step 3: 实现**（`StorySession` 增补：构造中 `self._tts = None`、`_speak`、改写 `_command_loop` 与 `_start_image_workers` 之后挂播报；`speak_fn` 参数删除——Task 6 测试改用真实现但注入假 TTS 同样通过，`speak_fn` 保留作可选覆盖以兼容 Task 6 测试桩）

```python
    # ---------- 播报（复用 BroadcastSession：句边界/清洗/看门狗/打断串行化） ----------

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
        if self._tts is None:
            self._tts = self._make_broadcast()
        self._speaking_page = n
        body = text if text is not None else self._pages[n - 1]["text"]
        threading.Thread(target=self._tts.ask, args=(body,), daemon=True).start()
```

`_command_loop` 改写：

```python
    def _command_loop(self) -> None:
        reason = "done"
        self._speak(1)                               # story_begin 后自动开播第 1 页
        while True:
            kind, payload = self._cmd.get()
            if kind == "cancel":
                reason = "cancelled"
                break
            if kind == "finish":
                self._speak(0, self._cfg.story_closing)
                self._wait_speak_done(30.0)          # 收尾语播尽再发 story_end
                break
            if kind == "page" and 1 <= payload <= len(self._pages):
                if payload != self._speaking_page or not self._tts.busy:
                    self._speak(payload)
        if reason == "cancelled":
            self._tts.barge_in()
            self._wait_speak_done(5.0)
        self._emit({"type": "story_end", "reason": reason})

    def _wait_speak_done(self, timeout: float) -> None:
        end = self._clock() + timeout
        while self._tts is not None and self._tts.busy and self._clock() < end:
            time.sleep(0.05)
```

（`start()` 的 finally 追加：`if self._tts is not None: self._tts.close()`；
`story_error` 路径在 emit error 后 `self._emit({"type":"story_end","reason":"error"})` 已有。）

- [ ] **Step 4: 跑测试确认通过 + Task 6 回归** — `python -m pytest tests/test_web055_story_session_start.py tests/test_web056_story_speak.py -q`

- [ ] **Step 5: 提交** — `git add -f tests/test_web056_story_speak.py && git add kiosk_server/story.py && git commit -m "feat(web): web-056 故事绘本——播报状态机（逐页 speak/翻页即切/收尾/cancel）"`

---

### Task 8: VoiceSession 集成——意图拦截 + 故事态语音静默（web-057）

**Files:**
- Modify: `kiosk_server/voice.py`
- Test: `tests/test_web057_story_voice.py`

**Interfaces:**
- Consumes: `parse_story_intent`、`StorySession`。
- Produces: `VoiceSession.set_story_session(factory)`（factory(emit_wrapper)->StorySession）、`on_story_page/on_story_finish/on_story_cancel`；故事态行为：`feed_audio` 全静默、`barge_in` 忽略、`ask` 命中正则转故事/非故事文本防御性 cancel 后照常问答。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web057_story_voice.py
# web-057：VoiceSession 故事集成——ask 单点拦截/故事态帧静默/barge 忽略/退出复原
import threading
import time
from kiosk_server.voice import VoiceSession


class _FakePipeline:
    def query_stream(self, question, conversation_history=None):
        yield "问答答案。"


class _FakeStory:
    def __init__(self, emit):
        self.emit, self.started, self.cancelled = emit, [], 0
        self.active = False
    def start(self, theme):
        self.active = True
        self.started.append(theme)
        self.emit({"type": "story_preparing", "theme": theme})
        self.emit({"type": "story_end", "reason": "done"})
        self.active = False
    def on_page(self, n): pass
    def on_finish(self): pass
    def cancel(self):
        self.cancelled += 1
        self.active = False
    def close(self): pass


def _make(events):
    vs = VoiceSession(_FakePipeline(), None, None, events.append,
                      greeting_pcm_fn=None, sync_audio=True)
    story = _FakeStory(events.append)
    vs.set_story_session(lambda emit: story)
    return vs, story


class TestStoryRouting:
    def test_story_intent_routes_to_story(self):
        events = []
        vs, story = _make(events)
        vs.ask("给我讲一个霸王别姬的故事")
        assert story.started == ["霸王别姬"]
        assert any(e["type"] == "story_preparing" for e in events)
        assert not any(e["type"] == "answer_start" for e in events)   # 未进问答

    def test_normal_question_untouched(self):
        events = []
        vs, story = _make(events)
        vs.ask("图书馆几点关门")
        assert story.started == []
        assert any(e["type"] == "answer_start" for e in events)

    def test_audio_frames_dropped_in_story_mode(self):
        events = []
        vs, story = _make(events)
        vs.set_story_mode(True)
        vs.feed_audio(b"\x00" * 640)
        assert not events                                  # assistant=None 本应报错，静默=生效
        vs.set_story_mode(False)
        vs.feed_audio(b"\x00" * 640)
        assert any(e.get("code") == "voice_unavailable" for e in events)

    def test_barge_ignored_in_story_mode(self):
        events = []
        vs, story = _make(events)
        vs.set_story_mode(True)
        vs.barge_in()
        assert story.cancelled == 0                        # 不级联取消故事

    def test_ask_during_story_defensive_cancel(self):
        events = []
        vs, story = _make(events)
        vs._story = story    # 模拟故事实例在进行（Task 8 实施修正：brief 原测试缺此线必失败）
        vs.set_story_mode(True)
        story.active = True
        vs.ask("图书馆几点关门")                           # 非故事文本 → 取消故事后照常问答
        assert story.cancelled == 1
        assert any(e["type"] == "answer_start" for e in events)
```

- [ ] **Step 2: 跑测试确认失败** — 预期 AttributeError。

- [ ] **Step 3: 实现**（`kiosk_server/voice.py` 增补）

```python
from .story import parse_story_intent

# VoiceSession.__init__ 追加：
#     self._story_factory = None
#     self._story = None
#     self._story_mode = False

    def set_story_session(self, factory) -> None:
        """注入故事会话工厂（voice_ws 接线；factory(emit)->StorySession）。"""
        self._story_factory = factory

    def set_story_mode(self, on: bool) -> None:
        self._story_mode = bool(on)

    # ask 改写：
    def ask(self, text: str) -> None:
        theme = parse_story_intent(text)
        if self._story_mode:
            if self._story is not None:
                self._story.cancel()                 # 防御：旧故事先停（web-057）
            self._story_mode = False
            if theme:
                self._start_story(theme)
                return
            self._broadcast.ask(text)                # 非故事文本照常问答
            return
        if theme and self._story_factory is not None:
            self._start_story(theme)
            return
        self._broadcast.ask(text)

    def _start_story(self, theme: str) -> None:
        self._story = self._story_factory(self._on_story_event)
        self._story_mode = True
        self._story.start(theme)                     # 阻塞驱动（调用方在线程中）

    def _on_story_event(self, ev: dict) -> None:
        if ev.get("type") in ("story_end", "story_error"):
            self._story_mode = False
        self._emit(ev)

    # 故事态指令：
    def on_story_page(self, n: int) -> None:
        if self._story is not None and self._story_mode:
            self._story.on_page(n)

    def on_story_finish(self) -> None:
        if self._story is not None and self._story_mode:
            self._story.on_finish()

    def on_story_cancel(self) -> None:
        if self._story is not None and self._story_mode:
            self._story.cancel()
            self._story_mode = False

    # feed_audio 首行追加：
    #     if self._story_mode:
    #         return                                   # 故事态全静默（web-057）
    # barge_in 首行追加：
    #     if self._story_mode:
    #         logger.info("故事态忽略 barge_in（web-057）")
    #         return
```

- [ ] **Step 4: 跑测试确认通过 + 语音回归** — `python -m pytest tests/test_web057_story_voice.py tests/test_kiosk_voice.py -q`

- [ ] **Step 5: 提交** — `git add -f tests/test_web057_story_voice.py && git add kiosk_server/voice.py && git commit -m "feat(web): web-057 故事绘本——VoiceSession 集成（意图拦截/故事态静默/防御取消）"`

---

### Task 9: WS 路由 + 供图端点（web-058）

**Files:**
- Modify: `kiosk_server/voice_ws.py`、`kiosk_server/app.py`、`kiosk_server/config.py`（无新字段，复用 Task 1）
- Test: `tests/test_web058_story_ws_api.py`

**Interfaces:**
- Consumes: Task 7/8 全部；`KioskConfig.story_cache_dir`。
- Produces: WS 消息 `story_page/story_finish/story_cancel` 路由；`GET /api/story/{sid}/img/{n}`（200 PNG / 404 / token 查询参数放行）；`_default_session_factory` 组装 StorySession 全套真件。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web058_story_ws_api.py
# web-058：WS 故事路由 + 供图端点 + token 查询参数
import base64
import pytest
from fastapi.testclient import TestClient
from kiosk_server.app import create_app
from kiosk_server.config import KioskConfig


class _FakeSession:
    voice_enabled = False
    def __init__(self):
        self.pages, self.finished, self.cancelled = [], 0, 0
    def feed_audio(self, b): pass
    def ask(self, t): pass
    def barge_in(self): pass
    def on_story_page(self, n): self.pages.append(n)
    def on_story_finish(self): self.finished += 1
    def on_story_cancel(self): self.cancelled += 1
    def close(self): pass


class TestStoryWs:
    def test_routes(self):
        sess = _FakeSession()
        app = create_app(config=KioskConfig(), session_factory=lambda emit: sess)
        with TestClient(app).websocket_connect("/ws/voice") as ws:
            ws.send_json({"type": "story_page", "n": 4})
            ws.send_json({"type": "story_finish"})
            ws.send_json({"type": "story_cancel"})
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"
        assert sess.pages == [4] and sess.finished == 1 and sess.cancelled == 1


class TestStoryImageApi:
    def _app(self, tmp_path, token=""):
        cfg = KioskConfig(token=token, story_cache_dir=str(tmp_path))
        return create_app(config=cfg, session_factory=lambda emit: _FakeSession())

    def test_serve_and_404(self, tmp_path):
        (tmp_path / "sid1").mkdir()
        (tmp_path / "sid1" / "page_2.png").write_bytes(b"\x89PNG")
        c = TestClient(self._app(tmp_path))
        assert c.get("/api/story/sid1/img/2").content == b"\x89PNG"
        assert c.get("/api/story/sid1/img/9").status_code == 404
        assert c.get("/api/story/..%2F..%2Fetc/img/1").status_code in (404, 422)

    def test_token_query_param(self, tmp_path):
        (tmp_path / "s").mkdir()
        (tmp_path / "s" / "page_1.png").write_bytes(b"P")
        c = TestClient(self._app(tmp_path, token="sekret"))
        assert c.get("/api/story/s/img/1").status_code == 401
        assert c.get("/api/story/s/img/1?token=sekret").content == b"P"
        assert c.get("/api/health").status_code == 200     # health 仍免 token
```

- [ ] **Step 2: 跑测试确认失败** — 预期 404/unknown_type。

- [ ] **Step 3: 实现**

`voice_ws.py`（消息循环追加分支 + 工厂组装）：

```python
                elif mtype == "story_page":
                    session.on_story_page(data.get("n", 0))
                elif mtype == "story_finish":
                    session.on_story_finish()
                elif mtype == "story_cancel":
                    session.on_story_cancel()
```

`_default_session_factory` 的 `make` 内追加（`from .story import ScriptClient, ImageClient, StoryCache, StorySession`）：

```python
        def story_factory(emit):
            return StorySession(
                emit,
                ScriptClient(cfg.story_script_model, cfg.story_script_max_tokens,
                             cfg.story_script_timeout_s),
                ImageClient(cfg.story_image_model, cfg.story_image_size,
                            cfg.story_image_timeout_s),
                StoryCache(cfg.story_cache_dir, cfg.story_cache_max_mb),
                tts_factory, cfg)
        vs = VoiceSession(...)          # 既有构造不变
        if cfg.story_enabled:
            vs.set_story_session(story_factory)
        return vs
```

`app.py`（token 中间件放行查询参数 + 供图端点）：

```python
# _TokenMiddleware.dispatch：header 校验失败时回退 query token
        if self._token and request.url.path.startswith("/api/") \
                and request.url.path != "/api/health":
            token = request.headers.get("X-Kiosk-Token") or request.query_params.get("token")
            if token != self._token:
                return JSONResponse({"detail": "未授权"}, status_code=401)

# create_app 内新增：
    from fastapi.responses import FileResponse
    from pathlib import Path

    @app.get("/api/story/{sid}/img/{n}")
    def story_img(sid: str, n: int):
        if not sid.isalnum() or len(sid) > 32 or not (1 <= n <= 99):
            return JSONResponse({"detail": "参数非法"}, status_code=404)
        path = Path(cfg.story_cache_dir) / sid / f"page_{n}.png"
        if not path.is_file():
            return JSONResponse({"detail": "插图未就绪"}, status_code=404)
        return FileResponse(path, media_type="image/png")
```

- [ ] **Step 4: 跑测试确认通过 + API 回归** — `python -m pytest tests/test_web058_story_ws_api.py tests/test_kiosk_api.py -q`

- [ ] **Step 5: 提交** — `git add -f tests/test_web058_story_ws_api.py && git add kiosk_server/voice_ws.py kiosk_server/app.py && git commit -m "feat(web): web-058 故事绘本——WS 路由 + 供图端点（token 查询参数）"`

---

### Task 10: VoiceWsClient 故事方法（web-059）

**Files:**
- Modify: `frontend/src/voice/VoiceWsClient.ts`
- Test: `frontend/tests/storyWs.test.ts`

**Interfaces:**
- Produces: `client.storyPage(n:number)`、`client.storyFinish()`、`client.storyCancel()`（Task 11 消费）；事件本就泛型透传（测试固化 story_* 事件直达 onEvent 零改动）。

- [ ] **Step 1: 写失败测试**（范式照抄 `voiceWs.test.ts` 的假 WebSocket）

```ts
// frontend/tests/storyWs.test.ts
// web-059：VoiceWsClient 故事消息发送 + story_* 事件泛型透传
import { describe, expect, it, vi } from "vitest";
import { VoiceWsClient } from "../src/voice/VoiceWsClient";

class FakeWs {
  static last: FakeWs;
  sent: any[] = [];
  readyState = 1;
  onopen: any; onmessage: any; onclose: any;
  constructor(public url: string) { FakeWs.last = this; }
  send(d: any) { this.sent.push(typeof d === "string" ? JSON.parse(d) : d); }
  close() {}
}
(globalThis as any).WebSocket = FakeWs;

function make() {
  const events: any[] = [];
  const c = new VoiceWsClient({ onEvent: (e) => events.push(e) });
  c.connect();
  FakeWs.last.onopen?.({});
  FakeWs.last.onmessage?.({ data: JSON.stringify({ type: "hello", ok: true, voice: false }) });
  return { c, events };
}

describe("web-059 story ws", () => {
  it("sends story messages", () => {
    const { c } = make();
    c.storyPage(3); c.storyFinish(); c.storyCancel();
    const types = FakeWs.last.sent.map((m) => `${m.type}${m.n ?? ""}`);
    expect(types).toContain("story_page3");
    expect(types).toContain("story_finish");
    expect(types).toContain("story_cancel");
  });

  it("passes story events through untouched", () => {
    const { events } = make();
    const ev = { type: "story_begin", story_id: "x", total: 8, pages: [] };
    FakeWs.last.onmessage?.({ data: JSON.stringify(ev) });
    expect(events).toContainEqual(ev);
  });
});
```

- [ ] **Step 2: 跑测试确认失败** — `cd frontend && npx vitest run tests/storyWs.test.ts`，预期 TypeError（方法不存在）。

- [ ] **Step 3: 实现**（`VoiceWsClient.ts` 追加）

```ts
  storyPage(n: number): void {
    this.send({ type: "story_page", n });
  }

  storyFinish(): void {
    this.send({ type: "story_finish" });
  }

  storyCancel(): void {
    this.send({ type: "story_cancel" });
  }
```

- [ ] **Step 4: 跑测试确认通过 + 回归** — `cd frontend && npx vitest run tests/storyWs.test.ts tests/voiceWs.test.ts`

- [ ] **Step 5: 提交** — `git add -f frontend/tests/storyWs.test.ts && git add frontend/src/voice/VoiceWsClient.ts && git commit -m "feat(web): web-059 故事绘本——VoiceWsClient 故事消息"`

---

### Task 11: useStorySession 组合件（web-060）

**Files:**
- Create: `frontend/src/voice/useStorySession.ts`
- Test: `frontend/tests/storySession.test.ts`

**Interfaces:**
- Consumes: Task 10 client 方法 + `PcmPlayer`（`onEnded`/`stop`/`playing`）。
- Produces:

```ts
export type StoryPhase = "idle" | "preparing" | "playing" | "finished";
export function useStorySession(deps: {
  client: VoiceWsClient; player: PcmPlayer;
  onPhaseChange?: (p: StoryPhase) => void;
}): {
  phase: Ref<StoryPhase>; title: Ref<string>; page: Ref<number>; total: Ref<number>;
  pages: Ref<{n:number;text:string}[]>; images: Record<number, string>;
  preparingTheme: Ref<string>; errorText: Ref<string>;
  handleEvent(ev: any): void; goTo(n: number): void; next(): void; prev(): void;
  back(): void; reset(): void;
}
```

- [ ] **Step 1: 写失败测试**

```ts
// frontend/tests/storySession.test.ts
// web-060：绘本会话——事件流转/自动推进（双序）/乐观翻页/取消不回弹
import { describe, expect, it, vi } from "vitest";
import { useStorySession } from "../src/voice/useStorySession";

function make() {
  const sent: any[] = [];
  const client: any = {
    storyPage: (n: number) => sent.push({ type: "story_page", n }),
    storyFinish: () => sent.push({ type: "story_finish" }),
    storyCancel: () => sent.push({ type: "story_cancel" }),
  };
  let ended: (() => void) | null = null;
  const player: any = {
    playing: false,
    stop: vi.fn(),
    set onEnded(fn: any) { ended = fn; },
    get onEnded() { return ended; },
  };
  const phases: string[] = [];
  const s = useStorySession({ client, player, onPhaseChange: (p) => phases.push(p) });
  return { s, sent, player, phases, drain: () => ended?.() };
}

const begin = {
  type: "story_begin", story_id: "s1", title: "霸王别姬", total: 3, cached: false,
  pages: [{ n: 1, text: "一" }, { n: 2, text: "二" }, { n: 3, text: "三" }],
};

describe("web-060 useStorySession", () => {
  it("event flow preparing→playing→img", () => {
    const { s } = make();
    s.handleEvent({ type: "story_preparing", theme: "霸王别姬" });
    expect(s.phase.value).toBe("preparing");
    s.handleEvent(begin);
    expect(s.phase.value).toBe("playing");
    expect(s.page.value).toBe(1);
    s.handleEvent({ type: "story_page_img", n: 2, url: "/api/story/s1/img/2" });
    expect(s.images[2]).toBe("/api/story/s1/img/2");
  });

  it("auto-advance: speak_end then drain", () => {
    const { s, sent, player, drain } = make();
    s.handleEvent(begin);
    s.handleEvent({ type: "story_speak_end", n: 1, cancelled: false });
    player.playing = true;
    drain();                                    // 播尽 → 翻第 2 页
    expect(s.page.value).toBe(2);
    expect(sent).toContainEqual({ type: "story_page", n: 2 });
  });

  it("auto-advance: drain before speak_end (reverse order)", () => {
    const { s, sent, player, drain } = make();
    s.handleEvent(begin);
    player.playing = false;
    drain();                                    // 先排空（guard 未满足，不动）
    expect(s.page.value).toBe(1);
    s.handleEvent({ type: "story_speak_end", n: 1, cancelled: false });
    expect(s.page.value).toBe(2);               // speak_end 到达时已在播尽态 → 立即推进
  });

  it("cancelled speak_end never advances", () => {
    const { s, player, drain } = make();
    s.handleEvent(begin);
    s.handleEvent({ type: "story_speak_end", n: 1, cancelled: true });
    player.playing = false;
    drain();
    expect(s.page.value).toBe(1);
  });

  it("manual flip optimistic + local stop", () => {
    const { s, sent, player } = make();
    s.handleEvent(begin);
    s.next();
    expect(s.page.value).toBe(2);
    expect(player.stop).toHaveBeenCalled();     // 立马静音（对齐 web-047）
    expect(sent).toContainEqual({ type: "story_page", n: 2 });
    s.prev();
    expect(s.page.value).toBe(1);
    s.prev();
    expect(s.page.value).toBe(1);               // 边界钳制
  });

  it("last page drain sends finish; story_end done → finished", () => {
    const { s, sent, player, drain, phases } = make();
    s.handleEvent(begin);
    s.goTo(3);
    s.handleEvent({ type: "story_speak_end", n: 3, cancelled: false });
    player.playing = false;
    drain();
    expect(sent).toContainEqual({ type: "story_finish" });
    s.handleEvent({ type: "story_end", reason: "done" });
    expect(s.phase.value).toBe("finished");
  });

  it("back cancels and resets", () => {
    const { s, sent } = make();
    s.handleEvent(begin);
    s.back();
    expect(sent).toContainEqual({ type: "story_cancel" });
    expect(s.phase.value).toBe("idle");
  });

  it("story_error surfaces message and idles", () => {
    const { s } = make();
    s.handleEvent({ type: "story_preparing", theme: "x" });
    s.handleEvent({ type: "story_error", code: "moderation", message: "换一个试试吧" });
    expect(s.phase.value).toBe("idle");
    expect(s.errorText.value).toContain("换一个");
  });
});
```

- [ ] **Step 2: 跑测试确认失败** — 预期模块不存在。

- [ ] **Step 3: 实现**（`frontend/src/voice/useStorySession.ts`）

```ts
/** 绘本会话（web-060）：story_* 事件 → 页码/插图/阶段；自动推进=播尽(onEnded)+speak_end 双序护栏。 */
import { reactive, ref } from "vue";
import type { Ref } from "vue";
import type { VoiceWsClient } from "./VoiceWsClient";
import type { PcmPlayer } from "../audio/player";

export type StoryPhase = "idle" | "preparing" | "playing" | "finished";

export function useStorySession(deps: {
  client: VoiceWsClient;
  player: PcmPlayer;
  onPhaseChange?: (p: StoryPhase) => void;
}) {
  const phase: Ref<StoryPhase> = ref("idle");
  const title = ref("");
  const page = ref(1);
  const total = ref(0);
  const pages: Ref<{ n: number; text: string }[]> = ref([]);
  const images: Record<number, string> = reactive({});
  const preparingTheme = ref("");
  const errorText = ref("");
  let speakEndPage = 0;          // 最后一个非 cancel 的 speak_end 页码
  // 【实施修正（Task 11 实测+审查）】drained 初值必须为 false 且 story_speak_start 复位——
  // 初值 true 会让 speak_end 单独满足护栏提前翻页，goTo 的 player.stop() 截掉在播尾音；
  // speakStarted 兜底无 TTS 降级（tts=None 时无 speak_start 无 PCM，speak_end 即视为已排尽，防永卡第 1 页）。
  let drained = false;           // 播放器已排空（无在途音频）
  let speakStarted = false;      // 本页是否见过 speak_start

  function setPhase(p: StoryPhase) {
    phase.value = p;
    deps.onPhaseChange?.(p);
  }

  function advance() {
    if (phase.value !== "playing") return;
    if (page.value < total.value) {
      goTo(page.value + 1);
    } else {
      deps.client.storyFinish();
    }
  }

  function maybeAdvance() {
    // 双序护栏：speak_end(page) 与播尽两个条件都齐才推进（乱序各触发一次检查）
    if (speakEndPage === page.value && drained) advance();
  }

  function handleEvent(ev: any) {
    switch (ev.type) {
      case "story_preparing":
        preparingTheme.value = ev.theme ?? "";
        errorText.value = "";
        setPhase("preparing");
        break;
      case "story_begin":
        title.value = ev.title ?? "";
        total.value = ev.total ?? 0;
        pages.value = ev.pages ?? [];
        page.value = 1;
        speakEndPage = 0;
        Object.keys(images).forEach((k) => delete images[Number(k)]);
        setPhase("playing");
        break;
      case "story_page_img":
        images[ev.n] = ev.url;
        break;
      case "story_speak_start":            // 【实施修正新增】
        speakStarted = true;
        drained = false;                     // 新页音频开播 → 未排尽（护栏关键复位）
        break;
      case "story_speak_end":
        if (ev.cancelled) break;
        if (!speakStarted) drained = true;   // 【fix round 1】无 TTS：本页无音频，视为已排尽
        speakEndPage = ev.n;
        maybeAdvance();
        break;
      case "story_end":
        setPhase(ev.reason === "done" ? "finished" : "idle");
        break;
      case "story_error":
        errorText.value = ev.message ?? "故事生成失败";
        setPhase("idle");
        break;
    }
  }

  function goTo(n: number) {
    if (phase.value !== "playing" && phase.value !== "finished") return;
    const target = Math.min(Math.max(1, n), total.value || 1);
    if (target === page.value) return;
    page.value = target;                          // 乐观翻页
    speakEndPage = 0;
    speakStarted = false;                         // 【实施修正】新页 speak_start 未至
    deps.player.stop();                           // 本地立即静音（web-047 对齐）
    drained = true;                               // stop 后队列已空=已排尽
    deps.client.storyPage(target);
  }

  deps.player.onEnded = () => {
    drained = true;
    maybeAdvance();
  };

  // 【实施修正】reset 提升为作用域函数——对象字面量内自引用属性名会 ReferenceError
  function reset() {
    setPhase("idle"); page.value = 1;
    speakEndPage = 0; speakStarted = false; drained = false;
  }

  return {
    phase, title, page, total, pages, images, preparingTheme, errorText,
    handleEvent, goTo,
    next: () => goTo(page.value + 1),
    prev: () => goTo(page.value - 1),
    back: () => { deps.player.stop(); deps.client.storyCancel(); reset(); },
    reset,
  };
}
```

- [ ] **Step 4: 跑测试确认通过** — `cd frontend && npx vitest run tests/storySession.test.ts`

- [ ] **Step 5: 提交** — `git add -f frontend/tests/storySession.test.ts frontend/src/voice/useStorySession.ts && git commit -m "feat(web): web-060 故事绘本——useStorySession（自动推进双序护栏/乐观翻页）"`

---

### Task 12: StoryBook.vue 绘本组件（web-061）

**Files:**
- Create: `frontend/src/components/StoryBook.vue`
- Test: `frontend/tests/storyBook.test.ts`

**Interfaces:**
- Consumes: Task 11 `useStorySession` 返回对象（prop 注入，测试用假件）。
- Produces: `StoryBook.vue`（props: `story`；emits: `back`）。

- [ ] **Step 1: 写失败测试**（范式照抄 `components.test.ts`/`chatPanel.test.ts` 的 mount 方式）

```ts
// frontend/tests/storyBook.test.ts
// web-061：绘本组件——页式渲染/占位→图/翻页边界/结束态/preparing/返回
import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { reactive, ref } from "vue";
import StoryBook from "../src/components/StoryBook.vue";

function fakeStory() {
  return {
    phase: ref("playing"),
    title: ref("霸王别姬"),
    page: ref(1),
    total: ref(3),
    pages: ref([{ n: 1, text: "第一页文" }, { n: 2, text: "第二页文" }, { n: 3, text: "第三页文" }]),
    images: reactive({}) as Record<number, string>,
    preparingTheme: ref(""),
    errorText: ref(""),
    next: vi.fn(), prev: vi.fn(), back: vi.fn(),
  };
}

describe("web-061 StoryBook", () => {
  it("renders current page text and indicator", () => {
    const w = mount(StoryBook, { props: { story: fakeStory() } });
    expect(w.text()).toContain("第一页文");
    expect(w.text()).toContain("1 / 3");
    expect(w.text()).toContain("霸王别姬");
  });

  it("placeholder before image, img after story_page_img", async () => {
    const st = fakeStory();
    const w = mount(StoryBook, { props: { story: st } });
    expect(w.find(".story-img").exists()).toBe(false);
    expect(w.find(".story-img-placeholder").exists()).toBe(true);
    st.images[1] = "/api/story/s1/img/1";
    await w.vm.$nextTick();
    expect(w.find(".story-img").exists()).toBe(true);
    expect(w.find("img.story-img").attributes("src")).toBe("/api/story/s1/img/1");
  });

  it("flip buttons call next/prev and disable at bounds", async () => {
    const st = fakeStory();
    const w = mount(StoryBook, { props: { story: st } });
    expect((w.find(".btn-prev").element as HTMLButtonElement).disabled).toBe(true);
    await w.find(".btn-next").trigger("click");
    expect(st.next).toHaveBeenCalled();
    st.page.value = 3;
    await w.vm.$nextTick();
    expect((w.find(".btn-next").element as HTMLButtonElement).disabled).toBe(true);
  });

  it("back button emits and calls story.back", async () => {
    const st = fakeStory();
    const w = mount(StoryBook, { props: { story: st } });
    await w.find(".btn-back").trigger("click");
    expect(st.back).toHaveBeenCalled();
    expect(w.emitted("back")).toBeTruthy();
  });

  it("preparing and finished overlays", async () => {
    const st = fakeStory();
    st.phase = ref("preparing");
    st.preparingTheme.value = "嫦娥奔月";
    const w = mount(StoryBook, { props: { story: st } });
    expect(w.text()).toContain("嫦娥奔月");
    st.phase.value = "finished";
    await w.vm.$nextTick();
    expect(w.text()).toContain("故事讲完啦");
  });
});
```

- [ ] **Step 2: 跑测试确认失败** — 预期组件不存在。

- [ ] **Step 3: 实现**（`frontend/src/components/StoryBook.vue`；样式照抄 ChatPanel 的返回钮 104px/drop-shadow 与羊皮纸色系，1080×1920 设计坐标）

```vue
<template>
  <div class="storybook">
    <button class="btn-back" @click="onBack">返回</button>

    <div v-if="story.phase.value === 'preparing'" class="story-overlay">
      <div class="prepare-text">湘小图正在想「{{ story.preparingTheme.value }}」的故事…</div>
    </div>

    <template v-else>
      <div class="story-img-area">
        <img v-if="imgUrl" class="story-img" :src="imgUrl" :alt="story.title.value" />
        <div v-else class="story-img-placeholder"><span>插画绘制中…</span></div>
      </div>
      <div class="story-text">{{ currentText }}</div>
      <div class="story-bar">
        <button class="btn-prev" :disabled="story.page.value <= 1" @click="story.prev()">上一页</button>
        <span class="page-indicator">{{ story.page.value }} / {{ story.total.value }}</span>
        <button class="btn-next" :disabled="story.page.value >= story.total.value" @click="story.next()">下一页</button>
      </div>
      <div v-if="story.phase.value === 'finished'" class="story-overlay finished">
        <div class="finished-text">故事讲完啦</div>
      </div>
    </template>
  </div>
</template>

<script lang="ts" setup>
/** 绘本模式（web-061）：一页=一图+一段文；翻页/返回/占位/结束态。 */
import { computed } from "vue";

const props = defineProps<{ story: any }>();
const emit = defineEmits<{ (e: "back"): void }>();

const currentText = computed(() => {
  const p = props.story.pages.value.find((x: any) => x.n === props.story.page.value);
  return p?.text ?? "";
});
const imgUrl = computed(() => props.story.images[props.story.page.value] ?? "");

function onBack() {
  props.story.back();
  emit("back");
}
</script>
```

（`<style scoped>` 实装要点，写码时照抄 ChatPanel 既有变量：图区 1080×1150 object-fit cover +
占位 shimmer 动画；文区衬线 44px/1.6 居中；底栏按钮 200×96px；`.story-overlay` 半透明盖层。）

- [ ] **Step 4: 跑测试确认通过 + build** — `cd frontend && npx vitest run tests/storyBook.test.ts && npm run build`

- [ ] **Step 5: 提交** — `git add -f frontend/tests/storyBook.test.ts frontend/src/components/StoryBook.vue && git commit -m "feat(web): web-061 故事绘本——StoryBook 组件（页式布局/占位/翻页/结束态）"`

---

### Task 13: HomeView 三态接线 + 预设引导（web-062）

**Files:**
- Modify: `frontend/src/views/HomeView.vue`、`frontend/src/voice/useVoiceSession.ts`（story 事件转接）、`frontend/src/stores/app.ts`、`kiosk_server/presets.py`
- Test: `frontend/tests/storyHome.test.ts`、`tests/test_web062_story_presets.py`

**Interfaces:**
- Consumes: Task 10~12 全部。
- Produces: `useVoiceSession` deps 增 `onStoryEvent?: (ev:any)=>void`（default 分支转接）；HomeView `mode` 三态 `"home"|"chat"|"story"`；预设池含故事引导。

- [ ] **Step 1: 写失败测试**

```ts
// frontend/tests/storyHome.test.ts
// web-062：useVoiceSession 转接 story_* 事件；其余事件行为不变
import { describe, expect, it, vi } from "vitest";
import { useVoiceSession } from "../src/voice/useVoiceSession";

class FakeWs {
  static last: any;
  readyState = 1; onopen: any; onmessage: any; onclose: any;
  sent: any[] = [];
  constructor(public url: string) { FakeWs.last = this; }
  send(d: any) { this.sent.push(d); }
  close() {}
}
(globalThis as any).WebSocket = FakeWs;

describe("web-062 voice session story relay", () => {
  it("relays story_* events to onStoryEvent, not chat bubbles", () => {
    const storyEvs: any[] = [];
    const s = useVoiceSession({ onStoryEvent: (e) => storyEvs.push(e) });
    s.connect();
    FakeWs.last.onopen?.({});
    FakeWs.last.onmessage?.({ data: JSON.stringify({ type: "hello", ok: true, voice: false }) });
    FakeWs.last.onmessage?.({ data: JSON.stringify({ type: "story_preparing", theme: "x" }) });
    FakeWs.last.onmessage?.({ data: JSON.stringify({ type: "story_begin", total: 2, pages: [] }) });
    expect(storyEvs.map((e) => e.type)).toEqual(["story_preparing", "story_begin"]);
    expect(s.chatHistory.length).toBe(0);          // 不产生聊天气泡
  });

  it("non-story events unchanged (answer flow)", () => {
    const storyEvs: any[] = [];
    const s = useVoiceSession({ onStoryEvent: (e) => storyEvs.push(e) });
    s.onEvent({ type: "answer_start", turn: 1 });
    s.onEvent({ type: "answer_chunk", turn: 1, text: "答" });
    expect(storyEvs.length).toBe(0);
    expect(s.chatHistory.length).toBe(1);
  });
});
```

```python
# tests/test_web062_story_presets.py
# web-062：预设池含故事引导入口（服务端缺省池）
from kiosk_server.presets import DEFAULT_PRESETS, load_presets


class TestStoryPresets:
    def test_default_pool_has_story_entry(self):
        assert any("故事" in q for q in DEFAULT_PRESETS)

    def test_fallback_load_keeps_story_entry(self, tmp_path):
        qs = load_presets(str(tmp_path / "missing.json"))
        assert any("故事" in q for q in qs)
```

- [ ] **Step 2: 跑测试确认失败** — 预期断言失败。

- [ ] **Step 3: 实现**

`useVoiceSession.ts`（onEvent switch 的 default 分支前插入）：

```ts
      default:
        if (typeof ev.type === "string" && ev.type.startsWith("story_")) {
          deps.onStoryEvent?.(ev);                 // web-062：绘本事件转接，不进聊天气泡
        }
        break;
```

（`VoiceSessionDeps` 接口增 `onStoryEvent?: (ev: any) => void;`）

`HomeView.vue`（要点：mode 三态；创建 story 会话并转接；phase 驱动切页；返回/结束复原）：

```ts
const mode = ref<"home" | "chat" | "story">("home");
const story = useStorySession({
  client: session.client,
  player: session.player,
  onPhaseChange: (p) => {
    if (p === "preparing" || p === "playing") mode.value = "story";
    else if (p === "idle") mode.value = "home";        // error/cancel 回首页
    // finished：停留收尾页（web-062），空闲计时器到点回首页（既有机制零改动）
  },
});
const session = useVoiceSession({
  // …既有 deps 不动，
  onStoryEvent: (ev) => story.handleEvent(ev),
});
```

模板增：`<StoryBook v-if="mode === 'story'" :story="story" @back="mode = 'home'" />`。

`kiosk_server/presets.py` 与 `frontend/src/stores/app.ts` 的缺省池各加一条
`"给我讲个嫦娥奔月的故事"`（服务器正式池在 `data/kiosk/preset_questions.json`，
部署侧追加=运维动作，记入 README）。

- [ ] **Step 4: 跑测试确认通过 + 全量回归** — `python -m pytest tests/ -q`（772+新增全绿）；`cd frontend && npx vitest run && npm run build`

- [ ] **Step 5: 提交** — `git add -f tests/test_web062_story_presets.py frontend/tests/storyHome.test.ts && git add kiosk_server/presets.py frontend/src/views/HomeView.vue frontend/src/voice/useVoiceSession.ts frontend/src/stores/app.ts && git commit -m "feat(web): web-062 故事绘本——HomeView 三态接线 + 预设引导入口"`

---

### Task 14: 真实 API 冒烟 + 文档记账（web-063）

**Files:**
- Create: `scripts/smoke_story.py`
- Modify: `README.md`（更新日志）、`code_review_report_v3.md`（增补）、`deploy/OPERATIONS.md`（冒烟命令一行）

**Interfaces:**
- Consumes: Task 3/4 的 `ScriptClient`/`ImageClient`/`build_image_prompt`（真 API）。

- [ ] **Step 1: 冒烟脚本**（真实 qwen-plus + qwen-image-3.0；出图落盘留档；prompt A/B 对比模式）

```python
# scripts/smoke_story.py
# web-063：绘本真实 API 冒烟（非测试，不进 pytest）——脚本+插图全链，留档为证。
"""用法：
  python scripts/smoke_story.py "霸王别姬"            # 全链：脚本→全部插图（页数可用 --pages 截断省钱）
  python scripts/smoke_story.py "霸王别姬" --pages 2  # 只出前 2 页图
  python scripts/smoke_story.py "霸王别姬" --ab       # prompt A/B：第 1 页两种模板各出 1 张对比
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kiosk_server.story import (IMAGE_NEGATIVE_SUFFIX, IMAGE_STYLE_PREFIX,
                                ImageClient, ScriptClient, StoryCache,
                                build_image_prompt)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("theme")
    ap.add_argument("--pages", type=int, default=0, help="只出前 N 页图（0=全部）")
    ap.add_argument("--ab", action="store_true", help="prompt A/B 对比模式")
    ap.add_argument("--out", default="data/story_smoke", help="留档目录")
    args = ap.parse_args()
    out = Path(args.out) / StoryCache.story_id(args.theme)
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    script = ScriptClient("qwen-plus", 1600, 60).generate(args.theme)
    t_script = time.time() - t0
    (out / "script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print(f"[script] {t_script:.1f}s title={script['title']} scenes={len(script['scenes'])}")
    for i, s in enumerate(script["scenes"], 1):
        assert len(s) <= 80, f"分镜 {i} 超 80 字（{len(s)}）"
        print(f"  {i:2d}. ({len(s)}字) {s}")

    img = ImageClient("qwen-image-3.0", "1024*1024", 90)
    pages = script["scenes"][: args.pages or len(script["scenes"])]
    if args.ab:
        # A=现模板；B=现模板+「上一页画面延续」衔接语（对比一致性差异，留档人工判读）
        for tag, prompt in (
            ("A", build_image_prompt(script["characters"], pages[0])),
            ("B", IMAGE_STYLE_PREFIX + f"主要角色保持统一形象：{script['characters']}。"
                f"本页画面紧接故事开头：{pages[0]}" + IMAGE_NEGATIVE_SUFFIX),
        ):
            t = time.time()
            ok = img.generate_to(out / f"page_1_{tag}.png", prompt)
            print(f"[img 1{tag}] {time.time() - t:.1f}s ok={ok}")
    else:
        for i, scene in enumerate(pages, 1):
            t = time.time()
            ok = img.generate_to(out / f"page_{i}.png",
                                 build_image_prompt(script["characters"], scene))
            print(f"[img {i}] {time.time() - t:.1f}s ok={ok}")
    print(f"[done] 留档 {out}")
    print("SMOKE_STORY_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 跑真实冒烟** — `python scripts/smoke_story.py "霸王别姬" --pages 3`，期望 `SMOKE_STORY_OK` + 3 张图落盘；再 `--ab --pages 1` 出 A/B 对比图，**人工目检一致性后回填最优 prompt 模板**（如需调整 `IMAGE_STYLE_PREFIX`，同步跑 Task 4 测试保持全绿）。
- [ ] **Step 3: 服务端 WS 全链冒烟（经 SSH 隧道，可选人工验收）** — 部署后 `python scripts/smoke_kiosk_ws.py --port 7862 "给我讲一个霸王别姬的故事"` 验证意图拦截不破坏既有冒烟。
- [ ] **Step 4: 文档** — README 更新日志加「AI 故事绘本（web-050~063）」条目（形态/模型/协议/缓存/测试数）；`code_review_report_v3.md` 增补本轮（决策溯源 + 实测数据 + 测试清单）；`deploy/OPERATIONS.md` §1.4 冒烟加一行 `python scripts/smoke_story.py "主题" --pages 2`。
- [ ] **Step 5: 全量回归** — `python -m pytest tests/ -q` 与 `cd frontend && npx vitest run && npm run build` 全绿。
- [ ] **Step 6: 提交** — `git add -f scripts/smoke_story.py && git add README.md code_review_report_v3.md deploy/OPERATIONS.md && git commit -m "feat(web): web-063 故事绘本——真实 API 冒烟脚本 + 文档记账"`

---

## Self-Review 记录（写计划后已执行）

- **Spec 覆盖**：D1→Task 11/12（翻页/自动+手动）；D2→Task 3；D3→Task 4/6；D4→全局约束（绘本固定云端）；D5→Task 2/8/13；D6→Task 6/7/11；D7→Task 7/8/11/13；D8→Task 5/6（LRU/重试/占位/审核）；D9→Task 5/9；D10→全局架构。无遗漏。
- **已知计划级细化**（实施时照此执行，无需再拍板）：①部分缺图的缓存命中会**补生成缺失页**（spec §6 语义的自然延伸，全图命中=零生成）；②`story_end{done}` 在收尾语播尽后发出；③`ask` 命中新故事=防御性取消旧故事后开新故事。
- **类型一致性**：`StorySession.wait_idle` 在 Task 6 测试使用、Task 6 Step 4 备注给出实现；`speak_fn` 参数在 Task 7 保留兼容；`s._tts` 在 Task 7 测试使用、Task 7 实现提供。前端 `images` 为 reactive Record（StoryBook computed 依赖，Task 12 测试经 `st.images[1]=…` 验证响应式）。
