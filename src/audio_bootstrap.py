"""音频环境引导：确保 ffmpeg/ffprobe 可用（gradio 6 HLS 流式音频输出依赖 pydub+ffmpeg）。

必须在 import gradio 之前调用 ensure_ffmpeg()（gradio 导入即触发 pydub 导入，
pydub 在 import 时缓存 ffmpeg 查找结果）。
"""
import glob
import os

from loguru import logger


def patch_gradio_hls_reuse() -> bool:
    """修复 gradio 6.22 前端音频流式 bug 组（原地修改 StaticAudio-*.js，幂等）。

    ① hls.js 分支（bug-121 + audit-TTS）：原版 Se 布尔标记只创建一次 hls → 第 2
       轮起无声。注意：不能只改成“有实例就 destroy 重建”——前端 effect 在每个流式
       yield（每音频批）都会重跑 ke()，那样每 ~0.8s 销毁重建 MediaSource，缓冲清空
       重新加载，正是中途停顿的主因之一（E2E 实证：每批一次 detachMedia/attachMedia）。
       正确修复：**URL 未变直接返回**（同流复用），URL 变了（新一轮）才 destroy 重建。
    ② 原生 HLS 分支（audit-TTS）：Safari/无 MSE 浏览器走 `Se||=(M.src=...)` 一次性
       赋值，第 2 轮无声；同理改为 URL 变化才重赋值播放（Se 存上次 URL）。
    ③ hls.js 缓冲（audit-TTS）：maxBufferLength:1（只缓冲 1s！）是停顿放大器
       ——playlist 重载节奏≈段时长、无更新时 TD/2，发布稍有间断即断流 → 加深至 60s。
    ④ lowLatencyMode（audit-TTS）：低延迟模式让播放器贴着 live edge 播放（前向缓冲
       恒 ~0s，E2E 实证播放 33s 处缓冲仅 0.1s）——任何发布间断直接断流；关闭后
       hls.js 按 maxBufferLength 深缓冲（伪直播流的正确姿势）。

    gradio 升级导致模式不匹配则跳过并告警；注意本 patch 改文件内容但文件名
    （内容哈希）不变，需配合 app.py 的 /assets no-cache 中间件让客户端拿到新文件。
    """
    # (old, new, 已生效标记)；old 以 "REGEX:" 前缀走正则替换（兼容历史中间态）
    replacements = [
        # ① hls.js 分支：同 URL 复用，新 URL 才重建（兼容原版/旧 patch 中间态）
        (r"REGEX:if\(Il\.isSupported\(\)(?:&&!Se)?\)\{"
         r"(?:if\(Se instanceof Il\)\{Se\.destroy\(\),Se=!1\})?let t=new Il\(",
         "if(Il.isSupported()){if(Se instanceof Il){if(Se.url===e.url)return;"
         "Se.destroy(),Se=!1}let t=new Il(",
         "Se.url===e.url"),
        # ② 原生 HLS 分支：URL 变化才重赋值播放（Se 存上次 URL）
        (r"REGEX:\}\),Se=(?:!0|t)\}else\s*Se(?:\|\|)?=\(M\.src=e\.url,"
         r"o\.waveform_settings\.autoplay&&M\.play\(\),!0\)",
         "}),Se=t}else{if(Se!==e.url)M.src=e.url,"
         "o.waveform_settings.autoplay&&M.play(),Se=e.url}",
         "if(Se!==e.url)M.src=e.url"),
        # ③+④ 缓冲加深 + 关闭低延迟模式
        (r"REGEX:maxBufferLength:\d+,maxMaxBufferLength:\d+,lowLatencyMode:![01]",
         "maxBufferLength:60,maxMaxBufferLength:60,lowLatencyMode:!1",
         "maxBufferLength:60,maxMaxBufferLength:60,lowLatencyMode:!1"),
    ]
    try:
        import gradio

        assets_dir = os.path.join(
            os.path.dirname(os.path.abspath(gradio.__file__)),
            "templates", "frontend", "assets",
        )
        for f in glob.glob(os.path.join(assets_dir, "StaticAudio-*.js")):
            try:
                with open(f, encoding="utf-8") as fh:
                    src = fh.read()
                changed = False
                missing = []
                for old, new, marker in replacements:
                    if marker in src:
                        continue          # 该条已生效
                    if old.startswith("REGEX:"):
                        import re as _re

                        if _re.search(old[6:], src):
                            src = _re.sub(old[6:], new, src, count=1)
                            changed = True
                        else:
                            missing.append(marker)
                    elif old in src:
                        src = src.replace(old, new)
                        changed = True
                    else:
                        missing.append(marker)
                if changed:
                    with open(f, "w", encoding="utf-8") as fh:
                        fh.write(src)
                    logger.info(f"gradio HLS 复用 patch 已应用: {os.path.basename(f)}")
                if missing:
                    logger.warning(
                        f"gradio HLS patch 部分未匹配（版本可能变化）{missing}: {os.path.basename(f)}")
                    return False
                if not changed:
                    logger.info(f"gradio HLS 复用 patch 已应用（跳过）: {os.path.basename(f)}")
                return True
            except Exception as e:
                logger.warning(f"gradio HLS 复用 patch 失败 {os.path.basename(f)}: {e}")
        return False
    except Exception as e:
        logger.warning(f"gradio HLS 复用 patch 不可用: {e}")
        return False


