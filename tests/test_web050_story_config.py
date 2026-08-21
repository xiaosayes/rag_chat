# web-050：故事绘本配置族（KIOSK_STORY_*，默认零行为变化）
import os
from kiosk_server.config import KioskConfig


class TestStoryConfig:
    def test_defaults(self, monkeypatch):
        for k in list(os.environ):
            if k.startswith("KIOSK_STORY_"):
                monkeypatch.delenv(k)
        cfg = KioskConfig.from_env()
        assert cfg.story_enabled is True
        assert cfg.story_script_model == "qwen-plus"
        assert cfg.story_script_max_tokens == 1600
        assert cfg.story_script_timeout_s == 60.0
        assert cfg.story_min_scenes == 8 and cfg.story_max_scenes == 10
        assert cfg.story_scene_max_chars == 80
        assert cfg.story_image_model == "qwen-image-3.0"
        assert cfg.story_image_size == "1024*1024"
        assert cfg.story_image_concurrency == 2      # web-067：实测并发>2 触发 429
        assert cfg.story_image_timeout_s == 90.0
        assert cfg.story_total_budget_s == 300.0
        assert cfg.story_cache_dir == "data/story"
        assert cfg.story_cache_max_mb == 500
        assert "故事讲完啦" in cfg.story_closing

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("KIOSK_STORY_ENABLED", "false")
        monkeypatch.setenv("KIOSK_STORY_IMAGE_CONCURRENCY", "3")
        monkeypatch.setenv("KIOSK_STORY_CACHE_MAX_MB", "100")
        cfg = KioskConfig.from_env()
        assert cfg.story_enabled is False
        assert cfg.story_image_concurrency == 3
        assert cfg.story_cache_max_mb == 100
