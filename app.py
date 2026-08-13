"""
文物知识库 RAG 系统 - Gradio Web UI v2
支持检索结果可视化、闲聊路由、响应时间显示
"""

import sys
import queue
import threading
import time
import json
import hashlib
from contextlib import suppress
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 语音功能（bug-121）：gradio 导入即触发 pydub 导入，pydub 在 import 时缓存
# ffmpeg 查找结果，因此 HLS 流式音频输出所需的 ffmpeg 引导必须在 gradio 之前执行
from src.audio_bootstrap import (
    _adts_duration,
    ensure_ffmpeg,
    patch_gradio_audio_transcode,
    patch_gradio_hls_reuse,
    patch_gradio_media_stream_targetduration,
    patch_gradio_mic_aec,
)

ensure_ffmpeg()

# audit-ASR：onnxruntime 主线程预加载——服务器进程内、工作线程里首次 lazy import
# 实测 4/4 触发 DLL 初始化失败（DLL load failed: onnxruntime_pybind11_state，
# 用户生产 VAD 初始化失败即此根因）；主线程提前 import 后，工作线程只是缓存命中。
try:
    import onnxruntime as _onnxruntime_preload  # noqa: F401
except Exception:
    pass  # 未安装：VAD 初始化路径会给出可操作报错并降级

# audit-ASR：gradio 6.22 流式输出收尾 KeyError 修复（未产出音频的事件末趟 None 触发
# end_stream KeyError，收尾输出丢失；E2E 实证）
from src.audio_bootstrap import patch_gradio_stream_endstream_guard  # noqa: E402
patch_gradio_stream_endstream_guard()

# 必须在真实浏览器使用前应用：gradio 6.22 前端 bug——同一 Audio 组件多轮流式值
# 只创建一次 hls（Se 标记不重置），第 2 轮起自动播报无声（bug-121 实测）；
# audit-TTS 补充：原生 HLS 分支一次性赋值修复 + hls.js 缓冲 1s→60s（停顿放大器）
patch_gradio_hls_reuse()
# audit-ASR：getUserMedia 强制 AEC/降噪/增益——一体机外放下 TTS 播报被麦克风
# 拾取会被 VAD 判为语音 → 自触发打断/唤醒死循环（浏览器 AEC 以本页输出为参考）
patch_gradio_mic_aec()
from src.audio_bootstrap import verify_frontend_patches  # noqa: E402
verify_frontend_patches()  # 复读磁盘 JS 确认 3+1 标记落盘（写权限/版本漂移防御）
# audit-TTS：修正服务端 MediaStream TARGETDURATION 每段 +1 蠕变（无更新时 hls.js
# 按 TD/2 重载 playlist，长回答发布间断会被放大成数十秒停顿，仿真实证 22.5s）
patch_gradio_media_stream_targetduration()
# audit-TTS：流式音频转码提速 + EXTINF 真实时长（pydub 双进程 0.7s/批 → 单 ffmpeg
# 0.23s/批；AAC priming 致声明漂移 48ms/段出 MSE 空洞——E2E 实证卡顿跳段）
patch_gradio_audio_transcode()

import gradio as gr
from loguru import logger

from src.config import settings
from src.utils import setup_logger, clean_text_for_tts
from src.asr import IflytekASR, _to_pcm16k, load_dict
from src.tts import CosyVoiceTTS
from src.rag_pipeline import RAGPipeline
from src.project import project_manager

# Gradio 6.0 破坏性变更兼容（bug-098）：
#   - Chatbot 的 show_copy_button / bubble_full_width 参数被移除，改用 buttons / layout
#   - Blocks 构造器的 theme / css 参数被移除，改到 launch() 传入
# 通过主版本号分支，保证 4.x / 5.x / 6.x 均可运行。
_GRADIO_MAJOR = int(gr.__version__.split(".")[0])

_UI_THEME = gr.themes.Soft(
    primary_hue="amber",
    secondary_hue="stone",
    neutral_hue="slate",
)
_UI_CSS = """
.gradio-container { max-width: 1100px !important; margin: auto; }
.chat-message { font-size: 15px; line-height: 1.6; }
footer { display: none !important; }
.chunks-panel { border-left: 3px solid #e5a23b; padding-left: 12px; }
"""


def _extract_text(content):
    """统一提取消息文本内容，兼容 Gradio 6.x 的 content 格式。

    bug-104 修复：Gradio 6 的 Chatbot.preprocess 会把消息 content 从 str
    转为多模态 list 格式（如 [{"type": "text", "text": "..."}]），
    多轮对话时 _iter_history_pairs 取到的 content 为 list，
    导致 _convert_history 中 .find() 崩溃。此处兼容 str / list / 数字 / None。
    """
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    if isinstance(content, (int, float)):
        return str(content)
    return content  # str 或 None


def _iter_history_pairs(history: list):
    """归一化 Gradio 4/5（tuple 列表）与 6.x（dict 列表）的 Chatbot 历史格式。

    bug-101 修复：Gradio 6.0 起 Chatbot 消息格式从 [(user, assistant), ...]
    改为 [{"role": ..., "content": ...}, ...]。此处按元素类型自动检测（不依赖
    Gradio 版本号），统一转换为 (user_msg, assistant_msg) 对，供 _convert_history
    复用原有处理逻辑。
    bug-104 修复：content 统一经 _extract_text 提取为文本（Gradio 6 preprocess
    会把 content 转为 list[dict] 多模态格式）。
    """
    user_msg = None
    for msg in history:
        if isinstance(msg, dict):
            # Gradio 6.x：user/assistant 交替的 dict 消息
            role = msg.get("role")
            content = _extract_text(msg.get("content", ""))
            if role == "user":
                user_msg = content
            elif role == "assistant":
                yield user_msg, content
                user_msg = None
        else:
            # Gradio 4/5：成对的 (user, assistant) 元组/列表
            yield (
                _extract_text(msg[0]) if len(msg) > 0 else None,
                _extract_text(msg[1]) if len(msg) > 1 else None,
            )
            user_msg = None
    if user_msg is not None:
        # 末尾未配对的 user 消息（对应回复为空）
        yield user_msg, None


def _append_conversation(history: list, user_msg: str, assistant_msg: str) -> None:
    """按 Gradio 版本追加一轮对话（用户消息 + 助手消息）。

    bug-101：6.x 的 Chatbot 要求 dict 消息；4/5.x 用 (user, assistant) 元组。
    """
    if _GRADIO_MAJOR >= 6:
        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": assistant_msg})
    else:
        history.append((user_msg, assistant_msg))


def _update_last_assistant(history: list, user_msg: str, assistant_msg: str) -> None:
    """按 Gradio 版本更新最后一条 assistant 消息的内容。

    bug-101：6.x 中最后一条是 assistant dict，直接改 content；4/5.x 替换整个元组。
    """
    if _GRADIO_MAJOR >= 6:
        history[-1]["content"] = assistant_msg
    else:
        history[-1] = (user_msg, assistant_msg)

# 对话历史与检索来源的分隔符
# 注意：此分隔符与 src/rag_pipeline.py 中的 CHUNK_SEPARATOR 用途不同：
#   - HISTORY_SEPARATOR（本文件）：用于对话历史与检索来源标注之间的分隔
#   - CHUNK_SEPARATOR（rag_pipeline.py）：用于上下文 chunk 之间的分隔
HISTORY_SEPARATOR = "\n\n---\n\n"
HISTORY_SEPARATOR_OLD = "\n---\n"

pipeline: RAGPipeline = None
# bug-011 修复：使用 None 而非空字符串表示空项目，避免与 None 比较时类型不一致
_current_project: Optional[str] = None
# bug-014 修复：添加线程锁，保护全局 pipeline 变量的并发访问
_pipeline_lock = threading.Lock()


def init_pipeline(project_id: str = ""):
    """初始化 RAG 流水线（指定项目）"""
    global pipeline, _current_project
    # 统一规范化：None 和 "" 都视为空（bug-013）
    project_id = project_id or ""
    # 加锁初始化（双重检查锁定模式）
    with _pipeline_lock:
        if pipeline is not None and project_id == _current_project:
            return pipeline
        logger.info(f"初始化 RAG 流水线 - 项目: {project_id or '默认'}")
        # bug-038 修复：锁内创建后用局部变量持有，预热与返回值都使用局部引用，
        # 避免其他线程在锁外替换全局 pipeline 后返回错误项目的实例（竞态）
        new_pipeline = RAGPipeline(
            local_mode=True,
            enable_cache=True,
            memory_mode=settings.qdrant_memory_mode,
            project_id=project_id or None,
        )
        # bug-059 修复：替换前关闭旧 pipeline 的向量库连接，
        # 避免频繁切换项目累积 Qdrant 文件句柄/连接
        if pipeline is not None:
            with suppress(Exception):
                pipeline.vector_store.close()
        pipeline = new_pipeline
        _current_project = project_id
        # 并发预热竞态修复：预热在锁内完成后才释放锁，
        # 避免并发请求在预热完成前拿到 _is_built=False 的 pipeline，
        # 导致 answer_question/get_system_status 误报"知识库尚未构建"
        try:
            new_pipeline._ensure_knowledge_base()
            new_pipeline.warmup()
            logger.info("RAG 流水线就绪")
        except RuntimeError as e:
            logger.warning(f"知识库未构建: {e}")
        except Exception as e:
            logger.warning(f"预热失败: {e}")
    return new_pipeline


# ========== 语音功能（bug-121）：ASR 语音输入 ==========

