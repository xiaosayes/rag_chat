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
    script = ScriptClient("deepseek-v4-flash-0731", 2200, 60).generate(args.theme)  # web-070 换型
    t_script = time.time() - t0
    (out / "script.json").write_text(json.dumps(script, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    images = script.get("images")
    print(f"[script] {t_script:.1f}s title={script['title']} scenes={len(script['scenes'])}"
          f" images={len(images) if images else 0}")
    for i, s in enumerate(script["scenes"], 1):
        assert len(s) <= 80, f"分镜 {i} 超 80 字（{len(s)}）"
        warn = " ⚠<40字" if len(s) < 40 else ""       # web-064：短分镜观察提示（不硬断言）
        print(f"  {i:2d}. ({len(s)}字){warn} {s}")
        if images:
            print(f"       img: {images[i - 1]}")     # web-070：画面短句观察

    img = ImageClient("qwen-image-3.0", "1024*1024", 90)
    scenes = script["scenes"]
    # web-070：生图 prompt 优先 images 画面短句（prose 喂图会被渲染成文字），缺失回退叙述
    pages = [(images[i] if images and i < len(images) else scenes[i])
             for i in range(len(scenes))][: args.pages or len(scenes)]
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
