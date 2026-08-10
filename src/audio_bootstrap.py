"""音频环境引导：确保 ffmpeg/ffprobe 可用（gradio 6 HLS 流式音频输出依赖 pydub+ffmpeg）。

必须在 import gradio 之前调用 ensure_ffmpeg()（gradio 导入即触发 pydub 导入，
pydub 在 import 时缓存 ffmpeg 查找结果）。
"""
from loguru import logger


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