def patch_gradio_mic_aec() -> bool:
    """麦克风 AEC 补丁（audit-ASR）：record.esm-*.js 的 getUserMedia 约束强制
    echoCancellation/noiseSuppression/autoGainControl。

    根因：gradio 录音 `getUserMedia({audio:t==null||t})`（即 audio:true，无 AEC）——
    一体机外放场景 TTS 播报被麦克风拾取，silero VAD 必然判为语音 → 自触发打断/
    唤醒死循环。浏览器内置 AEC 以本页输出为参考信号，是同源播报回串的标准解法
    （《数字人一体机落地方案》§5.1 亦冻结 echoCancellation:true）。
    """
    old = "getUserMedia({audio:t==null||t})"
    new = ("getUserMedia({audio:Object.assign({echoCancellation:!0,"
           "noiseSuppression:!0,autoGainControl:!0},t==null||t===!0?{}:t)})")
    marker = "echoCancellation:!0"
    try:
        import gradio

        assets_dir = os.path.join(
            os.path.dirname(os.path.abspath(gradio.__file__)),
            "templates", "frontend", "assets",
        )
        files = glob.glob(os.path.join(assets_dir, "record.esm-*.js"))
        if not files:
            logger.warning("麦克风 AEC patch：未找到 record.esm-*.js（gradio 版本/路径不符）")
            return False
        ok = True
        for f in files:
            try:
                with open(f, encoding="utf-8") as fh:
                    src = fh.read()
                if marker in src:
                    logger.info(f"麦克风 AEC patch 已应用（跳过）: {os.path.basename(f)}")
                    continue
                if old not in src:
                    logger.warning(
                        f"麦克风 AEC patch 未匹配（gradio 版本可能变化，结构校验跳过）: "
                        f"{os.path.basename(f)}")
                    ok = False
                    continue
                with open(f, "w", encoding="utf-8") as fh:
                    fh.write(src.replace(old, new, 1))
                logger.info(f"麦克风 AEC patch 已应用: {os.path.basename(f)}")
            except Exception as e:
                logger.warning(f"麦克风 AEC patch 失败 {os.path.basename(f)}: {e}")
                ok = False
        return ok
    except Exception as e:
        logger.warning(f"麦克风 AEC patch 不可用: {e}")
        return False