def _pcm_rms(pcm: bytes) -> float:
    """计算 16bit PCM 的 RMS 音量（静音检测用，0-32768 量级）。"""
    import array
    import math

    if len(pcm) % 2:
        pcm = pcm[: len(pcm) - 1]  # 奇数长度截断（测试数据/异常流）
    samples = array.array("h")
    samples.frombytes(pcm)
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def asr_stream_chunk(audio_filepath, state, project_id: str = ""):
    """Gradio Audio stream 事件：音频块送达 → 送入讯飞 ASR → 实时更新输入框。

    差分发送：兼容"增量块"与"累计录音"两种前端语义（只发送新增的 PCM 长度）。
    VAD（服务端静默判定）或最长录音超时 → 自动结束转写，填入最终文本。
    """
    if not (settings.xfyun_app_id and settings.xfyun_api_key and settings.xfyun_api_secret):
        yield state, gr.update(), gr.update(value="未配置讯飞密钥（XFYUN_APP_ID/API_KEY/API_SECRET），请在 .env 补充")
        return
    if not audio_filepath:
        yield state, gr.update(), gr.update(value="")
        return
    if state is None:
        cfg = load_dict(project_id, settings.asr_dict_dir)
        state = {
            "session": IflytekASR(
                settings.xfyun_app_id, settings.xfyun_api_key, settings.xfyun_api_secret,
                language=settings.asr_language, accent=settings.asr_accent,
                vad_eos_ms=settings.asr_vad_eos, hotwords=cfg["hotwords"],
                corrections=cfg["corrections"],
            ),
            "sent_bytes": 0,
            "started": time.time(),
            "finalized": False,
            "fed": False,
            "last_key": None,
            "pcm_buffer": b"",
        }
    if state["finalized"]:
        # 已识别完成：忽略所有后续块；msg 不更新，voice_status 置空（避免空 update 到 Markdown
        # 导致前端 f.message.trim 报错，且不覆盖 TTS 播报提示）。
        # 注（audit-F27 评估后维持原决策）：若 stop_recording 事件未到达，state 残留期间
        # 新录音首块可能被忽略，该场景依赖 gradio 事件时序、概率低；而 finalized 后
        # 继续 feed 可能反复重建会话导致无限识别（test_voice_ui.TestAsrGuards 明确防护），
        # 两害相权维持“忽略后续块”。
        yield state, gr.update(), gr.update(value="")
        return
    asr = state["session"]
    try:
        if time.time() - state["started"] > settings.asr_max_duration:
            # 超时兜底：超过最长录音时长自动收尾（不依赖 stop 事件，防止录音未停导致无限识别）
            final = asr.finish()
            state["finalized"] = True
            logger.info(f"ASR 最终结果(超时): {final}")
            yield state, gr.update(value=final), gr.update(value="已识别完成，可修改后发送")
            return
        raw = Path(audio_filepath).read_bytes()
        key = hashlib.md5(raw).hexdigest()
        if state["fed"] and key == state["last_key"]:
            # gradio 6.22 录音中每 0.5s 发一个独立 wav 增量块（size 恒定）；
            # 相同块重复 = 录音已停止（value 稳定）→ 完成识别
            if not state["finalized"]:
                final = asr.finish()
                state["finalized"] = True
                logger.info(f"ASR 最终结果: {final}")
                yield state, gr.update(value=final), gr.update(value="已识别完成，可修改后发送")
            else:
                # 已 finalized：msg 不更新，voice_status 置空（避免空 update 到 Markdown 报错）
                yield state, gr.update(), gr.update(value="")
            return
        logger.info(f"ASR stream 回调: file={Path(audio_filepath).name} size={len(raw)} magic={raw[:4].hex()}")
        # 新块（录音中增量）：每块独立转 PCM16k → 追加累积 → 增量 feed（只发新增部分）
        pcm_block = _to_pcm16k(raw, settings.asr_sample_rate)
        state["pcm_buffer"] = state["pcm_buffer"] + pcm_block
        new_pcm = state["pcm_buffer"][state["sent_bytes"]:]
        if new_pcm:
            logger.info(f"ASR feed: +{len(new_pcm)} bytes (buffer {len(state['pcm_buffer'])}, 已发 {state['sent_bytes']})")
            asr.feed(new_pcm)
            state["sent_bytes"] += len(new_pcm)
            state["fed"] = True
            # 等待讯飞 wpgs 部分结果返回（动态修正 apd 追加/rpl 替换），实现边说边出字
            time.sleep(0.2)
            # 客户端静音检测：连续静音块（0.5s/块）→ 自动结束（信飞 vad 不主动结束，实测）
            if _pcm_rms(pcm_block) < settings.asr_silence_threshold:
                state["silent_blocks"] = state.get("silent_blocks", 0) + 1
            else:
                state["silent_blocks"] = 0
            if state["silent_blocks"] >= settings.asr_silence_blocks:
                final = asr.finish()
                state["finalized"] = True
                logger.info(f"ASR 最终结果(静音自动结束): {final}")
                yield state, gr.update(value=final), gr.update(value="已识别完成，可修改后发送")
                return
        state["last_key"] = key
        if asr.is_final():
            # 服务端已自动结束（vad 兜底）→ 立即收尾，避免继续 feed 到已关闭连接（Broken pipe）
            final = asr.finish()
            state["finalized"] = True
            logger.info(f"ASR 最终结果(vad): {final}")
            yield state, gr.update(value=final), gr.update(value="已识别完成，可修改后发送")
            return
        # 实时显示当前最佳转写文本（可编辑，停止后填最终文本）
        text = asr.correct(asr.current_text)
        if text:
            logger.info(f"ASR 实时文本: {text}")
        yield state, gr.update(value=text) if text else gr.update(), gr.update(value="识别中…")
        return
    except Exception as e:
        logger.warning(f"ASR 音频处理失败: {e}")
        # 清理坏会话（连接已断），重置 state，下次录音重建，避免循环报错
        try:
            asr.close()
        except Exception:
            pass
        state = None
        yield state, gr.update(), gr.update(value=f"识别出错: {e}")
        return


def asr_stream_stop(state, project_id: str = ""):
    """Gradio Audio stop 事件：用户停止录音 → 结束 ASR 会话，返回最终文本。

    gradio 6.22 停止后前端不再发送新块；此处 finish（幂等）收尾并清空会话。
    """
    if state and state.get("session"):
        final = state["session"].finish()
        state["finalized"] = True
        state = None
        yield state, gr.update(value=final), gr.update(value="已识别完成，可修改后发送")
    else:
        yield state, gr.update(), gr.update(value="")


# ========== 语音助手（audit-ASR）：唤醒 + VAD + 双计时 + 打断 ==========

class _BroadcastToken:
    """一次播报（回答/欢迎语）的生命周期句柄：cancel=打断请求，done=收尾完成。"""

    __slots__ = ("kind", "cancel", "done", "at")

    def __init__(self, kind: str):
        self.kind = kind
        self.cancel = threading.Event()
        self.done = threading.Event()
        self.at = time.time()


_broadcast_tokens: dict = {}
_broadcast_lock = threading.Lock()

# 待提交问题存储（audit-ASR 修复轮2）：问题文本**不走组件值**——gradio 6.22 会把
# 更新指令串线进组件值（用户复测实证：聊天气泡出现 [['add','[value]','问题\u200b#2']]
# 乱码且 nonce 未被剥离）。组件只传纯数字 nonce，文本存此处按会话取。
_pending_questions: dict = {}
_pending_greet: set = set()   # 待播欢迎语的会话键（组件值可能串线，以此为准）
_pending_lock = threading.Lock()


def _session_key(request) -> str:
    try:
        return (request.session_hash if request else None) or "anon"
    except Exception:
        return "anon"


def _register_broadcast(request, kind: str) -> _BroadcastToken:
    """注册新播报；同会话未完结的旧播报先取消（新提问打断旧回答的残留发布）。"""
    token = _BroadcastToken(kind)
    key = _session_key(request)
    with _broadcast_lock:
        old = _broadcast_tokens.get(key)
        if old is not None and not old.done.is_set():
            old.cancel.set()
        _broadcast_tokens[key] = token
    return token


def _active_broadcast(request) -> Optional[_BroadcastToken]:
    """当前活跃播报。cancel 中的视为非激活：打断后 respond 收尾（done）需 ~0.1s，
    期间若按 active 上报，状态机会被抖回 broadcast 态、吞掉打断出的新问题。"""
    with _broadcast_lock:
        tok = _broadcast_tokens.get(_session_key(request))
    if tok is None or tok.done.is_set() or tok.cancel.is_set():
        return None
    return tok


_assist_init_error = ""  # VAD 初始化失败原因（降级提示上屏：用户不用翻日志就知道修什么）


def _create_voice_assistant(project_id: str = ""):
    """构建 VoiceAssistant；VAD 不可用 → None 且 _assist_init_error 记录原因（降级不崩）。"""
    global _assist_init_error
    from src.vad import create_vad
    from src.voice_assistant import VoiceAssistant, make_corrector

    try:
        vad = create_vad(
            model_path=settings.silero_vad_model_path, threshold=settings.vad_threshold,
            min_speech_ms=settings.vad_min_speech_ms, min_silence_ms=settings.vad_min_silence_ms,
            pad_ms=settings.vad_speech_pad_ms, max_speech_s=settings.vad_max_speech_s,
            sample_rate=settings.asr_sample_rate)
    except Exception as e:
        _assist_init_error = (str(e) or type(e).__name__)[:150]
        logger.warning(f"VAD 初始化失败（语音助手不可用）: {e}")
        return None
    _assist_init_error = ""
    cfg = load_dict(project_id, settings.asr_dict_dir)
    wake_words = cfg.get("wake_words") or [
        w.strip() for w in settings.asr_wake_words.split(",") if w.strip()]

    def asr_factory():
        # 纠错由 FSM 的 correct_fn 统一施加（先归一后纠错，唤醒匹配容错），实例不再重复配置
        return IflytekASR(
            settings.xfyun_app_id, settings.xfyun_api_key, settings.xfyun_api_secret,
            language=settings.asr_language, accent=settings.asr_accent,
            vad_eos_ms=settings.asr_vad_eos, hotwords=cfg["hotwords"])

    return VoiceAssistant(
        vad, asr_factory, wake_words=wake_words,
        correct_fn=make_corrector(cfg["corrections"]),
        initial_wait_s=settings.asr_initial_wait_s,
        extend_wait_s=settings.asr_extend_wait_s,
        greeting=cfg.get("wake_greeting") or settings.asr_wake_greeting)


def voice_stream_dispatch(audio_filepath, state, project_id: str = "", request: gr.Request = None):
    """语音流事件分发：VOICE_ASSIST_ENABLED 走语音助手，否则手动模式（bug-121 语义不变）。

    输出恒为 5 元组 [asr_state, msg, voice_status, auto_q, greet_trig]（手动模式后两个 no-op）。
    """
    if settings.voice_assist_enabled:
        yield from _assist_stream_chunk(audio_filepath, state, project_id, request)
        return
    for s, m, v in asr_stream_chunk(audio_filepath, state, project_id):
        yield s, m, v, gr.update(), gr.update()


def _assist_stream_chunk(audio_filepath, state, project_id, request):
    """语音助手路径：常驻录音 → PCM16k → 播报状态同步 → FSM → 动作翻译为 gradio 输出。"""
    no = gr.update()
    if not (settings.xfyun_app_id and settings.xfyun_api_key and settings.xfyun_api_secret):
        yield state, no, gr.update(value="未配置讯飞密钥（XFYUN_APP_ID/API_KEY/API_SECRET），请在 .env 补充"), no, no
        return
    if not audio_filepath:
        yield state, no, no, no, no
        return
    if state is None:
        assistant = _create_voice_assistant(project_id)
        if assistant is None:
            state = {"assist_failed": True}
            yield state, no, gr.update(
                value=f"VAD 初始化失败：{_assist_init_error or '未知原因'}"
                      f"（置 VOICE_ASSIST_ENABLED=false 回手动模式）"), no, no
            return
        state = {"assistant": assistant, "nonce": 0}
        logger.info(f"语音助手会话已启动（VAD 监听 + 唤醒词「{settings.asr_wake_words}」）")
    if state.get("assist_failed"):
        yield state, no, no, no, no
        return
    assistant = state["assistant"]
    try:
        raw = Path(audio_filepath).read_bytes()
        pcm_block = _to_pcm16k(raw, settings.asr_sample_rate)
        token = _active_broadcast(request)
        actions = assistant.notify_broadcast(token is not None)
        actions += assistant.process_chunk(pcm_block)
    except Exception as e:
        logger.warning(f"语音助手音频处理失败: {e}")
        yield state, no, gr.update(value=f"语音助手异常: {e}"), no, no
        return
    msg_u, status_u, auto_u, greet_u = no, no, no, no
    for a in actions:
        if a.kind == "msg":
            msg_u = gr.update(value=a.text)
        elif a.kind == "status":
            status_u = gr.update(value=a.text)
            logger.info(f"语音助手状态: {a.text}")
        elif a.kind == "submit":
            state["nonce"] += 1
            with _pending_lock:
                _pending_questions[_session_key(request)] = a.text
            auto_u = state["nonce"]  # gr.State：递增多调必触发 .change（deep_hash 检测）
        elif a.kind == "greet":
            state["nonce"] += 1
            with _pending_lock:
                _pending_greet.add(_session_key(request))
            greet_u = state["nonce"]
        elif a.kind == "barge_in" and token is not None:
            token.cancel.set()
    yield state, msg_u, status_u, auto_u, greet_u


def voice_stop_dispatch(state, project_id: str = ""):
    """停止录音分发：assist 模式关闭状态机会话；手动模式照旧。输出恒 5 元组。"""
    if settings.voice_assist_enabled:
        if state and state.get("assistant"):
            state["assistant"].close()
        yield None, gr.update(), gr.update(value="录音已停止（刷新页面可重启语音助手）"), gr.update(), gr.update()
        return
    for s, m, v in asr_stream_stop(state, project_id):
        yield s, m, v, gr.update(), gr.update()


def auto_respond(nonce, chat_history, stream, project, tts_enabled, request: gr.Request = None):
    """语音助手自动提交（audit-ASR 需求3）：隐藏 Textbox .change 触发。

    独立事件（concurrency_limit=1 约束：不得塞进 stream 事件，否则 respond 运行的
    30s+ 内音频块排队、打断检测失效）。问题文本取自服务端 pending 存储（消费一次性）；
    nonce 仅为触发器，无 pending → no-op（页面加载等伪触发兜底）。
    """
    key = _session_key(request)
    with _pending_lock:
        question = _pending_questions.pop(key, "").strip()
    if not question:
        yield "", chat_history, "[]", gr.update(), gr.update(), gr.update()
        return
    logger.info(f"语音助手自动提交问答: {question}")
    yield from respond(question, chat_history, stream, project, tts_enabled, request)


