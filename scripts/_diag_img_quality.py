# -*- coding: utf-8 -*-
"""一次性 A/B：qwen-image-3.0 文字伪影/解剖错误 抑制实验（真实 API）。
A=现行（仅 prompt 内否定句） B=+negative_prompt 专用参数 C=negative_prompt+强化 prompt 否定
用法: python -X utf8 scripts/_diag_img_quality.py
输出: data/img_quality/*.png + 控制台结果
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings  # noqa: E402

import dashscope  # noqa: E402

dashscope.api_key = settings.dashscope_api_key

STYLE = "中国传统绘本插画，水彩淡彩，色调柔和温暖，儿童读物风格，画面简洁干净。"
NEG_OLD = "画面中不要出现任何文字、水印、标志；不要恐怖、阴暗元素。"
NEG_PARAM = ("文字，汉字，字母，拼音，数字，符号，水印，标志，字幕，标题，"
             "畸形，多余的肢体，多余的耳朵，五官错位，肢体融合，恐怖，阴暗")
NEG_NEW = "画面中绝对不要出现任何文字、汉字、字母、拼音、数字或符号；角色的耳朵、四肢、五官数量必须准确自然。"

# 两个最易出问题的分镜：①含对话引号（易诱发文字渲染）②角色动作特写（易解剖错误）
SCENES = [
    ("dialog", "小兔子跳跳拍拍胸脯说：「我跑得可快啦，一定第一个到终点！」小乌龟慢慢只是笑了笑。"),
    ("action", "小兔子跳跳蹦蹦跳跳往前跑，红红的蝴蝶结在耳朵上一颠一颠，可爱极了。"),
]
CHARS = "小乌龟慢慢背着圆圆硬壳、戴蓝色小花；小兔子跳跳长耳朵、戴红蝴蝶结、穿条纹小背心"


def one(tag: str, prompt: str, neg: str | None, out: Path, results: dict) -> None:
    from dashscope import MultiModalConversation
    t = time.monotonic()
    kw = dict(model="qwen-image-3.0",
              messages=[{"role": "user", "content": [{"text": prompt}]}],
              prompt_extend=False, size="1024*1024")
    if neg is not None:
        kw["negative_prompt"] = neg
    try:
        rsp = MultiModalConversation.call(**kw)
        if getattr(rsp, "status_code", 0) != 200:
            raise RuntimeError(f"HTTP {rsp.status_code}: {getattr(rsp, 'code', '')}")
        url = next(i["image"] for i in rsp.output.choices[0].message.content
                   if isinstance(i, dict) and i.get("image"))
        import urllib.request
        out.write_bytes(urllib.request.urlopen(url, timeout=30).read())
        results[tag] = (True, time.monotonic() - t, "")
    except Exception as e:  # noqa: BLE001
        results[tag] = (False, time.monotonic() - t, f"{type(e).__name__}: {e}")
    print(f"  [{tag}] {'OK' if results[tag][0] else 'FAIL'} {results[tag][1]:.1f}s {results[tag][2]}",
          flush=True)


def main() -> None:
    out_dir = Path("data/img_quality")
    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict = {}
    ths = []
    sem = threading.Semaphore(2)

    def wrap(*a):
        with sem:
            one(*a)

    for scene_tag, scene in SCENES:
        base = f"{STYLE}主要角色保持统一形象：{CHARS}。本页画面：{scene}"
        variants = {
            f"A_{scene_tag}": (base + NEG_OLD, None),                    # 现行
            f"B_{scene_tag}": (base + NEG_OLD, NEG_PARAM),               # +专用负向参数
            f"C_{scene_tag}": (base + NEG_NEW, NEG_PARAM),               # 负向参数+强化否定
        }
        for tag, (prompt, neg) in variants.items():
            th = threading.Thread(target=wrap, args=(tag, prompt, neg,
                                                     out_dir / f"{tag}.png", results),
                                  daemon=True)
            ths.append(th)
            th.start()
    for t in ths:
        t.join()
    print("\n=== 汇总 ===")
    for tag in sorted(results):
        ok, dt, err = results[tag]
        print(f"{tag}: {'OK' if ok else 'FAIL'} {dt:.1f}s {err}")


if __name__ == "__main__":
    main()