def patch_gradio_stream_endstream_guard() -> bool:
    """修复 gradio 6.22 流式输出收尾 KeyError（audit-ASR 实证）。

    现象：带 streaming Audio 输出的生成器事件结束时，末趟 final pass 所有输出值为
    None；`handle_streaming_outputs` 对未打开过流的输出执行 `stream_run[output_id]
    .end_stream()` → KeyError（E2E 实录：fn=auto_respond，out[3] id=28 Audio data=None）。
    触发条件：该轮从未 yield 音频（TTS 关闭/合成失败/被打断跳过收尾）。后果：事件
    收尾中断，最后一批输出（状态/重播更新）丢失。
    修复：final 趟预检——流式输出若无已打开的流且值为 None，降级为空 update（跳过）。
    """
    try:
        from gradio import components, utils
        from gradio.blocks import Blocks

        if getattr(Blocks.handle_streaming_outputs, "_asr_guarded", False):
            return True
        _orig = Blocks.handle_streaming_outputs

        async def _guarded(self, block_fn, data, session_hash=None, run=None,
                           root_path=None, final=False):
            if final and session_hash is not None and run is not None:
                streams = self.pending_streams.get(session_hash, {}).get(run)
                for i, block in enumerate(block_fn.outputs):
                    if (isinstance(block, components.StreamingOutput)
                            and block.streaming
                            and not utils.is_prop_update(data[i])
                            and (streams is None or block._id not in streams)
                            and data[i] is None):
                        data[i] = {"__type__": "update"}  # 从未开流 → 末趟跳过
            return await _orig(self, block_fn, data, session_hash=session_hash,
                               run=run, root_path=root_path, final=final)

        _guarded._asr_guarded = True
        Blocks.handle_streaming_outputs = _guarded
        logger.info("gradio 流式输出收尾 KeyError guard patch 已应用")
        return True
    except Exception as e:
        logger.warning(f"gradio 流式收尾 guard patch 不可用: {e}")
        return False


def verify_frontend_patches() -> bool:
    """启动自检：复读磁盘上的 StaticAudio-*.js / record.esm-*.js，确认 patch 标记真实落盘。

    patch 写入后再读回验证——写权限不足/多版本 gradio/路径漂移时 patch 日志可能
    “看似成功”但服务出去的还是旧文件（用户侧停顿排查依据）。audit-ASR 增查麦克风
    AEC 标记（缺失则一体机外放场景会自触发打断/误唤醒）。
    """
    markers = [
        "Se.url===e.url",
        "if(Se!==e.url)M.src=e.url",
        "maxBufferLength:60,maxMaxBufferLength:60,lowLatencyMode:!1",
    ]
    try:
        import gradio

        assets_dir = os.path.join(
            os.path.dirname(os.path.abspath(gradio.__file__)),
            "templates", "frontend", "assets",
        )
        files = glob.glob(os.path.join(assets_dir, "StaticAudio-*.js"))
        if not files:
            logger.error("前端 patch 自检失败: 未找到 StaticAudio-*.js（gradio 版本/路径不符）")
            return False
        for f in files:
            with open(f, encoding="utf-8") as fh:
                src = fh.read()
            missing = [m for m in markers if m not in src]
            if missing:
                logger.error(
                    f"前端 patch 自检失败: {os.path.basename(f)} 缺标记 {missing}——"
                    f"客户端将运行未打补丁的旧播放逻辑（缓冲 ~1s，LLM 出文停顿必现可闻停顿）。"
                    f"请检查 gradio 版本（需 6.22.x）与 site-packages 写权限后重启")
                return False
        # audit-ASR：麦克风 AEC 补丁同检
        rec_files = glob.glob(os.path.join(assets_dir, "record.esm-*.js"))
        if not rec_files:
            logger.error("前端 patch 自检失败: 未找到 record.esm-*.js（gradio 版本/路径不符）")
            return False
        with open(rec_files[0], encoding="utf-8") as fh:
            rec_src = fh.read()
        if "echoCancellation:!0" not in rec_src:
            logger.error(
                f"前端 patch 自检失败: {os.path.basename(rec_files[0])} 缺 echoCancellation "
                f"标记——一体机外放场景 TTS 播报会被麦克风拾取自触发打断/误唤醒。"
                f"请检查 gradio 版本（需 6.22.x）与 site-packages 写权限后重启")
            return False
        logger.info(f"前端 patch 自检通过: 3+1 标记在位（{os.path.basename(files[0])}）")
        return True
    except Exception as e:
        logger.error(f"前端 patch 自检异常: {e}")
        return False


