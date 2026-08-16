"""数字人前端资产 vendor（web-013）：模型/视频从 CDN 下载，切图/字体从只读参考复制。

幂等：已存在且大小非零即跳过。末段校验 gltf 引用完整性。
用法：python scripts/vendor_frontend_assets.py
"""
from __future__ import annotations

import json
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFER = ROOT / "data" / "front_ui" / "xiaolu" / "front_refer" / "问答-H5端" / "public"
FRONT = ROOT / "frontend" / "public"
CDN = "https://ai.museumbi.cn/vr-resource/model/hnsetsg/deer"

# (相对目标路径, 来源 URL)
DOWNLOADS = [
    ("model/deer/deer_final_5.gltf", f"{CDN}/gltf/deer_final_5.gltf"),
    ("model/deer/deer_final_5.bin", f"{CDN}/gltf/deer_final_5.bin"),
    ("model/deer/evening_road_01_puresky_1k.exr", f"{CDN}/evening_road_01_puresky_1k.exr"),
    ("video/o.webm", f"{CDN}/o.webm"),
    ("video/o.apng", f"{CDN}/o.apng"),
]
# gltf 引用的贴图（相对 model/deer/）
TEXTURES = [
    "AI_Xxl_Normal.png", "AI_Xxl_Base_color.png", "AI_Xxl_Roughness.png",
    "KouQiang_Normal_1001.png", "KouQiang_BaseColor_1001.png",
    "KouQiang_Roughness_1001.png", "CH_PipiTiger_LEye_MAT_BaseColor_4_tif.png",
]
# 参考切图：v2 全套 + 根级图标（排除合影/图书相关）
IMG_ROOT_FILES = [
    "audio.png", "audio_play.png", "audio_stop.png", "back.png",
    "icon_quit.png", "icon_refresh.png", "icon_sys.png", "keyboard.png",
    "logo.png", "micro.png", "send.png", "shadow.png",
]
FONTS = ["OPPOSans-M.ttf", "OPPOSans-R.ttf",
         "SourceHanSerifCN-Bold.otf", "SourceHanSerifCN-Medium.otf"]


def download(rel: str, url: str) -> str:
    dest = FRONT / rel
    if dest.exists() and dest.stat().st_size > 0:
        return f"skip {rel} ({dest.stat().st_size}B)"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return f"got  {rel} ({dest.stat().st_size}B)"


def copy(src_rel: str, dst_rel: str) -> str:
    src, dest = REFER / src_rel, FRONT / dst_rel
    if dest.exists() and dest.stat().st_size > 0:
        return f"skip {dst_rel}"
    if not src.exists():
        raise FileNotFoundError(f"参考资产缺失: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return f"cp   {dst_rel}"


def main() -> None:
    if not REFER.exists():
        raise SystemExit(f"只读参考目录不存在: {REFER}")
    for rel, url in DOWNLOADS:
        print(download(rel, url))
    gltf = json.loads((FRONT / "model/deer/deer_final_5.gltf").read_text(encoding="utf-8"))
    refs = [b["uri"] for b in gltf.get("buffers", [])] + \
           [i["uri"] for i in gltf.get("images", [])]
    for uri in refs:  # gltf 内引用全部要本地化
        if uri not in [d[0].split("/")[-1] for d in DOWNLOADS]:
            DOWNLOADS.append((f"model/deer/{uri}", f"{CDN}/gltf/{uri}"))
    seen = set()
    for rel, url in DOWNLOADS:
        if rel in seen:
            continue
        seen.add(rel)
        if not (FRONT / rel).exists():
            print(download(rel, url))
    for uri in refs:
        assert (FRONT / "model/deer" / uri).exists(), f"gltf 引用缺失: {uri}"
    print(f"[ok] gltf 引用完整（{len(refs)} 项）；动画剪辑: "
          f"{len(gltf.get('animations', []))} 个")
    for name in IMG_ROOT_FILES:
        print(copy(f"img/{name}", f"img/{name}"))
    for f in (REFER / "img" / "v2").iterdir():
        if f.is_file():
            print(copy(f"img/v2/{f.name}", f"img/v2/{f.name}"))
    for name in FONTS:
        print(copy(f"fonts/{name}", f"fonts/{name}"))
    total = sum(p.stat().st_size for p in FRONT.rglob("*") if p.is_file())
    print(f"[ok] vendor 完成: {FRONT} 共 {total / 1024 / 1024:.1f}MB")
    print("VENDOR_OK")


if __name__ == "__main__":
    sys.exit(main())
