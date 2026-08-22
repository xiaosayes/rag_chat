# -*- coding: utf-8 -*-
"""一次性诊断②：qwen-flash 脚本时延/寓言还原度 + 并发2限流验证（真实 API）。
用法: python scripts/_diag_story_fix.py
"""
import json
import re
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings  # noqa: E402

import dashscope  # noqa: E402

dashscope.api_key = settings.dashscope_api_key

NEW_PROMPT = (
    "你是湘小图，湖南省少年儿童图书馆里给小朋友讲故事的亲切姐姐。"
    "请把用户给出的主题改编成一个适合 3~8 岁儿童聆听的绘本故事，"
    "语气亲切温暖、句子简短口语化、内容健康积极，不要列表、不要 Markdown、不要英文术语。"
    "若主题出自已有的寓言、成语、神话或童话故事（例如龟兔赛跑、守株待兔、嫦娥奔月），"
    "必须严格沿用原著的情节脉络、角色与结局，不得添加原著没有的角色、事件或转折，"
    "不得改变故事寓意；儿童化只体现在用词和语气上，可在不改动情节主线的前提下做适龄化柔化。"
    "把整个故事拆成 8 到 10 个分镜，每个分镜是一段 40 到 80 个字的叙述，合起来情节完整连贯。"
    "参考长度：「清晨，小乌龟和兔子站在森林的起跑线上。兔子拍拍胸脯说，我跑得可快啦，"
    "一定第一个到终点！小乌龟只是笑了笑，没有说话。」"
    "同时用一句话提炼主要角色的形象特征（年龄感、发型、服饰、颜色），供插画师保持角色一致。"
    "只输出 JSON，格式：{\"title\":\"故事标题\",\"characters\":\"角色形象描述\","
    "\"scenes\":[\"分镜1\",\"分镜2\",...]}，不要输出任何其他文字。"
)


def probe_script(model: str, theme: str) -> None:
    from dashscope import Generation
    t0 = time.monotonic()
    rsp = Generation.call(
        model=model,
        messages=[{"role": "system", "content": NEW_PROMPT},
                  {"role": "user", "content": f"故事主题：{theme}"}],
        result_format="message", max_tokens=1600)
    dt = time.monotonic() - t0
    if rsp.status_code != 200:
        print(f"[{model}|{theme}] HTTP {rsp.status_code} {rsp.code}")
        return
    content = rsp.output.choices[0].message.content
    m = re.search(r"\{.*\}", content, re.S)
    try:
        payload = json.loads(m.group(0))
        scenes = payload.get("scenes") or []
        lens = [len(s) for s in scenes]
        print(f"[{model}|{theme}] {dt:.1f}s | title={payload.get('title')} | "
              f"scenes={len(scenes)} | lens={lens}")
        for i, s in enumerate(scenes, 1):
            print(f"    {i}. {s}")
    except Exception as e:  # noqa: BLE001
        print(f"[{model}|{theme}] {dt:.1f}s JSON 解析失败: {e} | raw={content[:200]}")


def probe_images_concurrency2() -> None:
    from dashscope import MultiModalConversation
    prompts = [f"中国传统绘本插画，水彩淡彩，色调柔和温暖，儿童读物风格，画面简洁干净。"
               f"本页画面：小石头在森林小路上第{i}处风景玩耍。"
               f"画面中不要出现任何文字、水印、标志；不要恐怖、阴暗元素。" for i in range(1, 11)]
    results: dict[int, tuple[bool, float, str]] = {}
    lock = threading.Lock()

    def gen(i: int, prompt: str) -> None:
        t = time.monotonic()
        try:
            rsp = MultiModalConversation.call(
                model="qwen-image-3.0",
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                prompt_extend=False, size="1024*1024")
            if getattr(rsp, "status_code", 0) != 200:
                raise RuntimeError(f"HTTP {rsp.status_code}: {getattr(rsp, 'code', '')}")
            ok, err = True, ""
        except Exception as e:  # noqa: BLE001
            ok, err = False, f"{type(e).__name__}: {e}"
        with lock:
            results[i] = (ok, time.monotonic() - t, err)
            print(f"  [img{i}] {'OK' if ok else 'FAIL'} {results[i][1]:.1f}s {err}")

    sem = threading.Semaphore(2)

    def worker(i: int, p: str) -> None:
        with sem:
            gen(i, p)

    t0 = time.monotonic()
    ths = [threading.Thread(target=worker, args=(i, p)) for i, p in enumerate(prompts, 1)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    fails = [i for i, r in results.items() if not r[0]]
    print(f"[conc2] wall={time.monotonic() - t0:.1f}s | ok={10 - len(fails)}/10 | fails={fails}")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "script"):
        probe_script("qwen-flash", "守株待兔")
        probe_script("qwen-flash", "小石头和大山爷爷")
    if which in ("all", "img"):
        probe_images_concurrency2()


def probe_script_nothink(model: str, theme: str) -> None:
    from dashscope import Generation
    t0 = time.monotonic()
    try:
        rsp = Generation.call(
            model=model,
            messages=[{"role": "system", "content": NEW_PROMPT},
                      {"role": "user", "content": f"故事主题：{theme}"}],
            result_format="message", max_tokens=1600, enable_thinking=False)
    except Exception as e:  # noqa: BLE001
        print(f"[{model}-nothink|{theme}] 调用异常: {type(e).__name__}: {e}")
        return
    dt = time.monotonic() - t0
    if rsp.status_code != 200:
        print(f"[{model}-nothink|{theme}] HTTP {rsp.status_code} {rsp.code}")
        return
    content = rsp.output.choices[0].message.content
    m = re.search(r"\{.*\}", content, re.S)
    try:
        payload = json.loads(m.group(0))
        scenes = payload.get("scenes") or []
        lens = [len(s) for s in scenes]
        print(f"[{model}-nothink|{theme}] {dt:.1f}s | title={payload.get('title')} | scenes={len(scenes)} | lens={lens}")
        for i, s in enumerate(scenes, 1):
            print(f"    {i}. {s}")
    except Exception as e:  # noqa: BLE001
        print(f"[{model}-nothink|{theme}] {dt:.1f}s JSON 解析失败: {e} | raw={content[:300]}")
