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
    # ---- web-050 故事绘本配置族（KIOSK_STORY_*，默认零行为变化） ----
    story_enabled: bool = True             # KIOSK_STORY_ENABLED（false=意图拦截关闭，全走问答）
    story_script_model: str = "qwen-plus"  # KIOSK_STORY_SCRIPT_MODEL（分镜脚本，固定云端）
    story_script_max_tokens: int = 1600    # KIOSK_STORY_SCRIPT_MAX_TOKENS（独立限长，先例 FALLBACK_MAX_TOKENS）
    story_script_timeout_s: float = 60.0   # KIOSK_STORY_SCRIPT_TIMEOUT_S
    story_min_scenes: int = 8              # KIOSK_STORY_MIN_SCENES（分镜目标区间下限）
    story_max_scenes: int = 10             # KIOSK_STORY_MAX_SCENES（上限）
    story_scene_max_chars: int = 80        # KIOSK_STORY_SCENE_MAX_CHARS（单镜头字数硬限）
    story_image_model: str = "qwen-image-3.0"  # KIOSK_STORY_IMAGE_MODEL（文生图，固定云端）
    story_image_size: str = "1024*1024"    # KIOSK_STORY_IMAGE_SIZE（实测 2048 默认→1024 提速）
    story_image_concurrency: int = 2       # KIOSK_STORY_IMAGE_CONCURRENCY（web-067：实测并发>2 触发 429 Throttling.RateQuota；429 退避重试见 ImageClient）
    story_first_image_fast: bool = True    # KIOSK_STORY_FIRST_IMAGE_FAST（首页插图并行预生成，首屏提速）
    story_image_timeout_s: float = 90.0    # KIOSK_STORY_IMAGE_TIMEOUT_S（单张超时，实测 13~18s）
    story_total_budget_s: float = 300.0    # KIOSK_STORY_TOTAL_BUDGET_S（整故事插图总预算）
    story_cache_dir: str = "data/story"    # KIOSK_STORY_CACHE_DIR（同名故事缓存落盘目录）
    story_cache_max_mb: int = 500          # KIOSK_STORY_CACHE_MAX_MB（LRU 容量上限）
    story_closing: str = "故事讲完啦，还想听什么故事吗？"  # KIOSK_STORY_CLOSING（收尾语）

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
            # ---- web-050 故事绘本配置族 ----
            story_enabled=os.getenv("KIOSK_STORY_ENABLED", "true").lower() in ("1", "true", "yes"),
            story_script_model=os.getenv("KIOSK_STORY_SCRIPT_MODEL", cls.story_script_model),
            story_script_max_tokens=int(os.getenv("KIOSK_STORY_SCRIPT_MAX_TOKENS",
                                                  str(cls.story_script_max_tokens))),
            story_script_timeout_s=float(os.getenv("KIOSK_STORY_SCRIPT_TIMEOUT_S",
                                                   str(cls.story_script_timeout_s))),
            story_min_scenes=int(os.getenv("KIOSK_STORY_MIN_SCENES", str(cls.story_min_scenes))),
            story_max_scenes=int(os.getenv("KIOSK_STORY_MAX_SCENES", str(cls.story_max_scenes))),
            story_scene_max_chars=int(os.getenv("KIOSK_STORY_SCENE_MAX_CHARS",
                                                str(cls.story_scene_max_chars))),
            story_image_model=os.getenv("KIOSK_STORY_IMAGE_MODEL", cls.story_image_model),
            story_image_size=os.getenv("KIOSK_STORY_IMAGE_SIZE", cls.story_image_size),
            story_image_concurrency=int(os.getenv("KIOSK_STORY_IMAGE_CONCURRENCY",
                                                  str(cls.story_image_concurrency))),
            story_first_image_fast=os.getenv("KIOSK_STORY_FIRST_IMAGE_FAST",
                                             "true").lower() in ("1", "true", "yes"),
            story_image_timeout_s=float(os.getenv("KIOSK_STORY_IMAGE_TIMEOUT_S",
                                                  str(cls.story_image_timeout_s))),
            story_total_budget_s=float(os.getenv("KIOSK_STORY_TOTAL_BUDGET_S",
                                                 str(cls.story_total_budget_s))),
            story_cache_dir=os.getenv("KIOSK_STORY_CACHE_DIR", cls.story_cache_dir),
            story_cache_max_mb=int(os.getenv("KIOSK_STORY_CACHE_MAX_MB",
                                             str(cls.story_cache_max_mb))),
            story_closing=os.getenv("KIOSK_STORY_CLOSING", cls.story_closing),
        )