_greeting_mem: dict = {}  # 欢迎语 PCM 内存缓存：key → 24k mono PCM


def _wake_greeting_text(project: str = "") -> str:
    """唤醒应答语文本：asr_dict.json 的 wake_greeting（项目级覆盖）> settings 默认。"""
    cfg = load_dict(project, settings.asr_dict_dir)
    return cfg.get("wake_greeting") or settings.asr_wake_greeting


def _greeting_pcm(project: str = "") -> Optional[bytes]:
    """欢迎语 PCM（24k mono 16bit）：首次合成，之后内存/磁盘缓存复用（零合成延迟）。"""
    import io
    import wave

    text = _wake_greeting_text(project)
    key = hashlib.sha1(
        f"{settings.tts_model}|{settings.tts_voice}|{settings.tts_speech_rate}|{text}".encode("utf-8")
    ).hexdigest()[:12]
    if key in _greeting_mem:
        return _greeting_mem[key]
    cache_dir = settings.project_root / "data" / "processed" / "tts_cache"
    path = cache_dir / f"greeting_{key}.wav"
    try:
        if path.exists():
            wav_bytes = path.read_bytes()
        else:
            tts = _init_tts()
            if tts is None:
                return None
            wav_bytes = tts.synthesize_sentence(text)
            cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(wav_bytes)
            logger.info(f"欢迎语已合成并缓存: {path.name}（{text}）")
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            pcm = w.readframes(w.getnframes())  # CosyVoice 默认 WAV_24000HZ_MONO_16BIT
        _greeting_mem[key] = pcm
        return pcm
    except Exception as e:
        logger.warning(f"欢迎语合成/读取失败（降级无音频）: {e}")
        return None


def play_greeting(trigger, project: str = "", tts_enabled: bool = True,
                  request: gr.Request = None):
    """唤醒应答（audit-ASR 需求1 + 优化轮3 提速）。

    音频走**预置静态文件 + 前端 JS 直播**（GET /__voice_greeting，启动预合成 + 客户端
    预加载）：原 HLS 链路（编码 ~0.2s + 发布 + 客户端起播 ~0.4s+）对固定应答语纯属
    浪费，直播 ~0.1s 起播。本函数只注册 token 并等待音频时长（驱动状态机 播报态→
    倾听态 迁移）+ 输出 tts_status；可打断（cancel 即提前收尾）。
    门闩：组件值串线会产生伪触发（E2E 实录 trigger='[]'）→ 以 pending 存储为准，
    且必须先判再注册 token——伪触发注册会误取消进行中的回答播报。
    """
    no = gr.update()
    key = _session_key(request)
    with _pending_lock:
        pending = key in _pending_greet
        if pending:
            _pending_greet.discard(key)
    if not trigger or not pending:
        yield no, no, no
        return
    token = _register_broadcast(request, "greeting")
    try:
        pcm = _greeting_pcm(project) if tts_enabled else None
        if not pcm:
            yield no, no, gr.update(value=("✅ 已唤醒（语音播报已关闭），请提问" if not tts_enabled
                                           else "✅ 已唤醒（欢迎语不可用），请提问"))
            return
        duration = len(pcm) / 48000.0  # 24k 16bit mono
        yield no, no, gr.update(value="🔊 应答中…")
        # 等待音频时长（前端 JS 直播静态文件），每 0.1s 检查打断
        deadline = time.time() + duration + 0.3  # +客户端起播余量
        while time.time() < deadline and not token.cancel.is_set():
            time.sleep(0.1)
        if not token.cancel.is_set():
            yield no, no, gr.update(value="✅ 已唤醒，请提问")
    except Exception as e:
        logger.warning(f"欢迎语播报异常: {e}")
        yield no, no, gr.update(value=f"欢迎语播报异常: {e}")
    finally:
        token.done.set()


class _GreetingAudioMiddleware:
    """GET /__voice_greeting → 唤醒应答 wav（优化轮3：预置音频，前端预加载直播）。

    no-cache：应答语可配置变更，浏览器每次 revalidate（ETag/mtime 变化即拿新文件）。
    """

    PATH = "/__voice_greeting"

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != self.PATH:
            await self.app(scope, receive, send)
            return
        pcm = _greeting_pcm("")  # 默认项目（一体机单项目场景；项目级 wake_greeting 覆写暂走默认）
        if not pcm:
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})
            return
        body = _wrap_pcm(pcm, 24000)
        await send({"type": "http.response.start", "status": 200, "headers": [
            (b"content-type", b"audio/wav"),
            (b"content-length", str(len(body)).encode()),
            (b"cache-control", b"no-cache"),
        ]})
        await send({"type": "http.response.body", "body": body})


# ========== 语音功能（bug-121）：TTS 语音播报（句子级流式） ==========

def _extract_last_answer_text(history) -> str:
    """取对话历史最后一条 assistant 正文（去掉 **[检索来源]** 及之后内容）。"""
    if not history:
        return ""
    last = history[-1]
    if isinstance(last, dict):
        content = _extract_text(last.get("content", ""))
    elif isinstance(last, (list, tuple)) and len(last) > 1:
        content = _extract_text(last[1])
    else:
        content = ""
    marker = "**[检索来源]**"
    idx = content.find(marker)
    if idx >= 0:
        content = content[:idx].rstrip()
        # 去掉正文末尾残留的分隔符（\n---\n 或 \n\n---\n\n），与 _convert_history 一致
        if content.endswith("---"):
            content = content[:-3].rstrip()
    return content.strip()


class _PauseCompressor:
    """TTS PCM 流静默压缩（audit-TTS 第五轮）。

    根因（真实 API 实证）：喂入式合成在每个 streaming_call 边界烙入 ~0.9s 静默——
    整段喂 0 处、20 字块喂 4 处、整句喂 3 处，边界位置即静默位置；减少喂入次数
    只能减少不能消除，且与首播小批次策略冲突。故在 PCM 流上压缩：保留每段静默的
    前 cap_s（自然气口），超出部分丢弃——逐 20ms 窗实时判决（“本窗到来时当前静默
    是否已超 cap”），无需前瞻、零额外延迟。峰值 <300（≈-40dB）判静默。
    """

    def __init__(self, rate: int = 24000, cap_s: float = 0.35, thresh: int = 300):
        import numpy as np

        self._np = np
        self.rate = rate
        self.win = int(rate * 0.02)               # 20ms 窗（采样数）
        self.cap_windows = max(1, round(cap_s / 0.02))
        self.thresh = thresh
        self._buf = bytearray()
        self._silent_run = 0                      # 当前连续静默窗数
        self.dropped_s = 0.0                      # 累计丢弃时长（诊断）

    def feed(self, pcm: bytes) -> bytes:
        self._buf.extend(pcm)
        out = bytearray()
        wbytes = self.win * 2
        while len(self._buf) >= wbytes:
            w = bytes(self._buf[:wbytes])
            del self._buf[:wbytes]
            peak = int(self._np.abs(self._np.frombuffer(w, dtype=self._np.int16)).max())
            if peak < self.thresh:
                self._silent_run += 1
                if self._silent_run > self.cap_windows:
                    self.dropped_s += 0.02
                    continue                       # 超出 cap 的静默窗：丢弃
            else:
                self._silent_run = 0
            out += w
        return bytes(out)

    def flush(self) -> bytes:
        """收尾：残余不足一窗的字节原样放出。"""
        out = bytes(self._buf)
        self._buf.clear()
        return out


def _audit_silence(pcm: bytes, rate: int = 24000, min_s: float = 0.6) -> list:
    """播报内容静默审计（audit-TTS 第五轮）：返回 ≥min_s 的静默区间 [(起s, 止s)]。

    背景：客户端遥测零上报但用户听到多处停顿 → waiting/stalled 未触发说明不是
    缓冲断流，停顿只可能烙在音频内容里（播放器平滑播过静默不产生任何事件）。
    重播 PCM 即实际播放内容，直接扫描它给内容侧定性。20ms 窗峰值 <300 视为静默；
    贴边（含头/尾）的静默段属起音/拖尾，不计。
    """
    import numpy as np

    if len(pcm) < rate * 2:  # 短于 1s 不审
        return []
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.int32)
    win = int(rate * 0.02)
    n = len(samples) // win
    if n == 0:
        return []
    peaks = np.abs(samples[: n * win].reshape(n, win)).max(axis=1)
    silent = peaks < 300
    runs, start = [], None
    for i, s in enumerate(silent):
        if s and start is None:
            start = i
        elif not s and start is not None:
            if (i - start) * 0.02 >= min_s:
                runs.append((round(start * 0.02, 2), round(i * 0.02, 2)))
            start = None
    if start is not None and (n - start) * 0.02 >= min_s:
        runs.append((round(start * 0.02, 2), round(n * 0.02, 2)))
    total = len(samples) / rate
    # 贴头（起≈0）/贴尾（止≈total）的段是起音/拖尾，排除
    return [r for r in runs if r[0] > 0.02 and r[1] < total - 0.03]


def _audit_silence_log(pcm: bytes) -> None:
    """审计结果落日志（后台线程执行，勿阻塞发布）。"""
    try:
        runs = _audit_silence(pcm)
        if runs:
            logger.warning(
                f"播报静默审计: {len(runs)} 处 ≥0.6s 静默 {runs}——"
                f"与听感停顿位置吻合 → 停顿烙在音频内容里（TTS 侧）；"
                f"无此日志却有停顿 → 播放/传输侧")
        else:
            logger.info("播报静默审计: 无 ≥0.6s 静默（音频内容连续）")
    except Exception as e:
        logger.warning(f"播报静默审计失败: {e}")


def _write_replay_wav(chunks):
    """合并各句 wav 字节写入重播缓存文件，返回 Path。

    bug-121：多个 wav 直接 b"".join 会保留重复头（后段头被当 PCM，播放到一半损坏），
    此处用 wave 只取 PCM 拼接，保留第一个 wav 的参数。
    """
    import io
    import wave

    cache_dir = settings.project_root / "data" / "processed" / "tts_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    # audit-F12 修复：全局单文件 last_answer.wav 在多用户/多会话并发时互相覆写
    # （用户 A 的点重播会播放用户 B 的答案）。改为按请求唯一命名 + 保留最近 5 个。
    import uuid
    path = cache_dir / f"last_answer_{uuid.uuid4().hex[:8]}.wav"
    if not chunks:
        path.write_bytes(b"")
        return path
    with wave.open(io.BytesIO(chunks[0]), "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
    pcm_parts = []
    for c in chunks:
        with wave.open(io.BytesIO(c), "rb") as w:
            pcm_parts.append(w.readframes(w.getnframes()))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"".join(pcm_parts))
    path.write_bytes(buf.getvalue())
    # audit-F12：清理过期重播文件，仅保留最近 5 个，避免磁盘无限增长
    try:
        replays = sorted(cache_dir.glob("last_answer_*.wav"),
                         key=lambda p: p.stat().st_mtime, reverse=True)
        for old in replays[5:]:
            old.unlink(missing_ok=True)
    except Exception:
        pass
    return path


_SENTENCE_END = "，。！？…；、："  # 逗号/句号等（用户要求按标点划分，避免句中切导致卡壳）


_SENTENCE_FINAL = "。！？；!?\n"   # 句末标点（喂入单元只在这里切）
_CLAUSE_PAUSE = "，、：,."        # 首播兜底可用的停顿标点


def _cut_at_last(text: str, chars: str, limit: int) -> int:
    """text[:limit] 内最后一个 chars 字符的下一位置；无则 -1。"""
    head = text[:limit]
    for i in range(len(head) - 1, -1, -1):
        if head[i] in chars:
            return i + 1
    return -1


