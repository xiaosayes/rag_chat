"""薄层独立配置（web-001）。

只读 import src.config；本模块用 os.getenv 直读 KIOSK_* 环境变量，
避免修改冻结的 src/config.py（后端冻结红线）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class KioskConfig:
    host: str = "0.0.0.0"
    port: int = 7861
    token: str = ""                        # KIOSK_API_TOKEN；空=不鉴权（内网部署现状水位）
    cors_origins: tuple = ("*",)           # KIOSK_CORS_ORIGINS 逗号分隔
    persona: str = "湘小图"                # KIOSK_PERSONA（人称统一湘小图，用户拍板）
    ocr_model: str = "qwen-vl-ocr-latest"  # KIOSK_OCR_MODEL（百炼 OCR，同 DASHSCOPE_API_KEY）
    ocr_max_image_bytes: int = 8 * 1024 * 1024
    presets_path: str = "data/kiosk/preset_questions.json"  # KIOSK_PRESETS_PATH
    project_id: str = "jiabohui"           # KIOSK_PROJECT_ID（生产项目）
    idle_home_s: float = 150.0             # KIOSK_IDLE_HOME_S（参考实现 150s 回首页）
    idle_refresh_s: float = 300.0          # KIOSK_IDLE_REFRESH_S（参考实现 300s 自刷新）
    first_floor_chars: int = 12            # KIOSK_TTS_FIRST_FLOOR_CHARS（web-030 首播硬地板，0=禁用）
    web_fallback: bool = True              # KIOSK_WEB_FALLBACK（web-036 知识库拒答→联网搜索兜底）

    @classmethod
    def from_env(cls) -> "KioskConfig":
        origins = tuple(
            o.strip() for o in os.getenv("KIOSK_CORS_ORIGINS", "*").split(",") if o.strip()
        )
        return cls(
            host=os.getenv("KIOSK_API_HOST", cls.host),
            port=int(os.getenv("KIOSK_API_PORT", str(cls.port))),
            token=os.getenv("KIOSK_API_TOKEN", "").strip(),
            cors_origins=origins or ("*",),
            persona=os.getenv("KIOSK_PERSONA", cls.persona),
            ocr_model=os.getenv("KIOSK_OCR_MODEL", cls.ocr_model),
            presets_path=os.getenv("KIOSK_PRESETS_PATH", cls.presets_path),
            project_id=os.getenv("KIOSK_PROJECT_ID", cls.project_id),
            idle_home_s=float(os.getenv("KIOSK_IDLE_HOME_S", str(cls.idle_home_s))),
            idle_refresh_s=float(os.getenv("KIOSK_IDLE_REFRESH_S", str(cls.idle_refresh_s))),
            first_floor_chars=int(os.getenv("KIOSK_TTS_FIRST_FLOOR_CHARS",
                                            str(cls.first_floor_chars))),
            web_fallback=os.getenv("KIOSK_WEB_FALLBACK", "true").lower() in ("1", "true", "yes"),
        )
