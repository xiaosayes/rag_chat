"""FastAPI 装配（web-004）：数字人薄层 HTTP 端点。

只读 import src.config.settings；冻结内核零改动。
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from src.config import settings

from . import __version__, services
from .config import KioskConfig
from .ocr import OcrClient, OcrError
from .presets import load_presets
from .voice_ws import register_voice_ws

logger = logging.getLogger(__name__)


class _OcrRequest(BaseModel):
    image_base64: str


class _TokenMiddleware(BaseHTTPMiddleware):
    """可选令牌（web-004）：KIOSK_API_TOKEN 配置后 /api/*（除 /api/health）须带 X-Kiosk-Token。"""

    def __init__(self, app, token: str):
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next):
        if self._token and request.url.path.startswith("/api/") \
                and request.url.path != "/api/health":
            # web-058：header 优先，回退查询参数（浏览器 <img> 无法设自定义头）
            token = request.headers.get("X-Kiosk-Token") or request.query_params.get("token")
            if token != self._token:
                return JSONResponse({"detail": "未授权"}, status_code=401)
        return await call_next(request)


def create_app(config: KioskConfig | None = None, ocr_client=None,
               session_factory=None) -> FastAPI:
    cfg = config or KioskConfig.from_env()
    app = FastAPI(title="kiosk_server", version=__version__)
    app.state.config = cfg
    app.state.ocr = ocr_client or OcrClient(cfg.ocr_model, cfg.ocr_max_image_bytes)
    register_voice_ws(app, cfg, session_factory)   # web-008：/ws/voice
    app.add_middleware(_TokenMiddleware, token=cfg.token)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health():
        # web-008：kb（懒加载探针，不触发加载）；tts=配置面可用性
        # web-011：vad=语音探针（not_initialized/ready/unavailable:<原因>）
        return {"ok": True, "service": "kiosk_server", "version": __version__,
                "kb": services.pipeline_status(),
                "tts": bool(settings.tts_enabled and settings.dashscope_api_key
                            and settings.tts_voice),
                "vad": services.voice_status()}

    @app.get("/api/config")
    def client_config():
        return {
            "persona": cfg.persona,
            "wake_words": [w.strip() for w in settings.asr_wake_words.split(",") if w.strip()],
            "tts_enabled": settings.tts_enabled,
            "idle_home_s": cfg.idle_home_s,
            "idle_refresh_s": cfg.idle_refresh_s,
        }

    @app.get("/api/presets")
    def presets():
        return {"questions": load_presets(cfg.presets_path)}

    from fastapi.responses import FileResponse
    from pathlib import Path

    @app.get("/api/story/{sid}/img/{n}")
    def story_img(sid: str, n: int):
        # web-058：绘本插图供图（服务端落盘，前端不碰 OSS 临时链）
        if not sid.isalnum() or len(sid) > 32 or not (1 <= n <= 99):
            return JSONResponse({"detail": "参数非法"}, status_code=404)
        path = Path(cfg.story_cache_dir) / sid / f"page_{n}.png"
        if not path.is_file():
            return JSONResponse({"detail": "插图未就绪"}, status_code=404)
        return FileResponse(path, media_type="image/png")

    @app.post("/api/ocr")
    def ocr(req: _OcrRequest):
        try:
            text = app.state.ocr.recognize(req.image_base64)
        except OcrError as e:
            msg = str(e)
            if msg in ("空图像", "图像 base64 非法", "图像过大"):
                return JSONResponse({"detail": msg}, status_code=400)
            logger.warning("OCR 失败: %s", msg)
            return JSONResponse({"detail": "OCR 服务暂不可用"}, status_code=502)
        return {"text": text}

    return app
