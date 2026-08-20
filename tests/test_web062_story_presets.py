# tests/test_web062_story_presets.py
# web-062：预设池含故事引导入口（服务端缺省池）
from kiosk_server.presets import DEFAULT_PRESETS, load_presets


class TestStoryPresets:
    def test_default_pool_has_story_entry(self):
        assert any("故事" in q for q in DEFAULT_PRESETS)

    def test_fallback_load_keeps_story_entry(self, tmp_path):
        qs = load_presets(str(tmp_path / "missing.json"))
        assert any("故事" in q for q in qs)
