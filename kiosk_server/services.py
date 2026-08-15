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
    """测试/关停用：清空单例缓存。"""
    global _pipeline, _pipeline_project, _pipeline_status
    with _lock:
        _pipeline = None
        _pipeline_project = ""
        _pipeline_status = "not_loaded"