def patch_gradio_media_stream_targetduration() -> bool:
    """修正 gradio 6.22 服务端 MediaStream.max_duration 每段 +1 单调蠕变（audit-TTS）。

    原版 `max_duration = max(prev, duration) + 1`（起始 5）→ playlist 的
    EXT-X-TARGETDURATION 随段数无限膨胀（0.5s 批次的回答 100 段 → TD=105）。
    hls.js 在 playlist 无更新时按 TD/2 调度重载（实证 zr()）→ 长回答中任一
    发布间断后，恢复音频最长 TD/2（数十秒）才被发现 → 长停顿（仿真实证 22.5s）。
    修正：TD = clamp(ceil(最大段时长), 1, 5)。配合 ≤1s 的播报批次，TD 恒 1
    （规范允许 TARGETDURATION ≥ 所有 EXTINF）——hls.js 重载节奏从
    min(TD,lastDur)≈段时长（缓冲==节奏的自锁刃缘，起步必断流）降到 1s 轮询，
    且缓冲超过 4×TD=4s 后稳定 1s 轮询（audit-TTS E2E 实证起步 2s 停顿消除）。

    运行时 monkeypatch（不改 site-packages 文件，服务器启动自动生效）；幂等；
    gradio 升级导致结构变化则跳过并告警。
    """
    try:
        import inspect
        import math
        import uuid

        from gradio import route_utils

        cls = route_utils.MediaStream
        if getattr(cls.add_segment, "_tts_td_patched", False):
            logger.info("gradio MediaStream TD patch 已应用（跳过）")
            return True
        src = inspect.getsource(cls.add_segment)
        if "max(self.max_duration" not in src or "uuid" not in src:
            logger.warning("MediaStream.add_segment 结构变化，TD patch 跳过")
            return False

        async def add_segment(self, data):
            if not data:
                return
            self.segments.append({"id": str(uuid.uuid4()), **data})
            # 与原版差异仅此行：不 +1 蠕变；clamp(ceil(段时长), 1, 5)
            # —— ≤1s 播报批次下 TD 恒 1，重载节奏与缓冲解耦（见 docstring）
            d = min(5, max(1, math.ceil(data["duration"])))
            self.max_duration = d if len(self.segments) == 1 else max(self.max_duration, d)

        add_segment._tts_td_patched = True
        cls.add_segment = add_segment
        logger.info("gradio MediaStream TD patch 已应用")
        return True
    except Exception as e:
        logger.warning(f"gradio MediaStream TD patch 不可用: {e}")
        return False



def _adts_duration(adts: bytes, sample_rate: int) -> float:
    """ADTS 流的真实解码时长（帧数 × 1024 采样 / 采样率）。

    AAC 编码每段有 priming/padding 帧：2s PCM → 48 帧 = 2.048s（实测）。
    playlist 的 EXTINF 必须等于该真实时长——否则 playlist 时间线与解码时长漂移
    （48ms/段累积），MSE 缓冲出现空洞，播放器卡在固定位置跳段/停顿（audit-TTS
    E2E 实证：20 段后播放卡死 ~0.4s 空洞处）。
    """
    frames, i, n = 0, 0, len(adts)
    while i + 7 <= n:
        if adts[i] != 0xFF or (adts[i + 1] & 0xF0) != 0xF0:
            break
        frame_len = ((adts[i + 3] & 0x3) << 11) | (adts[i + 4] << 3) | ((adts[i + 5] & 0xE0) >> 5)
        if frame_len < 7:
            break
        frames += 1
        i += frame_len
    return frames * 1024 / sample_rate if frames else 0.0