def _take_first_unit(buf: str, hard_cap: int = 80):
    """首播喂入单元（audit-TTS 第五轮断句修复）。

    实证：streaming_call 边界会烙静默/韵律断层——边界在句中即“断句不连贯”。
    故首播单元也必须落在标点边界：① 有句末标点 → 切到句末；② ≥8 字有句读标点
    → 切到最后一个（逗号处停顿自然，保首播速度——实测整句等待会让首播退到
    ~1.3s，逗号兜底与旧 8 字策略同速但边界落在自然停顿处）；③ ≥hard_cap 硬切。
    """
    text = buf.strip()
    if not text:
        return "", buf
    cut = _cut_at_last(text, _SENTENCE_FINAL, hard_cap)
    if cut > 0:
        return text[:cut], buf[len(buf) - len(text) + cut:]
    if len(text) >= 8:
        cut = _cut_at_last(text, _SENTENCE_END, hard_cap)  # 含逗号等的宽标点集
        if cut > 0:
            return text[:cut], buf[len(buf) - len(text) + cut:]
    if len(text) >= hard_cap:
        return text[:hard_cap], buf[len(buf) - len(text) + hard_cap:]
    return "", buf


def _take_feed_unit(buf: str, min_chars: int, max_chars: int = 200,
                    starve: bool = False):
    """后续喂入单元：批量完整句（攒 ≥min_chars 字且切在句末标点；max_chars 上限；
    starve=True（距上次喂入超阈值，引擎可能断粮）时有完整句即喂）。
    无完整句且缓冲超 max_chars 时硬切（吞掉切点后剩余缓冲开头的孤立标点）。
    """
    text = buf.lstrip()
    if not text:
        return "", buf
    lead = len(buf) - len(text)
    cut = _cut_at_last(text, _SENTENCE_FINAL, max_chars)
    if cut > 0 and (cut >= min_chars or starve):
        return text[:cut], buf[lead + cut:]
    if cut <= 0 and len(text) >= max_chars:
        rest = text[max_chars:].lstrip(_SENTENCE_END + " ")  # 吞孤立标点（bug-121）
        return text[:max_chars], rest
    return "", buf


def _take_sentence(text: str, chunk_chars: int):
    """从攒字缓冲区开头取一段待合成文本（按标点逗号/句号等切分）。

    bug-121：攒够 chunk_chars 字后优先在标点处切分（避免句中划分导致 CosyVoice
    卡壳/孤立标点 invalid text 丢字）：① 前 chunk_chars 字内最后一个标点 → 切到标点后
    （含标点）；② 前段无标点 → 向后找第一个标点（最多 chunk_chars*2）；③ 仍无标点
    → 硬切，并吞掉切点后的标点（避免剩余段以孤立标点开头导致下次合成失败丢字）。

    Returns:
        (seg, rest)：seg 为本次合成文本（以文字或标点结尾），rest 为剩余待攒文本。
    """
    text = text.strip()
    if not text:
        return "", ""
    if len(text) <= chunk_chars:
        return text, ""
    # ① 前 chunk_chars 字内最后一个标点（在标点后切，含标点）
    head = text[:chunk_chars]
    cut = -1
    for i in range(len(head) - 1, -1, -1):
        if head[i] in _SENTENCE_END:
            cut = i + 1
            break
    # ② 前段无标点 → 往后找第一个标点
    if cut < 0:
        probe = text[: chunk_chars * 2]
        for i in range(chunk_chars, len(probe)):
            if probe[i] in _SENTENCE_END:
                cut = i + 1
                break
    # ③ 仍无标点 → 硬切，吞掉切点后的标点（避免剩余以孤立标点开头）
    if cut < 0:
        cut = chunk_chars
        while cut < len(text) and text[cut] in _SENTENCE_END:
            cut += 1
    return text[:cut], text[cut:].strip()


def _wav_duration(wav_bytes: bytes) -> float:
    """wav 字节时长（秒），解析失败返回 0。"""
    import io
    import wave

    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        return 0.0


def _merge_wavs(chunks: list) -> bytes:
    """合并多个 wav 字节为单个 wav（去重复头，仅拼接 PCM）。

    bug-121：直接 b"".join 会保留重复头（后段头被当 PCM 播放损坏），
    与 _write_replay_wav 同逻辑，此处返回 bytes 供流式播报使用。
    """
    import io
    import wave

    if not chunks:
        return b""
    if len(chunks) == 1:
        return chunks[0]
    with wave.open(io.BytesIO(chunks[0]), "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
    pcm_parts = []
    for c in chunks:
        with wave.open(io.BytesIO(c), "rb") as w:
            pcm_parts.append(w.readframes(w.getnframes()))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"".join(pcm_parts))
    return buf.getvalue()


def _wrap_pcm(pcm: bytes, rate: int = 24000) -> bytes:
    """裸 PCM(16bit mono) → wav 字节（重播文件用）。"""
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


class _AdtsStreamer:
    """单 ffmpeg 进程持续 AAC 编码 + ADTS 帧界切片（audit-TTS 音质修复）。

    逐批独立编码时每个 HLS 段都带 AAC priming（~43ms 头静音）+ 拖尾 padding，
    0.9s 段即每 0.9s 一个接口吞音（实测：段解码开头 42.7ms 才有声、接口能量塌
    至 1/3）。改为单个持续编码进程：PCM 实时写入 stdin，stdout 输出的是一条
    连续 AAC 流，按 ADTS 帧界切片出段——段只是同一流的切片，编码状态跨段连续，
    接口无缝（实测接口 RMS 与整体相当）。爬坡阈值：首播段小（0.4s）快开播，
    0.6/0.8s 爬坡，之后 0.9s 标准段（≤1s 保 TD=1，见 TD patch）。
    """

    _FRAME_SAMPLES = 1024  # AAC-LC 帧长（采样）

    def __init__(self, rate: int = 24000, seg_seconds: float = 0.9,
                 first_seg_seconds: float = 0.4, ramp_seconds=(0.6, 0.8),
                 bitrate: str = "96k"):
        import shutil
        import subprocess

        self.rate = rate
        # 各段的帧数阈值（爬坡）
        self._thresholds = [max(1, round(s * rate / self._FRAME_SAMPLES))
                            for s in (first_seg_seconds, *ramp_seconds)]
        self._standard = max(1, round(seg_seconds * rate / self._FRAME_SAMPLES))
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg 不可用（_init_tts 已把关，此处防御）")
        self._proc = subprocess.Popen(
            [ffmpeg, "-hide_banner", "-loglevel", "error",
             # 原始 PCM 无头可探，默认 probesize 会让 demuxer 攒满探针缓冲才开工
             # （实测首字节延迟 >700ms）；32B 探针 + 零分析时长 → ~210ms（实测）
             "-probesize", "32", "-analyzeduration", "0",
             "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
             "-f", "adts", "-b:a", bitrate, "-flush_packets", "1", "pipe:1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._q = queue.Queue()       # 切好的 ADTS 段
        self._buf = bytearray()       # 未切片字节
        self._seg_count = 0
        self.done = threading.Event()  # stdout EOF（编码器退出）
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # ---- 写入（respond 线程） ----
    def feed(self, pcm: bytes) -> None:
        if self.done.is_set():
            return
        try:
            self._proc.stdin.write(pcm)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            self.done.set()  # 编码器死亡：播报段停更（重播文件不受影响，走原始 PCM）

    # ---- 读取/切片（reader 线程） ----
    def _read_loop(self):
        try:
            while True:
                chunk = self._proc.stdout.read1(4096)  # read1：有数据即返回（read 会等满缓冲）
                if not chunk:
                    break
                self._buf.extend(chunk)
                self._emit(False)
        finally:
            self._emit(True)
            self.done.set()

    @staticmethod
    def _scan_upto_end(buf) -> int:
        """返回覆盖所有完整 ADTS 帧的字节数（尾部残帧/非帧数据不计）。"""
        i, n = 0, len(buf)
        while i + 7 <= n:
            if buf[i] != 0xFF or (buf[i + 1] & 0xF0) != 0xF0:
                break
            frame_len = ((buf[i + 3] & 0x3) << 11) | (buf[i + 4] << 3) | ((buf[i + 5] & 0xE0) >> 5)
            if frame_len < 7 or i + frame_len > n:
                break
            i += frame_len
        return i

    @staticmethod
    def _scan_frames(buf, n_frames: int):
        """返回覆盖前 n_frames 个完整 ADTS 帧的字节数；不足返回 None。"""
        i, n = 0, len(buf)
        for _ in range(n_frames):
            if i + 7 > n:
                return None
            if buf[i] != 0xFF or (buf[i + 1] & 0xF0) != 0xF0:
                return None
            frame_len = ((buf[i + 3] & 0x3) << 11) | (buf[i + 4] << 3) | ((buf[i + 5] & 0xE0) >> 5)
            if frame_len < 7 or i + frame_len > n:
                return None
            i += frame_len
        return i

    def _emit(self, final: bool):
        while True:
            target = (self._thresholds[self._seg_count]
                      if self._seg_count < len(self._thresholds) else self._standard)
            pos = self._scan_frames(self._buf, target)
            if pos is None:
                if final and self._buf:
                    # 收尾：残余完整帧出一段（不满一帧的尾巴丢弃）
                    end = self._scan_upto_end(self._buf)
                    if end:
                        self._q.put(bytes(self._buf[:end]))
                    self._buf.clear()
                return
            self._q.put(bytes(self._buf[:pos]))
            self._buf = self._buf[pos:]
            self._seg_count += 1

    # ---- 消费（respond 线程） ----
    def collect(self, timeout: float = 0.0) -> list:
        """取当前可用的 ADTS 段；timeout>0 时阻塞等首段（上限 timeout 秒）。"""
        out = []
        deadline = time.time() + timeout
        while True:
            try:
                if not out and timeout > 0:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        break
                    item = self._q.get(timeout=remaining)
                else:
                    item = self._q.get_nowait()
            except queue.Empty:
                break
            out.append(item)
        return out

    def collect_all(self, timeout: float = 10.0) -> list:
        """编码器收尾后排空全部残余段。"""
        out = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                out.append(self._q.get(timeout=0.2))
            except queue.Empty:
                if self.done.is_set() and self._q.empty():
                    break
        return out

    def finish(self) -> None:
        """关闭编码器 stdin（EOF → ffmpeg 冲尾 → stdout EOF → done）。"""
        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.close()
        except OSError:
            pass

    def close(self) -> None:
        self.finish()
        try:
            self._proc.terminate()
        except Exception:
            pass


_TTS_STALL_PROBE_HEAD = """
<script>
(function(){
  // 客户端停顿遥测（audit-TTS）：自动播报的 <video> 的卡顿时长/位置/前向缓冲
  // 上报服务端日志「客户端停顿上报」。waiting≥0.4s；stalled（缓冲≈0 时）；
  // seek 跳变≥0.5s（hls.js 跳缝 = 内容被跳过，听感是跳词而非停顿）。
  // ahead≈0 = 缓冲耗空（发布/网络追不平）；ahead>3 = 播放器侧问题。
  function ahead(v){try{var b=v.buffered;return b.length?+(b.end(b.length-1)-v.currentTime).toFixed(2):-1}catch(e){return -2}}
  function report(d){try{if(navigator.sendBeacon)navigator.sendBeacon('/__tts_stall',JSON.stringify(d));}catch(e){}}
  function instr(v){
    if(v.__ttsStallInstr)return; v.__ttsStallInstr=1;
    var w0=0, seekFrom=null, lastStalled=0;
    v.addEventListener('waiting',function(){if(!w0)w0=performance.now();});
    v.addEventListener('playing',function(){
      if(!w0)return;
      var d=(performance.now()-w0)/1000; w0=0;
      if(d>=0.4)report({type:'waiting',stall_s:+d.toFixed(2),pos:+v.currentTime.toFixed(2),ahead:ahead(v),rs:v.readyState,ns:v.networkState});
    });
    v.addEventListener('seeking',function(){seekFrom=v.currentTime;});
    v.addEventListener('seeked',function(){
      if(seekFrom===null)return;
      var j=v.currentTime-seekFrom; seekFrom=null;
      if(j>=0.5)report({type:'seek',jump_s:+j.toFixed(2),pos:+v.currentTime.toFixed(2),ahead:ahead(v)});
    });
    v.addEventListener('stalled',function(){
      var now=performance.now(); if(now-lastStalled<5000)return;
      var a=ahead(v); if(a>=0.5)return; lastStalled=now;
      report({type:'stalled',pos:+v.currentTime.toFixed(2),ahead:a,rs:v.readyState,ns:v.networkState});
    });
  }
  function scan(){var vs=document.querySelectorAll('video');for(var i=0;i<vs.length;i++)instr(vs[i]);}
  new MutationObserver(scan).observe(document.documentElement,{childList:true,subtree:true});
  scan();
})();
</script>
"""


def _voice_assist_head() -> str:
    """语音助手前端辅助 JS（audit-ASR，仅 VOICE_ASSIST_ENABLED 时注入 launch(head=)）。

    ① 自动点录音：页面加载后轮询点击 #voice_audio 的录音按钮（免提常驻收音；
       getUserMedia 授权弹窗仅首次，一体机 Chrome 惯例 --use-fake-ui-for-media-stream）。
    ② 打断强停：MutationObserver 观察 #voice_status 出现 ⚡ 标记 → 暂停 #tts_audio
       的 <video>（客户端 HLS 缓冲最深 60s，服务端停发不足以停声，必须前端强停）。
    __voiceAssistAutoRecord / __voiceAssistBargeIn 为控制台/测试标记。
    """
    return """
<script>
(function(){
  function findRecordBtn(){
    // gradio 6 按浏览器语言本地化按钮（zh-CN=「录制/停止录制」，en=Record/Stop）
    // → aria-label 与文本双语匹配，先排 stop 再匹 record（「停止录制」也含「录制」）
    var root=document.getElementById('voice_audio'); if(!root)return null;
    var btns=root.querySelectorAll('button');
    for(var i=0;i<btns.length;i++){
      var s=((btns[i].getAttribute('aria-label')||'')+' '+(btns[i].textContent||'')).toLowerCase();
      if(/stop|停止/.test(s))continue;
      if(/record|录制|录音/.test(s))return btns[i];
    }
    return null;
  }
  function findStopBtn(){
    var root=document.getElementById('voice_audio'); if(!root)return null;
    var btns=root.querySelectorAll('button');
    for(var i=0;i<btns.length;i++){
      var s=((btns[i].getAttribute('aria-label')||'')+' '+(btns[i].textContent||''));
      if(/停止|stop/i.test(s))return btns[i];
    }
    return null;
  }
  function streamAlive(){
    // 可靠判据：voice_status 有服务端写入的状态文本（首个流块必写：待机行/错误提示）。
    // 排除“录音已停止”文本（重试复位时写入的假阳性）。WS/fetch 挂钩均不可行——
    // gradio 6.22 的流块走长连接复用通道（实证：无 WebSocket、无逐块 POST）。
    var vs=document.getElementById('voice_status');
    var t=vs?(vs.textContent||'').trim():'';
    return t.length>0 && t.indexOf('录音已停止')<0;
  }
  var phase='wait', phaseAt=0, tries=0;
  var timer=setInterval(function(){
    tries++;
    if(tries>75){clearInterval(timer);console.warn('__voiceAssistAutoRecord give up');return;}
    if(phase==='wait'){
      // 起始延迟 ~5s：过早点击会落在 gradio hydrate 前的按钮上——UI 进录音态
      // 但录音/上行管线未启动（实证：UI 录音中但零流事件）
      if(tries<5)return;
      var b=findRecordBtn();
      if(b){try{b.click();}catch(e){}
        phase='clicked'; phaseAt=tries;
        console.log('__voiceAssistAutoRecord clicked(t'+tries+')');}
    }else{
      if(streamAlive()){clearInterval(timer);
        console.log('__voiceAssistAutoRecord stream-ok');return;}
      if(tries-phaseAt>6){
        var sb=findStopBtn(); if(sb){try{sb.click();}catch(e){}}
        phase='wait'; phaseAt=tries;  // 复位后下轮重试（自愈）
        console.warn('__voiceAssistAutoRecord retry: no stream within 6s');
      }
    }
  },1000);
  // 唤醒应答预置音频（优化轮3）：页面加载即预加载，命中「已唤醒」立即直播，
  // ~0.1s 起播（原 HLS 链路 ~1s+）；打断时与播报 video 一并暂停
  var greetAudio=null;
  try{greetAudio=new Audio('/__voice_greeting');greetAudio.preload='auto';greetAudio.load();}catch(e){}
  var lastGreet=0, lastBarge=0;
  function check(){
    var vs=document.getElementById('voice_status'); if(!vs)return;
    var t=vs.textContent||'';
    var now=Date.now();
    if(t.indexOf('已唤醒')>=0 && greetAudio && now-lastGreet>2000){
      lastGreet=now;
      try{greetAudio.currentTime=0;
        var p=greetAudio.play(); if(p&&p.catch)p.catch(function(e){console.warn('greet play blocked',e);});
      }catch(e){}
      return;
    }
    if(t.indexOf('⚡')<0)return;
    if(now-lastBarge<1000)return; lastBarge=now;
    var root=document.getElementById('tts_audio');
    var v=root?root.querySelector('video'):null;
    if(v){try{v.pause();}catch(e){}}
    if(greetAudio){try{greetAudio.pause();}catch(e){}}
    console.log('__voiceAssistBargeIn playback paused');
  }
  new MutationObserver(check).observe(document.documentElement,
    {childList:true,subtree:true,characterData:true});
})();
</script>
"""


class _TtsStallBeaconMiddleware:
    """接收客户端停顿遥测（audit-TTS）：/ __tts_stall 的 sendBeacon POST 直接应答 204
    并落日志；其余请求原样透传。ASGI 中间件实现，免注册路由。"""

    PATH = "/__tts_stall"

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != self.PATH:
            await self.app(scope, receive, send)
            return
        body = b""
        while True:
            msg = await receive()
            if msg["type"] != "http.request":
                break
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        try:
            d = json.loads(body.decode("utf-8", "replace") or "{}")
            logger.warning(
                f"客户端停顿上报: {d}"
                f"（ahead≈0→发布/网络追不平；ahead>3→播放器侧；type=seek→hls.js 跳缝）")
        except Exception:
            logger.warning(f"客户端停顿上报(解析失败): {body[:200]!r}")
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})


