"""服务装配（web-008）：懒加载冻结内核单例，测试可注入假件。

仿 app.py init_pipeline/_init_tts，但不 import app.py（模块级 gradio patch）。
注意：Qdrant 本地嵌入模式文件锁——本进程与 Gradio app 互斥运行（部署文档约定）。
"""
from __future__ import annotations

import logging
import threading

from src.config import settings

logger = logging.getLogger(__name__)

_pipeline = None
_pipeline_project: str = ""
_pipeline_status = "not_loaded"          # not_loaded / ready / error:<msg>
_lock = threading.Lock()

# web-041：一体机语音播报场景回答限长（用户拍板 320）。进程级钳制 settings.llm_max_tokens，
# 仅影响本薄层进程（Gradio 控制台不受影响，长回答对管理/调试仍有价值）；内核 LLM 实例在
# pipeline 装配时捕获该值 → 必须在任何 pipeline 加载之前调用（__main__ 生产入口保证）。
KIOSK_ANSWER_MAX_TOKENS = 320
_caps_applied = False


def apply_kiosk_llm_caps() -> None:
    """幂等：仅当现值高于上限时钳制（低于上限的部署配置不动）。"""
    global _caps_applied
    if _caps_applied:
        return
    _caps_applied = True
    if settings.llm_max_tokens > KIOSK_ANSWER_MAX_TOKENS:
        logger.info("kiosk 回答限长: llm_max_tokens %s → %s（仅本进程，Gradio 不受影响）",
                    settings.llm_max_tokens, KIOSK_ANSWER_MAX_TOKENS)
        settings.llm_max_tokens = KIOSK_ANSWER_MAX_TOKENS


def _load_pipeline(project_id: str):
    """真实加载路径（测试 monkeypatch 此函数注入假件）。"""
    from src.rag_pipeline import RAGPipeline

    pipe = RAGPipeline(
        local_mode=True,
        enable_cache=True,
        memory_mode=settings.qdrant_memory_mode,
        project_id=project_id or None,
    )
    pipe._ensure_knowledge_base()
    pipe.warmup()
    return pipe


def get_pipeline(project_id: str = ""):
    """锁内单例（双重检查）；知识库未构建不致命（查询时才报友好错误）。"""
    global _pipeline, _pipeline_project, _pipeline_status
    project_id = project_id or ""
    if _pipeline is not None and project_id == _pipeline_project:
        return _pipeline
    with _lock:
        if _pipeline is not None and project_id == _pipeline_project:
            return _pipeline
        try:
            _pipeline = _load_pipeline(project_id)
            _pipeline_project = project_id
            _pipeline_status = "ready"
            logger.info("kiosk RAG 流水线就绪 - 项目: %s", project_id or "默认")
        except Exception as e:
            _pipeline_status = f"error:{e}"
            logger.warning("kiosk 流水线初始化失败: %s", e)
            raise
    return _pipeline


def pipeline_status() -> str:
    """健康探针用：不触发加载，只报告现状。"""
    return _pipeline_status


def make_tts():
    """仿 app.py _init_tts：配置缺失返回 None（纯文本回答，播报静默跳过）。"""
    if not settings.tts_enabled:
        return None
    if not settings.dashscope_api_key:
        logger.warning("未配置 DASHSCOPE_API_KEY，语音播报不可用")
        return None
    if not settings.tts_voice:
        logger.warning("未配置 TTS 音色（TTS_VOICE 为空），语音播报不可用")
        return None
    from src.tts import CosyVoiceTTS

    return CosyVoiceTTS(model=settings.tts_model, voice=settings.tts_voice,
                        chunk_chars=settings.tts_chunk_chars,
                        speech_rate=settings.tts_speech_rate)


def _reset_cache() -> None:
    """测试/关停用：清空单例缓存与语音探针状态。"""
    global _pipeline, _pipeline_project, _pipeline_status
    global _voice_init_error, _voice_probed, _caps_applied
    with _lock:
        _pipeline = None
        _pipeline_project = ""
        _pipeline_status = "not_loaded"
        _voice_init_error = ""
        _voice_probed = False
        _caps_applied = False
        _greeting_mem.clear()


