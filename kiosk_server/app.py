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

from . import __version__
from .config import KioskConfig
from .ocr import OcrClient, OcrError
from .presets import load_presets

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
            if request.headers.get("X-Kiosk-Token") != self._token:
                return JSONResponse({"detail": "未授权"}, status_code=401)
        return await call_next(request)


def create_app(config: KioskConfig | None = None, ocr_client=None) -> FastAPI:
    cfg = config or KioskConfig.from_env()
    app = FastAPI(title="kiosk_server", version=__version__)
    app.state.config = cfg
    app.state.ocr = ocr_client or OcrClient(cfg.ocr_model, cfg.ocr_max_image_bytes)
    app.add_middleware(_TokenMiddleware, token=cfg.token)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cfg.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health():
        # M1 最小探针；kb/vad/tts 字段随 M2/M3 服务接入追加
        return {"ok": True, "service": "kiosk_server", "version": __version__}

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
