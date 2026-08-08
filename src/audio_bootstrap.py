"""音频环境引导：确保 ffmpeg/ffprobe 可用（gradio 6 HLS 流式音频输出依赖 pydub+ffmpeg）。

必须在 import gradio 之前调用 ensure_ffmpeg()（gradio 导入即触发 pydub 导入，
pydub 在 import 时缓存 ffmpeg 查找结果）。
"""
from loguru import logger


def ensure_ffmpeg() -> bool:
    """将 static-ffmpeg 自带的 ffmpeg/ffprobe 二进制目录加入 PATH。

    Returns:
        True 表示 ffmpeg 可用；False 表示不可用（调用方自行降级）。
    """
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
        import shutil
        ok = shutil.which("ffmpeg") is not None
        if not ok:
            logger.warning("static-ffmpeg 已加载但 ffmpeg 不在 PATH")
        return ok
    except Exception as e:
        logger.warning(f"ffmpeg 引导失败（TTS 流式播放将降级为一次性播放）: {e}")
        return False