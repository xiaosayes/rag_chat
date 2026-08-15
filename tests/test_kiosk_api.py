# web-001：kiosk_server 独立配置（os.getenv 直读，不改 src/config.py）
import os

import pytest

from kiosk_server.config import KioskConfig


class TestKioskConfig:
    def test_defaults(self, monkeypatch):
        for k in list(os.environ):
            if k.startswith("KIOSK_"):
                monkeypatch.delenv(k)
        cfg = KioskConfig.from_env()
        assert cfg.port == 7861
        assert cfg.token == ""                 # 默认不鉴权（内网）
        assert cfg.persona == "湘小图"
        assert cfg.ocr_model == "qwen-vl-ocr-latest"
        assert cfg.project_id == "jiabohui"
        assert cfg.idle_home_s == 150.0
        assert cfg.idle_refresh_s == 300.0
        assert cfg.cors_origins == ("*",)

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("KIOSK_API_PORT", "9000")
        monkeypatch.setenv("KIOSK_API_TOKEN", "s3cret")
        monkeypatch.setenv("KIOSK_CORS_ORIGINS", "http://a.local, http://b.local ,")
        monkeypatch.setenv("KIOSK_PRESETS_PATH", "data/kiosk/x.json")
        cfg = KioskConfig.from_env()
        assert cfg.port == 9000
        assert cfg.token == "s3cret"
        assert cfg.cors_origins == ("http://a.local", "http://b.local")
        assert cfg.presets_path == "data/kiosk/x.json"