# ============ web-009：语音助手装配 + 应答语缓存 ============

_voice_init_error = ""
_voice_probed = False


def make_voice_assistant(project_id: str = ""):
    """仿 app.py _create_voice_assistant：VAD 不可用 → None（降级不崩，原因落日志）。"""
    global _voice_init_error, _voice_probed
    try:
        assistant = _load_voice_assistant(project_id)
    except Exception as e:
        _voice_init_error = (str(e) or type(e).__name__)[:150]
        _voice_probed = True
        logger.warning("VAD/语音助手初始化失败（语音模式不可用）: %s", e)
        return None
    _voice_init_error = ""
    _voice_probed = True
    return assistant


def _load_voice_assistant(project_id: str = ""):
    """真实装配路径（测试 monkeypatch 此函数注入假 FSM）。"""
    from src.asr import IflytekASR, load_dict
    from src.vad import create_vad
    from src.voice_assistant import VoiceAssistant, make_corrector

    vad = create_vad(
        model_path=settings.silero_vad_model_path, threshold=settings.vad_threshold,
        min_speech_ms=settings.vad_min_speech_ms,
        min_silence_ms=settings.vad_min_silence_ms,
        pad_ms=settings.vad_speech_pad_ms, max_speech_s=settings.vad_max_speech_s,
        sample_rate=settings.asr_sample_rate)
    cfg = load_dict(project_id, settings.asr_dict_dir)
    wake_words = cfg.get("wake_words") or [
        w.strip() for w in settings.asr_wake_words.split(",") if w.strip()]

    def asr_factory():
        # 纠错由 FSM 的 correct_fn 统一施加（先归一后纠错，唤醒匹配容错）
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


def voice_status() -> str:
    """健康探针：not_initialized / ready / unavailable:<原因>。"""
    if not _voice_probed:
        return "not_initialized"
    return "ready" if not _voice_init_error else f"unavailable:{_voice_init_error}"


_greeting_mem: dict = {}


def greeting_pcm(project_id: str = ""):
    """唤醒应答语 PCM（24k mono s16le）：首次合成，内存+磁盘缓存复用（零合成延迟）。

    移植自 app.py _greeting_pcm（audit-ASR 优化轮3，冻结不可改）：缓存键含
    model|voice|rate|text —— 应答语改动自动重合成，无需手工清缓存。
    失败返回 None（降级：无音频，状态行仍可见）。
    """
    import hashlib
    import io
    import wave

    from src.asr import load_dict

    cfg = load_dict(project_id, settings.asr_dict_dir)
    text = cfg.get("wake_greeting") or settings.asr_wake_greeting
    key = hashlib.sha1(
        f"{settings.tts_model}|{settings.tts_voice}|{settings.tts_speech_rate}|{text}"
        .encode("utf-8")).hexdigest()[:12]
    if key in _greeting_mem:
        return _greeting_mem[key]
    cache_dir = settings.project_root / "data" / "processed" / "tts_cache"
    path = cache_dir / f"greeting_{key}.wav"
    try:
        if path.exists():
            wav_bytes = path.read_bytes()
        else:
            tts = make_tts()
            if tts is None:
                return None
            wav_bytes = tts.synthesize_sentence(text)
            cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(wav_bytes)
            logger.info("唤醒应答已合成并缓存: %s（%s）", path.name, text)
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            pcm = w.readframes(w.getnframes())
        _greeting_mem[key] = pcm
        return pcm
    except Exception as e:
        logger.warning("唤醒应答合成/读取失败（降级无音频）: %s", e)
        return None


def prewarm_voice(project_id: str = "") -> None:
    """后台预热（仅 __main__ 生产入口调用；create_app 不触发，测试零副作用）。"""
    def _warm():
        try:
            make_voice_assistant(project_id)   # VAD 探针（失败原因进 voice_status）
        except Exception:
            pass
        try:
            pcm = greeting_pcm(project_id)
            logger.info("唤醒应答预热: %s", f"{len(pcm) / 48000:.1f}s" if pcm else "未就绪")
        except Exception as e:
            logger.warning("唤醒应答预热失败: %s", e)

    threading.Thread(target=_warm, daemon=True).start()
