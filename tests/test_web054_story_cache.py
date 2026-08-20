# tests/test_web054_story_cache.py
# web-054：同名故事缓存——主题归一化/落盘命中/500MB LRU 整故事淘汰
import json
from kiosk_server.story import StoryCache


class TestStoryCache:
    def test_story_id_normalized(self):
        assert StoryCache.story_id("霸王别姬") == StoryCache.story_id(" 霸王别姬！")
        assert StoryCache.story_id("霸王别姬") != StoryCache.story_id("嫦娥奔月")

    def test_save_and_load(self, tmp_path):
        c = StoryCache(str(tmp_path), 500)
        sid = c.save("霸王别姬", {"title": "霸王别姬", "characters": "虞姬",
                                  "scenes": ["a", "b"]})
        hit = c.load(" 霸王别姬。")
        assert hit and hit["id"] == sid and hit["scenes"] == ["a", "b"]
        assert (tmp_path / sid / "meta.json").exists()

    def test_miss(self, tmp_path):
        assert StoryCache(str(tmp_path), 500).load("不存在") is None

    def test_image_path(self, tmp_path):
        c = StoryCache(str(tmp_path), 500)
        assert c.image_path("abc", 3).name == "page_3.png"

    def test_lru_eviction(self, tmp_path):
        c = StoryCache(str(tmp_path), 1)          # 1MB 上限
        for i in range(3):
            sid = c.save(f"主题{i}", {"title": "t", "characters": "", "scenes": ["x"]})
            (tmp_path / sid / "page_1.png").write_bytes(b"x" * 600 * 1024)
            c.save_meta_touch(sid, last_access=float(i))   # 测试注入访问时刻
        c.evict_if_needed()
        remaining = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
        assert remaining == [StoryCache.story_id("主题2")]   # 最旧两个被淘汰
