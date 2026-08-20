# tests/test_web051_story_intent.py
# web-051：故事意图薄层正则（宁漏勿抢：误判抢问答=事故，漏判进问答=无害）
from kiosk_server.story import parse_story_intent


class TestStoryIntent:
    def test_hit_patterns(self):
        assert parse_story_intent("给我讲一个霸王别姬的故事") == "霸王别姬"
        assert parse_story_intent("讲个嫦娥奔月的故事") == "嫦娥奔月"
        assert parse_story_intent("说一段后羿射日的故事吧") == "后羿射日"
        assert parse_story_intent("请给我讲一个三只小猪的故事") == "三只小猪"
        assert parse_story_intent("我想听孙悟空三打白骨精的故事") == "孙悟空三打白骨精"
        assert parse_story_intent("讲一个小红帽的绘本") == "小红帽"

    def test_miss_returns_none(self):
        assert parse_story_intent("讲个故事") is None            # 无主题不触发
        assert parse_story_intent("讲一下故事") is None
        assert parse_story_intent("霸王别姬是谁") is None         # 无讲/说动词
        assert parse_story_intent("图书馆几点关门") is None
        assert parse_story_intent("这个故事讲了什么") is None
        assert parse_story_intent("") is None
        assert parse_story_intent("   ") is None

    def test_theme_cleanup(self):
        assert parse_story_intent("给我讲一个 太空 的故事！") == "太空"
        assert parse_story_intent("讲一个龟兔赛跑的故事。") == "龟兔赛跑"