# ADTS 采样率表（ISO/IEC 13818-7）
_ADTS_SAMPLE_RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000,
                    22050, 16000, 12000, 11025, 8000, 7350)


def _adts_sample_rate(adts: bytes):
    """从 ADTS 头解析采样率；无法解析返回 None。"""
    if len(adts) < 7 or adts[0] != 0xFF or (adts[1] & 0xF0) != 0xF0:
        return None
    idx = (adts[2] >> 2) & 0x0F
    return _ADTS_SAMPLE_RATES[idx] if idx < len(_ADTS_SAMPLE_RATES) else None


def patch_gradio_audio_transcode() -> bool:
    """gradio 流式音频转码提速 + EXTINF 时长修正 + ADTS 直通（audit-TTS）。

    ① 提速：原版走 pydub（ffprobe+ffmpeg 双进程/批，Windows 实测 ~0.7s/批，
       发布吞吐 0.7x 实时必断流：实测 146.7s 音频发布耗时 269.7s）→ 单 ffmpeg
       进程 stdin→stdout（输入 24k mono s16 wav 已知，免 ffprobe），~0.23s/批。
    ② 时长修正：原版返回源 wav 时长（2.000s），但 AAC 实际解码 2.048s（priming/
       padding 帧）→ playlist 时间线漂移，MSE 出空洞、播放器卡死跳段（E2E 实证）。
       改为按 ADTS 帧数计算真实时长。
    ③ ADTS 直通：输入已是 ADTS（_AdtsStreamer 连续编码流的帧界切片）时原样透传
       ——二次编码会把无 priming 的连续流又切成带 priming 的独立段（音质回退），
       且有代际损失。
    任何失败回退原 pydub 路径。运行时 monkeypatch（服务器启动自动生效），幂等。
    """
    try:
        import io
        import shutil
        import subprocess
        import wave

        import gradio.components.audio as audio_mod

        orig = audio_mod.Audio._convert_to_adts
        if getattr(orig, "_tts_transcode_patched", False):
            logger.info("gradio 音频转码 patch 已应用（跳过）")
            return True

        def _convert_to_adts(data: bytes):
            # ③ ADTS 直通：连续编码流的切片不得二次编码（音质/时长双重保护）
            rate = _adts_sample_rate(data)
            if rate:
                dur = _adts_duration(data, rate)
                if dur > 0:
                    return data, dur
            ffmpeg = shutil.which("ffmpeg")
            if ffmpeg:
                try:
                    out = subprocess.run(
                        [ffmpeg, "-hide_banner", "-loglevel", "error",
                         "-f", "wav", "-i", "pipe:0", "-f", "adts", "pipe:1"],
                        input=data, capture_output=True, timeout=30)
                    if out.returncode == 0 and out.stdout:
                        with wave.open(io.BytesIO(data), "rb") as w:
                            rate = w.getframerate()
                            dur_wav = w.getnframes() / rate
                        # EXTINF 必须是真实解码时长（防时间线漂移出 MSE 空洞）
                        dur = _adts_duration(out.stdout, rate) or dur_wav
                        return out.stdout, dur
                except Exception:
                    pass  # 落回原版
            return orig(data)

        _convert_to_adts._tts_transcode_patched = True
        audio_mod.Audio._convert_to_adts = staticmethod(_convert_to_adts)
        logger.info("gradio 音频转码 patch 已应用（单 ffmpeg 进程 + EXTINF 真实时长）")
        return True
    except Exception as e:
        logger.warning(f"gradio 音频转码 patch 不可用: {e}")
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