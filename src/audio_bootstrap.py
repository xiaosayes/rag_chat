"""音频环境引导：确保 ffmpeg/ffprobe 可用（gradio 6 HLS 流式音频输出依赖 pydub+ffmpeg）。

必须在 import gradio 之前调用 ensure_ffmpeg()（gradio 导入即触发 pydub 导入，
pydub 在 import 时缓存 ffmpeg 查找结果）。
"""
import glob
import os

from loguru import logger


def patch_gradio_hls_reuse() -> bool:
    """修复 gradio 6.22 前端 bug：同一 Audio 组件的多次流式值只创建一次 hls。

    压缩 JS 中 ke() 用组件级标记 Se（布尔）记录"已创建 hls"，第二轮起的流式值
    （is_stream=true）因 !Se 为假而不再加载新 playlist → 自动播报第 2 轮起无声
    （bug-121 实测：本地两轮后端均正常输出 playlist，前端无 /stream/ 请求）。

    patch：Se 改为保存 hls 实例，重复流式先 destroy 旧实例再重建（幂等，重复
    执行安全；gradio 升级后文件名/内容变化则跳过并告警）。
    """
    try:
        import gradio

        assets_dir = os.path.join(
            os.path.dirname(os.path.abspath(gradio.__file__)),
            "templates", "frontend", "assets",
        )
        old1 = "if(Il.isSupported()&&!Se){"
        new1 = "if(Il.isSupported()){if(Se instanceof Il){Se.destroy(),Se=!1}"
        old2 = "}),Se=!0}else Se||="
        new2 = "}),Se=t}else Se||="
        for f in glob.glob(os.path.join(assets_dir, "StaticAudio-*.js")):
            try:
                with open(f, encoding="utf-8") as fh:
                    src = fh.read()
                if "Se instanceof Il" in src:
                    logger.info(f"gradio HLS 复用 patch 已应用（跳过）: {os.path.basename(f)}")
                    return True
                if old1 in src and old2 in src:
                    with open(f, "w", encoding="utf-8") as fh:
                        fh.write(src.replace(old1, new1).replace(old2, new2))
                    logger.info(f"gradio HLS 复用 patch 已应用: {os.path.basename(f)}")
                    return True
                logger.warning(f"gradio HLS 复用 patch 未匹配（版本可能变化）: {os.path.basename(f)}")
            except Exception as e:
                logger.warning(f"gradio HLS 复用 patch 失败 {os.path.basename(f)}: {e}")
        return False
    except Exception as e:
        logger.warning(f"gradio HLS 复用 patch 不可用: {e}")
        return False



def ensure_ffmpeg() -> bool:
    """将 static-ffmpeg 自带的 ffmpeg/ffprobe 二进制目录加入 PATH，并显式配置 pydub。

    gradio 6 流式音频输出（HLS/ADTS）依赖 pydub：AudioSegment.from_file 用 ffprobe
    探测、解码用 ffmpeg。仅加 PATH 不够（pydub 在导入时缓存查找结果，且服务器可能
    找不到 ffprobe）→ 此处直接写入 pydub.AudioSegment.converter/ffprobe（bug-121 实测）。

    Returns:
        True 表示 ffmpeg 可用；False 表示不可用（调用方自行降级）。
    """
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
        import shutil
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if ffmpeg and ffprobe:
            try:
                from pydub import AudioSegment

                AudioSegment.converter = ffmpeg
                AudioSegment.ffprobe = ffprobe
            except Exception:
                pass
            return True
        logger.warning("static-ffmpeg 已加载但 ffmpeg/ffprobe 不在 PATH")
        return False
    except Exception as e:
        logger.warning(f"ffmpeg 引导失败（TTS 流式播放将不可用）: {e}")
        return False