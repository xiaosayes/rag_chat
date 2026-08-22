# -*- coding: utf-8 -*-
"""一次性探测：deepseek-v4-flash-0731 绘本脚本（寓言忠实+画面描述字段+时延）。
用法: python -X utf8 scripts/_diag_ds_script.py
"""
import json
import re
import sys
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


def probe(theme: str, enable_thinking: bool | None) -> None:
    from dashscope import Generation
    kw = dict(model="deepseek-v4-flash-0731",
              messages=[{"role": "system", "content": NEW_PROMPT},
                        {"role": "user", "content": f"故事主题：{theme}"}],
              result_format="message", max_tokens=2200)
    if enable_thinking is not None:
        kw["enable_thinking"] = enable_thinking
    t0 = time.monotonic()
    rsp = Generation.call(**kw)
    dt = time.monotonic() - t0
    tag = f"thinking={enable_thinking}"
    if rsp.status_code != 200:
        print(f"[{tag}] HTTP {rsp.status_code} {getattr(rsp, 'code', '')} {getattr(rsp, 'message', '')[:100]}")
        return
    content = rsp.output.choices[0].message.content
    m = re.search(r"\{.*\}", content, re.S)
    try:
        payload = json.loads(m.group(0))
        scenes = payload.get("scenes") or []
        images = payload.get("images") or []
        print(f"[{tag}] {dt:.1f}s | title={payload.get('title')} | "
              f"scenes={len(scenes)} images={len(images)} | lens={[len(s) for s in scenes]}")
        print(f"  characters: {payload.get('characters')}")
        for i, (s, im) in enumerate(zip(scenes, images), 1):
            print(f"  {i}. ({len(s)}字) {s}")
            print(f"     img({len(im)}字): {im}")
    except Exception as e:  # noqa: BLE001
        print(f"[{tag}] {dt:.1f}s JSON 解析失败: {e} | raw={content[:300]}")


if __name__ == "__main__":
    theme = sys.argv[1] if len(sys.argv) > 1 else "农夫与蛇"
    probe(theme, False)