class _NoCacheAssetsMiddleware:
    """/assets/*.js 强制 no-cache（audit-TTS 第 2 轮无声修复的配套）。

    patch_gradio_hls_reuse 原地修改 gradio 前端 JS，文件名（内容哈希）不变；
    Starlette FileResponse 不带 Cache-Control → 浏览器启发式缓存（≈10%×文件
    年龄，可达数周）会让客户端长期运行 patch 前的旧 JS（第 2 轮无声修复因此
    不生效）。no-cache 强制每次 revalidate，ETag/mtime 变化后即拿到新文件。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")

        async def send_with_header(message):
            if (message["type"] == "http.response.start"
                    and "/assets/" in path and path.endswith(".js")):
                headers = message.setdefault("headers", [])
                headers.append([b"cache-control", b"no-cache"])
            await send(message)

        await self.app(scope, receive, send_with_header)


def _init_tts():
    """创建 TTS 实例前的守卫检查；不可用时返回 None（respond 静默跳过播报）。"""
    if not ensure_ffmpeg():
        logger.warning("ffmpeg 不可用，语音播报不可用（请安装 static-ffmpeg）")
        return None
    if not settings.dashscope_api_key:
        logger.warning("未配置 DASHSCOPE_API_KEY，语音播报不可用")
        return None
    if not settings.tts_voice:
        logger.warning("未配置 TTS 音色（TTS_VOICE 为空），语音播报不可用")
        return None
    return CosyVoiceTTS(model=settings.tts_model, voice=settings.tts_voice,
                        chunk_chars=settings.tts_chunk_chars,
                        speech_rate=settings.tts_speech_rate)


def tts_after_answer(chatbot_history, enabled):
    """respond 完成后触发：句子级流式播报 + 完整重播副本（生成器）。

    注意（audit-F18）：当前 UI 未将此函数绑定到任何事件（播报已内联进 respond），
    保留是因为 tests/test_voice_ui.py 与外部脚本仍在使用；如需启用“回答后播报”
    交互，可将其绑定到 chatbot.change 事件。
    """
    logger.info(f"TTS 播报触发: enabled={enabled}, key={'OK' if settings.dashscope_api_key else '缺失'}, voice={settings.tts_voice!r}")
    if not enabled:
        yield gr.update(), gr.update(), gr.update(value="")
        return
    if not ensure_ffmpeg():
        # gradio 6 流式播报依赖 pydub+ffmpeg 转 ADTS；服务器缺 ffmpeg 时明确提示（bug-121 实测）
        logger.warning("ffmpeg 不可用，语音播报不可用（请安装 static-ffmpeg）")
        yield gr.update(), gr.update(), gr.update(
            value="服务器缺少 ffmpeg（请执行 pip install static-ffmpeg 后重启），语音播报不可用"
        )
        return
    if not settings.dashscope_api_key:
        yield gr.update(), gr.update(), gr.update(value="未配置百炼 Key（DASHSCOPE_API_KEY），语音播报不可用")
        return
    if not settings.tts_voice:
        # Task 6 将音色守卫从 synthesize_sentence 移至调用层（计划内矛盾修正）
        yield gr.update(), gr.update(), gr.update(value="未配置 TTS 音色（TTS_VOICE 为空），请在 .env 设置")
        return
    text = _extract_last_answer_text(chatbot_history)
    if not text:
        logger.info("TTS 播报跳过: 未提取到回答文本")
        yield gr.update(), gr.update(), gr.update(value="")
        return
    logger.info(f"TTS 播报文本: {text[:60]}")
    text = clean_text_for_tts(text)
    tts = CosyVoiceTTS(model=settings.tts_model, voice=settings.tts_voice,
                       chunk_chars=settings.tts_chunk_chars,
                       speech_rate=settings.tts_speech_rate)
    chunks = []
    try:
        for sentence in tts.split_sentences(text, settings.tts_chunk_chars):
            logger.info(f"TTS 合成句子: {sentence[:40]}")
            wav = tts.synthesize_sentence(sentence)
            chunks.append(wav)
            # 句子级流式：每句合成完立即 yield。注意 streaming 输出必须 yield 直接值
            # （bytes），不能包 gr.update()——gradio 对 prop update 跳过 streaming 处理
            # （实测 KeyError/无 /stream/ 请求）
            yield wav, gr.update(), gr.update(value="播报中…")
        replay_path = _write_replay_wav(chunks)
        yield gr.update(), gr.update(value=str(replay_path)), gr.update(value="已播报（可点击重播）")
    except Exception as e:
        logger.warning(f"TTS 播报失败: {e}")
        yield gr.update(), gr.update(), gr.update(value=f"语音播报失败: {e}")


def _convert_history(history: list) -> list:
    """将 Gradio 对话历史转换为 LLM 消息格式（兼容 Gradio 4/5 tuple 与 6.x dict 格式）"""
    messages = []
    for user_msg, assistant_msg in _iter_history_pairs(history):
        if user_msg:
            # 如果前一条消息也是 user（中间 assistant 回复为空），
            # bug-034 修复：不能 continue（会跳过本轮的 assistant 消息，导致整轮对话丢失），
            # 上一条 user 是没有对应回复的孤儿消息，用当前 user 消息替换它，
            # 既避免连续 user，又保留最新问题及本轮回答
            if messages and messages[-1]["role"] == "user":
                messages[-1]["content"] = user_msg
            else:
                messages.append({"role": "user", "content": user_msg})
        if assistant_msg:
            # 只保留纯文本内容（去掉检索来源标注）
            # P1-1 修复：改为按检索来源标记定位并截断，
            # 避免旧分隔符 "\n---\n" 误伤回答正文中的 Markdown 水平线，
            # 导致多轮对话中该回答在分隔线之后的内容丢失
            marker = "**[检索来源]**"
            marker_idx = assistant_msg.find(marker)
            if marker_idx >= 0:
                clean = assistant_msg[:marker_idx].rstrip()
                # 去掉正文末尾残留的分隔符（\n---\n 或 \n\n---\n\n）
                if clean.endswith("---"):
                    clean = clean[:-3].rstrip()
            else:
                clean = assistant_msg.strip()
            if clean:
                messages.append({"role": "assistant", "content": clean})
            elif messages and messages[-1]["role"] == "user":
                # bug-028 修复：assistant 回复为空（只有检索来源无正文），
                # 删除对应的 user 消息，避免 LLM 收到不完整的上下文
                messages.pop()
    # 修复 bug-030：如果最后一条消息是 user 角色（对应的 assistant 回复为空），
    # 删除该消息，避免后续拼接时出现连续两个 user 角色消息违反 LLM API 格式要求
    if messages and messages[-1]["role"] == "user":
        messages.pop()
    return messages


def answer_question(question: str, history: list, use_stream: bool, project_id: str = ""):
    """
    回答用户问题（支持多项目、多轮对话、闲聊路由、检索可视化）
    """
    if not question or not question.strip():
        _append_conversation(history, "", "请输入问题")
        yield history, gr.update(), ""  # bug-123：空串会让 gr.JSON postprocess 抛 Error（事件静默失败）
        return

    try:
        pipe = init_pipeline(project_id)
    except Exception as e:
        _append_conversation(history, question, f"初始化失败: {e}")
        yield history, gr.update(), ""  # bug-123：空串会让 gr.JSON postprocess 抛 Error（事件静默失败）
        return

    if not pipe._is_built:
        _append_conversation(history, question,
            "知识库尚未构建！\n\n请先在终端运行:\n```\npython scripts/generate_mock_data.py -n 50\npython scripts/build_knowledge_base.py --source mixed\n```"
        )
        yield history, gr.update(), ""  # bug-123：空串会让 gr.JSON postprocess 抛 Error（事件静默失败）
        return

    conversation_history = _convert_history(history)
    _append_conversation(history, question, "")
    chunks_info = []

    if use_stream:
        full_answer = ""
        first_token_received = False
        try:
            for item in pipe.query_stream(
                question=question, top_k=settings.retriever_top_k, rerank=settings.reranker_enabled,
                conversation_history=conversation_history,
            ):
                if isinstance(item, dict) and item.get("type") == "meta":
                    # 检索结果元数据
                    chunks_info = item.get("chunks", [])
                    from_kb = item.get("from_kb", True)
                    qtype = item.get("query_type", "unknown")
                    continue
                else:
                    if not first_token_received:
                        first_token_received = True
                        _last_update = time.time()
                    full_answer += item
                    # bug-021 修复：改为基于时间间隔更新（每 100ms），避免 token 长度不均匀导致的更新频率不稳定
                    now = time.time()
                    if now - _last_update > 0.1 or len(full_answer) < 5:
                        # bug-115：展示前清洗答案正文（TTS + 字幕纯文本），
                        # 检索来源的 **名称** 加粗结构由 format_answer 保留
                        display = format_answer(clean_text_for_tts(full_answer), chunks_info)
                        _update_last_assistant(history, question, display)
                        yield history, json.dumps(chunks_info, ensure_ascii=False), full_answer
                        _last_update = now
            # 最后一次更新确保完整显示
            display = format_answer(clean_text_for_tts(full_answer), chunks_info)
            _update_last_assistant(history, question, display)
            yield history, json.dumps(chunks_info, ensure_ascii=False), full_answer
        except Exception as e:
            error_msg = f"查询出错: {e}"
            # 如果已经有部分回答，保留它而不是覆盖
            if full_answer:
                _update_last_assistant(history, question, full_answer + f"\n{HISTORY_SEPARATOR}> 剩余内容生成失败")
            else:
                _update_last_assistant(history, question, error_msg)
            yield history, json.dumps(chunks_info, ensure_ascii=False), full_answer  # bug-123：chunks_info 为空也是合法 "[]"
    else:
        try:
            # 非流式模式：先显示"正在查询..."提示
            _update_last_assistant(history, question, "正在查询知识库...")
            yield history, gr.update(), ""  # bug-123：空串会让 gr.JSON postprocess 抛 Error（事件静默失败）

            result = pipe.query(
                question=question, top_k=settings.retriever_top_k, rerank=settings.reranker_enabled,
                conversation_history=conversation_history,
            )
            answer = result["answer"]
            chunks_info = result.get("retrieved_chunks", [])
            timing = result.get("timing", {})
            # bug-115：展示前清洗答案正文（TTS + 字幕纯文本）
            display = format_answer(clean_text_for_tts(answer), chunks_info, timing)
            _update_last_assistant(history, question, display)
            yield history, json.dumps(chunks_info, ensure_ascii=False), answer
        except Exception as e:
            error_msg = f"查询出错: {e}"
            _update_last_assistant(history, question, error_msg)
            yield history, gr.update(), ""  # bug-123：空串会让 gr.JSON postprocess 抛 Error（事件静默失败）


def respond(message, chat_history, stream, project, tts_enabled, request: gr.Request = None):
    """回答 + 语音播报（audit-TTS：单会话流式合成 —— 首句 ≤1s、全程无停顿）。

    audit-ASR 需求4（打断）：入口注册播报 token（手动发送同样注册，任何播报都可被打断）；
    主循环/收尾循环每拍检查 cancel —— 语音助手在播报中检出持续语音（VAD ≥400ms）即置位，
    本生成器停喂停发、取消 TTS 会话、跳过收尾冲刷与重播写入，yield "已打断"状态。
    客户端 HLS 缓冲（≤60s）由 head JS 观察 voice_status 的 ⚡ 标记强停 <video> 清除。

    旧链路（bug-121 分段独立合成）：等整段合成完成（~2s/段）才有音频，且首播
    攒批门（5 chunk + 2s 等待）→ 首播 ~3s；段间会话边界（WS 连接+首块 0.6s）
    → 中途接缝。新链路：每个回答一个 CosyVoice 流式会话，LLM 文本增量喂入
    （首段 tts_first_fragment_chars(8) 字即喂，实测首音频块 ~0.6s 与文本长度
    无关），PCM 音频块边产边播（首播批 0.2s，后续 2s/批）→ 首播 ≈1s，且全程
    一条连续音频流（无段间边界；客户端 60s 缓冲 + TD 修正吸收残余抖动）。
    关键：answer_question 在后台泵线程迭代，音频按 0.1s 节拍独立发布——LLM 流
    中途停顿（实测 qwen-plus 高峰可停 40s+）不再阻塞音频发布（旧结构音频收集
    耦联在 LLM yield 上，是“有时长停顿”根因之一，E2E 实证 47s 断流）。
    会话异常（报错/看门狗无音频超时）自动重建（≤2 次，重喂最后一个片段，有界
    重复）；合成/播报任何异常都不影响回答输出。
    """
    if not message or not message.strip():
        yield "", chat_history, "[]", gr.update(), gr.update(), gr.update()
        return
    token = _register_broadcast(request, "answer")  # audit-ASR：打断靶向（request=None→"anon"）
    cancelled = False
    tts = _init_tts() if tts_enabled else None
    # 单编码器持续 AAC 流（音质修复：逐批独立编码每段带 priming 静音坑）；
    # 无 ffmpeg 时 _init_tts 已返回 None，此处不会触发 RuntimeError
    streamer = _AdtsStreamer(rate=24000,
                             seg_seconds=settings.tts_batch_seconds,
                             first_seg_seconds=settings.tts_first_batch_seconds,
                             ramp_seconds=(0.6, 0.8)) if tts is not None else None
    replay_pcm = bytearray()  # 原始 PCM 累积（重播文件用，与编码器健康解耦）
    compressor = _PauseCompressor(rate=24000) if tts is not None else None
    handle = None          # 流式会话句柄（懒启动：首个非空片段才建会话）
    fed_texts = []         # 已喂入片段（重启用）
    last_feed_at = time.time()  # 上次喂入时刻（断粮守卫：>2.5s 有完整句即喂）
    restarts = 0
    tts_dead = False       # 重建次数用尽 → 放弃本轮播报（回答不受影响）
    tts_text_buf = ""      # 攒字缓冲区（原始文本，待切分）
    prev_raw = ""
    replay_blocks = []     # 已发布音频批（结尾合并写重播文件）
    played_any = False
    respond_t0 = time.time()  # audit-TTS 首播延迟度量基准
    last_audio_at = time.time()
    first_feed_at = None
    # 缓冲告急诊断（audit-TTS）：播放器消耗≈开播至今墙钟，估算剩余缓冲
    published_audio_s = 0.0
    first_publish_at = None
    last_dearth_log = 0.0
    pcm_stats = {"chunks": 0, "bytes": 0}  # audit-TTS：PCM 到达计量（诊断 API 断流）

    def _on_audio(pcm: bytes) -> None:
        """PCM 到达回调（SDK 接收线程）：静默压缩 → 喂编码器 + 重播累积 + 计量。"""
        nonlocal last_audio_at
        now = time.time()
        gap = now - last_audio_at
        last_audio_at = now  # 先计量：API 已交付（即使全是待丢的静默），看门狗不误判
        pcm_stats["chunks"] += 1
        pcm_stats["bytes"] += len(pcm)
        pcm = compressor.feed(pcm)  # 丢弃 >0.35s 的静默窗（喂入边界烙入的停顿）
        if not pcm:
            return
        streamer.feed(pcm)
        replay_pcm.extend(pcm)
        # 首轮音频到达后，超过 3s 无块视为 API 侧断流（生产可观测性）
        if pcm_stats["chunks"] > 1 and gap > 3.0:
            logger.warning(f"TTS 音频块间隔异常: {gap:.1f}s（API 侧断流/拥塞；"
                           f"累计 {pcm_stats['bytes'] / 48000:.1f}s 音频）")

    def _feed(text) -> bool:
        """喂一个片段（会话懒启动）；返回 False 表示会话异常需重建。"""
        nonlocal handle, first_feed_at
        try:
            if handle is None:
                handle = tts.start_stream(_on_audio)
            handle.feed(text)
        except Exception as e:
            logger.warning(f"TTS 喂文本失败（会话异常）: {e}")
            return False
        fed_texts.append(text)
        if first_feed_at is None:
            first_feed_at = time.time()
        return True

    def _session_broken() -> bool:
        if handle is None:
            return False
        if handle.error:
            return True
        # 看门狗：有待播文本、会话未完成、超过阈值无音频 → 判定挂起
        return (fed_texts and not handle.done.is_set()
                and time.time() - last_audio_at > settings.tts_stream_watchdog_seconds)

    def _restart() -> None:
        """重建会话并重喂最后一个片段（有界重复 ≤1 段；更早缺失接受并记录）。"""
        nonlocal handle, restarts, tts_dead
        if restarts >= 2:
            tts_dead = True
            logger.warning("TTS 会话多次异常，放弃本轮播报（回答不受影响）")
            return
        restarts += 1
        logger.warning(f"TTS 会话异常（{(handle.error if handle else None) or '无音频超时'}），"
                       f"重建会话({restarts}/2)")
        try:
            if handle is not None:
                handle.cancel()
        except Exception:
            pass
        handle = None
        if fed_texts:
            _feed(fed_texts[-1])

    def _collect(wait: float = 0.0) -> list:
        """收集已切好的 ADTS 段；wait>0 时阻塞等首段（上限 wait 秒）。"""
        return streamer.collect(timeout=wait)

    # audit-TTS：后台泵线程迭代 answer_question，音频发布按 0.1s 节拍独立驱动，
    # 与 LLM yield 节奏解耦（LLM 流停顿不再断音频流）
    text_q = queue.Queue()

    def _answer_pump():
        try:
            for r in answer_question(message, chat_history, stream, project):
                text_q.put(("item", r))
        except Exception as e:
            text_q.put(("error", e))
        finally:
            text_q.put(("end", None))

    try:
        threading.Thread(target=_answer_pump, daemon=True).start()
        ended = False
        while not ended:
            if token.cancel.is_set():
                cancelled = True
                break
            try:
                kind, payload = text_q.get(timeout=0.1)
            except queue.Empty:
                kind, payload = "tick", None
            if kind == "item":
                yield "", payload[0], payload[1], gr.update(), gr.update(), gr.update()
                if tts is not None and not tts_dead:
                    # 用原始累积文本做 diff（answer_question 第三值；display 经 clean
                    # 补标点后前缀漂移，按长度 diff 会错位漏字——bug-121 实测）
                    raw = payload[2] if len(payload) > 2 else ""
                    if len(raw) > len(prev_raw):
                        tts_text_buf += raw[len(prev_raw):]
                        prev_raw = raw
                    # 增量喂文本（第五轮断句修复）：streaming_call 边界会烙静默/
                    # 韵律断层 → 边界必须落在自然停顿处。首播单元切句末（逗号兜底）；
                    # 后续批量完整句（≥tts_accum_chars 字起喂），只在句末标点切
                    while not tts_dead:
                        if not fed_texts:
                            seg, tts_text_buf = _take_first_unit(tts_text_buf)
                        else:
                            seg, tts_text_buf = _take_feed_unit(
                                tts_text_buf, min_chars=settings.tts_accum_chars,
                                starve=time.time() - last_feed_at > 2.5)
                        if not seg:
                            break
                        seg_clean = clean_text_for_tts(seg)
                        if not seg_clean:
                            continue
                        if _feed(seg_clean):
                            last_feed_at = time.time()
                        else:
                            _restart()
            elif kind == "error":
                logger.warning(f"回答流异常（防御兜底，answer_question 多已内部处理）: {payload}")
            elif kind == "end":
                ended = True
            # 每拍（≤0.1s）收集音频并发布：与 LLM 节奏解耦的核心
            if tts is not None and not tts_dead:
                if _session_broken():
                    _restart()
                segs = _collect()
                for seg in segs:
                    played_any = True
                    replay_blocks.append(seg)
                    published_audio_s += _adts_duration(seg, 24000)
                    if first_publish_at is None:
                        first_publish_at = time.time()
                    if len(replay_blocks) == 1 and first_feed_at is not None:
                        logger.info(f"TTS 首播: 喂文本@{first_feed_at - respond_t0:.2f}s "
                                    f"首批@{time.time() - respond_t0:.2f}s "
                                    f"（喂文本→发布 {time.time() - first_feed_at:.2f}s，验收 ≤1s）")
                    logger.debug(f"TTS 批({len(replay_blocks)}) @{time.time() - respond_t0:.2f}s")
                    yield gr.update(), gr.update(), gr.update(), seg, gr.update(), \
                        gr.update(value="播报中…")
                # 缓冲告急：发布节拍空 + PCM 断流 >1s + 估算剩余 <1.5s → 听众即将感知停顿
                if (not segs and first_publish_at is not None
                        and time.time() - last_audio_at > 1.0
                        and time.time() - last_dearth_log > 10):
                    est = published_audio_s - (time.time() - first_publish_at)
                    if est < 1.5:
                        last_dearth_log = time.time()
                        logger.warning(
                            f"TTS 缓冲告急: 估算剩余 {max(est, 0):.1f}s"
                            f"（已发布 {published_audio_s:.1f}s / 开播 {time.time() - first_publish_at:.1f}s）；"
                            f"LLM 出文停顿正被听众感知。若频繁出现：查客户端 HLS patch "
                            f"是否生效（StaticAudio-*.js 应含 maxBufferLength:60）与网络 RTT")
        # 回答结束：喂尾文本 → finish（后台线程，阻塞型） → 排空（含看门狗重建）
        # audit-ASR：被打断则整段跳过（不 finish、不冲尾、不写重播）
        if tts is not None and not tts_dead and not cancelled:
            tail = clean_text_for_tts(tts_text_buf.strip()) if tts_text_buf.strip() else ""
            if tail and not _feed(tail):
                _restart()

            def _finish_async(h):
                try:
                    h.finish()
                except Exception as e:
                    logger.warning(f"TTS finish 异常: {e}")

            if handle is not None:
                # audit-TTS：streaming_complete 会阻塞等全部合成完成（实测 26s+！）
                # 并在完成后 close WebSocket——同步调用会冻结发布（中途停顿真凶之一，
                # E2E 实证 playlist 冻结 22s）→ 放后台线程执行
                threading.Thread(target=_finish_async, args=(handle,), daemon=True).start()
                end_at = time.time() + 120  # 总兜底，防无限循环
                while not tts_dead and time.time() < end_at:
                    if token.cancel.is_set():
                        cancelled = True
                        break
                    for seg in _collect(wait=0.5):
                        played_any = True
                        replay_blocks.append(seg)
                        yield gr.update(), gr.update(), gr.update(), seg, gr.update(), \
                            gr.update(value="播报中…")
                    if handle.done.is_set():
                        break  # 会话完成：全部 PCM 已交付（WS 消息有序，on_complete 殿后）
                    if time.time() - last_audio_at > settings.tts_stream_watchdog_seconds:
                        _restart()
                        if not tts_dead and handle is not None:
                            threading.Thread(target=_finish_async,
                                             args=(handle,), daemon=True).start()
                # 全部 PCM 已喂完 → 压缩器残尾入管 → 关编码器 stdin 冲尾，排空残余段
                # audit-ASR：排空循环内被打断 → 跳过冲尾/审计（残余段已无听众）
                if not cancelled:
                    tail_pcm = compressor.feed(b"") + compressor.flush()
                    if tail_pcm:
                        streamer.feed(tail_pcm)
                        replay_pcm.extend(tail_pcm)
                    streamer.finish()
                    for seg in streamer.collect_all(timeout=10):
                        played_any = True
                        replay_blocks.append(seg)
                        yield gr.update(), gr.update(), gr.update(), seg, gr.update(), \
                            gr.update(value="播报中…")
                    if handle is not None and handle.error:
                        logger.warning(f"TTS 会话出错（部分音频可能缺失）: {handle.error}")
                    logger.info(f"TTS 播报收尾: 批次={len(replay_blocks)} "
                                f"音频={len(replay_pcm) / 48000:.1f}s "
                                f"耗时={time.time() - respond_t0:.1f}s 重建={restarts}")
            if cancelled:
                pass  # 打断：不写重播（部分内容无意义），由下方统一 yield 打断状态
            elif replay_pcm:
                # 重播走原始 PCM（与编码器健康解耦，质量无损）
                replay_path = _write_replay_wav([_wrap_pcm(bytes(replay_pcm), 24000)])
                # 静默审计（后台线程）：客户端零上报但用户听到停顿时给内容侧定性
                threading.Thread(target=_audit_silence_log,
                                 args=(bytes(replay_pcm),), daemon=True).start()
                yield gr.update(), gr.update(), gr.update(), gr.update(), \
                    gr.update(value=str(replay_path)), gr.update(value="已播报（可点击重播）")
            else:
                yield gr.update(), gr.update(), gr.update(), gr.update(), \
                    gr.update(), gr.update(value="")
        if cancelled:
            # audit-ASR 需求4：打断收尾（客户端缓冲由 head JS 观察 ⚡ 标记强停 <video>）
            logger.info("播报被打断（barge-in）：停止发布并取消合成")
            yield gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), \
                gr.update(value="⚡ 已打断，请继续提问")
    except Exception as e:
        logger.warning(f"语音播报异常（不影响回答）: {e}")
        yield gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), \
            gr.update(value=f"语音播报异常: {e}")
    finally:
        token.done.set()  # audit-ASR：状态机经 notify_broadcast(False) 进 LISTEN
        if handle is not None:
            try:
                handle.cancel()
            except Exception:
                pass
        if streamer is not None:
            streamer.close()


def format_answer(answer: str, chunks: list, timing: dict = None) -> str:
    """格式化答案，追加检索来源和时间信息"""
    parts = [answer]

    # 追加检索来源（仅当有检索结果时）
    if chunks:
        parts.append(f"\n{HISTORY_SEPARATOR}**[检索来源]**\n")
        # P1-4 修复：分数阈值自适应——混合检索的 RRF 融合分（约 0.01 量级）
        # 与重排后的相关性分数（0~1）量级差异大，固定阈值 0.7/0.4 会导致
        # RRF 场景下所有结果恒为灰色，无法区分相关度
        scores = [(c.get("score") or 0) for c in chunks[:5]]
        max_score = max(scores) if scores else 0
        # RRF 融合分无绝对意义，按显示排名标记：第1名 [高]，第2-3名 [中]，其余 [低]
        # audit-F10 修复：RRF 理论上限 = (w_sem + w_bm25)/(rrf_k+1) = 1/61 ≈ 0.0164
        # （rrf_k=60）；旧阈值 0.1 会把重排量纲的合法低分（如 0.05）误判为 RRF，
        # 导致低相关结果被按排名误标 [高]。阈值收紧到 0.02（留有余量）。
        rrf_scale = 0 < max_score < 0.02
        for i, c in enumerate(chunks[:5], 1):
            # bug-041 修复：字段缺失/为 None 时使用默认值，避免 score 比较崩溃
            name = c.get("artifact_name") or "未知"
            score = c.get("score") or 0
            ctype = c.get("chunk_type") or "full"
            if rrf_scale:
                score_label = "[高]" if i <= 1 else "[中]" if i <= 3 else "[低]"
            else:
                score_label = "[高]" if score > 0.7 else "[中]" if score > 0.4 else "[低]"
            parts.append(f"{i}. **{name}**  {score_label} 相关度: {score:.3f}  [{ctype}]")

    # 追加响应时间（仅非流式有）
    if timing:
        total = timing.get("total", 0)
        parts.append(f"\n\n> 响应时间: {total}ms")

    return "\n".join(parts)


def clear_history():
    """清空对话历史"""
    return [], ""


def get_system_status(project_id: str = ""):
    """获取系统状态（指定项目）"""
    try:
        pipe = init_pipeline(project_id)
        if pipe._is_built:
            stats = pipe.get_stats()
            if "error" in stats:
                return f"知识库状态异常: {stats['error']}"
            project_name = pipe.project_cfg.name if pipe.project_cfg else "默认"
            return (
                f"系统就绪\n\n"
                f"**当前项目**: {project_name}\n\n"
                f"**知识库统计**\n"
                f"- 向量数量: {stats.get('vector_count', 'N/A')}\n"
                f"- 向量维度: {stats.get('vector_size', 'N/A')}\n"
                f"- 距离算法: {stats.get('distance', 'N/A')}\n\n"
                f"**模型配置**\n"
                f"- LLM: {settings.llm_model_name}\n"
                f"- Embedding: {settings.embedding_model_name}\n"
                f"- 缓存: 已启用\n\n"
                f"**提示**\n"
                f"- 知识库问题 → 自动检索知识库\n"
                f"- 闲聊问候 → 直接 LLM 回答（更快）\n"
                f"- 支持追问: \"它是什么？\""
            )
        else:
            return "知识库未构建\n\n请先在终端运行:\n```\npython scripts/generate_mock_data.py -n 50\npython scripts/build_knowledge_base.py --source mixed\n```"
    except Exception as e:
        return f"状态检查失败: {e}"


def create_ui(default_stream: bool = True, default_project: str = ""):
    """创建 Gradio 界面 v2（含检索结果可视化）

    Args:
        default_stream: 流式输出复选框的默认值（--no-stream 时置 False）
        default_project: 启动时指定的项目 ID（--project 参数），
            作为下拉框默认选中值，避免页面加载时误切换全局 pipeline（bug-111）
    """
    # bug-111 修复：下拉框 choices 动态来自 ProjectManager（含自定义/外部项目），
    # 默认值跟随启动参数 --project。此前 choices/value 硬编码 museum/enterprise，
    # 导致 --project jiabohui 启动后页面加载（demo.load → get_system_status）
    # 把全局 pipeline 切换成 museum，所有回答都变成博物馆的。
    _projects = project_manager.list_projects()
    _project_ids = [p["id"] for p in _projects]
    if not _projects:
        _project_choices = [("默认", "")]
        _default_project = ""
    else:
        _project_choices = [(p["name"], p["id"]) for p in _projects]
        _default_project = (
            default_project if default_project in _project_ids else _projects[0]["id"]
        )
    with gr.Blocks(
        title="文物知识库 RAG 问答系统",
        # bug-098：Gradio 6.0 将 theme/css 移到 launch()，构造器传参会警告并失效
        **({} if _GRADIO_MAJOR >= 6 else {"theme": _UI_THEME, "css": _UI_CSS}),
    ) as demo:
        # 项目选择器
        with gr.Row():
            project_dropdown = gr.Dropdown(
                choices=_project_choices,
                value=_default_project,
                label="选择项目",
                scale=1,
            )
            status_btn = gr.Button("刷新状态", variant="secondary", scale=1)
            status_text = gr.Markdown("正在检测系统状态...", scale=4)

        with gr.Row():
            # 左侧：对话
            with gr.Column(scale=7):
                chatbot = gr.Chatbot(
                    label="对话",
                    height=500,
                    render_markdown=True,
                    # bug-108 修复：不再传 emoji 头像。Gradio 6 将 avatar_images 的 str
                    # 当作文件路径解析为 FileData（如 "🏛️" → 路径不存在），前端渲染
                    # Chatbot 时请求无效文件导致组件区域崩溃消失（页面一闪变空白）。
                    avatar_images=None,
                    # bug-098：Gradio 6.0 移除 show_copy_button / bubble_full_width，
                    # 改用 buttons=["copy"] / layout="bubble"
                    **({"buttons": ["copy"], "layout": "bubble"} if _GRADIO_MAJOR >= 6
                       else {"show_copy_button": True, "bubble_full_width": False}),
                )

                with gr.Row():
                    msg = gr.Textbox(
                        label="输入问题",
                        placeholder="输入问题，按 Enter 发送",
                        scale=8,
                        container=False,
                    )
                    submit_btn = gr.Button("发送", variant="primary", scale=1, min_width=80)

                with gr.Row():
                    voice_audio = gr.Audio(
                        sources=["microphone"],
                        streaming=True,
                        type="filepath",
                        label="语音输入（点击开始说话，实时转写，说完点击停止）",
                        scale=6,
                        elem_id="voice_audio",  # audit-ASR：自动点录音 JS 定位
                    )
                    voice_status = gr.Markdown("", scale=4, elem_id="voice_status")  # audit-ASR：打断观察
                    asr_state = gr.State(None)
                    # audit-ASR：触发器用 gr.State（服务端值跟踪，deep_hash 变更检测）——
                    # 隐藏 Textbox 组件值会被 gradio 6.22 流式 diff 串线（实证：[['add',...]]
                    # 与 '[]' 乱码进事件输入）；State 值不经过前端，天然免疫
                    auto_q = gr.State(0)
                    greet_trig = gr.State(0)

                with gr.Row():
                    use_stream = gr.Checkbox(label="流式输出", value=default_stream)
                    clear_btn = gr.Button("清空对话", variant="secondary", size="sm", scale=1)

            # 右侧：检索结果面板
            with gr.Column(scale=3):
                gr.Markdown("### 检索结果")
                chunks_json = gr.JSON(
                    label="匹配的文物",
                    value=[],
                    visible=True,
                )
                gr.Markdown(
                    "**提示**\n"
                    "- 文物问题 → 自动检索知识库\n"
                    "- 闲聊问候 → 直接 AI 回答\n"
                    "- [高] = 高相关度\n"
                    "- [中] = 中等相关度\n"
                    "- [低] = 低相关度"
                )

                gr.Markdown("### 语音播报")
                tts_audio = gr.Audio(streaming=True, autoplay=True, label="播报（自动播放）", visible=True,
                                     elem_id="tts_audio")  # audit-ASR：打断强停 <video> 定位
                tts_replay = gr.Audio(label="重播", visible=True)
                tts_enabled = gr.Checkbox(label="语音播报", value=True)
                tts_status = gr.Markdown("")

        # 示例问题
        with gr.Row():
            gr.Markdown("**试试这些问题:**")

        examples = [
            "推荐一些代表性的文物",
            "公司的主要产品有哪些",
            "司母戊鼎有多重",
            "员工入职流程是什么",
            "你好，你是谁",
            "今天天气怎么样",
        ]
        example_btns = []
        for i in range(0, len(examples), 2):
            with gr.Row():
                for j in range(2):
                    idx = i + j
                    if idx < len(examples):
                        btn = gr.Button(examples[idx], variant="secondary", size="sm", scale=1)
                        example_btns.append(btn)

        # ========== 事件绑定 ==========

        tts_outputs = [msg, chatbot, chunks_json, tts_audio, tts_replay, tts_status]
        no_tts = gr.State(False)  # 示例按钮不触发播报（避免误播）
        msg.submit(respond, [msg, chatbot, use_stream, project_dropdown, tts_enabled], tts_outputs)
        submit_click = submit_btn.click(respond, [msg, chatbot, use_stream, project_dropdown, tts_enabled], tts_outputs)
        clear_btn.click(clear_history, None, [chatbot, chunks_json])
        status_btn.click(get_system_status, [project_dropdown], [status_text])

        # 语音功能（bug-121 手动 / audit-ASR 语音助手）：ASR 流式输入
        # 分发器按 VOICE_ASSIST_ENABLED 分流；输出恒 5 元组（手动模式后两个 no-op）
        voice_audio.stream(
            voice_stream_dispatch,
            [voice_audio, asr_state, project_dropdown],
            [asr_state, msg, voice_status, auto_q, greet_trig],
            stream_every=0.3,  # 优化轮3：0.5→0.3s 块节奏（部分结果上屏更密、端点判定更及时）
        )
        # 注意：gradio 6 的 stop 事件是"播放停止按钮"；录音停止必须用 stop_recording
        voice_audio.stop_recording(
            voice_stop_dispatch,
            [asr_state, project_dropdown],
            [asr_state, msg, voice_status, auto_q, greet_trig],
        )
        # audit-ASR：语音助手自动提交（双计时到期）→ 独立事件走问答+播报，不阻塞收音
        auto_q.change(
            auto_respond,
            [auto_q, chatbot, use_stream, project_dropdown, tts_enabled],
            tts_outputs,
        )
        # audit-ASR：唤醒应答播报（缓存音频；应答语全文经 voice_status 常驻行展示）
        greet_trig.change(
            play_greeting,
            [greet_trig, project_dropdown, tts_enabled],
            [tts_audio, tts_replay, tts_status],
        )

        for btn in example_btns:
            btn.click(respond, [btn, chatbot, use_stream, project_dropdown, no_tts], tts_outputs)

        demo.load(get_system_status, [project_dropdown], [status_text])

    return demo


def _voice_assist_startup_probe() -> bool:
    """启动自检（audit-ASR）：VOICE_ASSIST_ENABLED=true 时验证 VAD 可用。

    不要等到用户开口才暴露环境问题（onnxruntime 未装 / 模型未拷贝）——启动即
    ERROR 日志，部署验收一眼可见。assist 关闭 → 跳过（True）。
    """
    if not settings.voice_assist_enabled:
        return True
    from src.vad import create_vad

    try:
        create_vad(
            model_path=settings.silero_vad_model_path, threshold=settings.vad_threshold,
            min_speech_ms=settings.vad_min_speech_ms, min_silence_ms=settings.vad_min_silence_ms,
            pad_ms=settings.vad_speech_pad_ms, max_speech_s=settings.vad_max_speech_s,
            sample_rate=settings.asr_sample_rate)
        logger.info("语音助手启动自检通过：VAD 可用（silero onnx）")
        return True
    except Exception as e:
        logger.error(
            f"语音助手已开启但 VAD 不可用: {e} —— 修复后重启进程；"
            f"或置 VOICE_ASSIST_ENABLED=false 回手动模式")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="文物知识库 RAG Web UI v2")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=7860, help="监听端口")
    parser.add_argument("--share", action="store_true", help="创建公开链接")
    # bug-054 修复：补全 README/DEPLOY_GUIDE 中已声明但缺失的 --project / --no-stream 参数
    parser.add_argument("--project", type=str, default="", help="项目 ID（如 museum、enterprise）")
    parser.add_argument("--no-stream", action="store_true", help="禁用流式输出")
    # bug-122：语音输入（麦克风）需 HTTPS 安全上下文，原生 SSL 启动：
    # python app.py --ssl-keyfile certs/key.pem --ssl-certfile certs/cert.pem
    parser.add_argument("--ssl-keyfile", type=str, default="", help="HTTPS 证书私钥路径（语音输入需 HTTPS，两个 ssl 参数需同时提供）")
    parser.add_argument("--ssl-certfile", type=str, default="", help="HTTPS 证书路径（语音输入需 HTTPS，两个 ssl 参数需同时提供）")
    args = parser.parse_args()

    setup_logger(settings.log_level)
    logger.info("正在初始化 RAG 系统...")
    _voice_assist_startup_probe()  # audit-ASR：assist 开启时先验 VAD，失败即 ERROR 日志
    if settings.voice_assist_enabled:
        # 优化轮3：启动后台预合成唤醒应答音频（首次唤醒零合成延迟 + 前端可预加载）
        def _warm_greeting():
            try:
                pcm = _greeting_pcm("")
                logger.info(f"唤醒应答音频就绪: {len(pcm) / 48000:.1f}s" if pcm
                            else "唤醒应答音频未就绪（首次唤醒无声，状态行仍在）")
            except Exception as e:
                logger.warning(f"唤醒应答音频预热失败: {e}")
        threading.Thread(target=_warm_greeting, daemon=True).start()
    try:
        init_pipeline(args.project)
    except Exception as e:
        logger.warning(f"初始化警告: {e}")

    demo = create_ui(default_stream=not args.no_stream, default_project=args.project)
    logger.info(f"启动 Web UI: http://{args.host}:{args.port}")
    launch_kwargs = {
        "server_name": args.host,
        "server_port": args.port,
        "share": args.share,
        # audit-F22 修复：show_error=True 会向前端用户展示完整后端堆栈（信息泄漏），
        # 仅在 DEBUG 日志级别下开启（开发排障），生产环境（INFO+）关闭
        "show_error": settings.log_level.upper() == "DEBUG",
    }
    if _GRADIO_MAJOR >= 6:
        # bug-098：Gradio 6.0 将 theme/css 移到 launch()
        launch_kwargs["theme"] = _UI_THEME
        launch_kwargs["css"] = _UI_CSS
        # audit-TTS：/assets/*.js no-cache——HLS patch 原地改文件、哈希文件名不变，
        # 浏览器启发式缓存会让客户端长期跑 patch 前的旧 JS（第 2 轮无声修复不生效）
        from starlette.middleware import Middleware

        launch_kwargs["head"] = _TTS_STALL_PROBE_HEAD  # 客户端停顿遥测探针
        if settings.voice_assist_enabled:
            # audit-ASR：自动点录音 + 打断强停（仅助手模式注入）
            launch_kwargs["head"] += _voice_assist_head()
        launch_kwargs["app_kwargs"] = {
            "middleware": [Middleware(_NoCacheAssetsMiddleware),
                           Middleware(_TtsStallBeaconMiddleware)]
        }
        if settings.voice_assist_enabled:
            # 优化轮3：预置应答音频端点（前端 JS 预加载直播，唤醒 ~0.1s 起播）
            launch_kwargs["app_kwargs"]["middleware"].append(
                Middleware(_GreetingAudioMiddleware))
    if args.ssl_keyfile or args.ssl_certfile:
        # bug-122：浏览器 getUserMedia 要求安全上下文（HTTPS/localhost），
        # 自签或正式证书均可；仅提供其一则报错提示
        if args.ssl_keyfile and args.ssl_certfile:
            launch_kwargs["ssl_keyfile"] = args.ssl_keyfile
            launch_kwargs["ssl_certfile"] = args.ssl_certfile
        else:
            raise SystemExit("--ssl-keyfile 与 --ssl-certfile 必须同时提供")
    demo.launch(**launch_kwargs)


if __name__ == "__main__":
    main()