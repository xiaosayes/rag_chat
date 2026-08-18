# web-036：联网搜索兜底——知识库无确切信息且内核按既定策略拒答（固定话术）时，
# 薄层接管为百炼 enable_search 流式作答。全部离线（假内核流 + monkeypatch 假 LLM）。
import pytest

from kiosk_server.web_fallback import REFUSAL_PREFIX, WebFallbackPipeline
from src.rag_pipeline import KB_NO_INFO_REPLY


class FakeInner:
    """假内核 pipeline：按脚本产出 query_stream 事件。"""

    def __init__(self, items):
        self._items = items
        self.calls = []

    def query_stream(self, question, conversation_history=None, **kw):
        self.calls.append(question)
        yield from self._items


META = {"type": "meta", "from_kb": True, "query_type": "factual", "search_enabled": False}


def _collect(pipe, q="测试问题"):
    return list(pipe.query_stream(question=q, conversation_history=[]))


class TestRefusalFallback:
    def test_refusal_triggers_web_fallback(self, monkeypatch):
        monkeypatch.setattr("kiosk_server.web_fallback._web_search_stream",
                            lambda q, h: iter(["湖南省少年儿童图书馆", "位于长沙市。"]))
        pipe = WebFallbackPipeline(FakeInner([META, KB_NO_INFO_REPLY]))
        out = _collect(pipe)
        assert out[0] is META                       # meta 透传
        assert "".join(x for x in out[1:] if isinstance(x, str)) == \
            "湖南省少年儿童图书馆位于长沙市。"        # 拒答模板被联网回答替换
        assert KB_NO_INFO_REPLY not in out

    def test_refusal_split_chunks_still_detected(self, monkeypatch):
        """拒答模板被分成多段 yield 也能识别（前缀累积判定）。"""
        monkeypatch.setattr("kiosk_server.web_fallback._web_search_stream",
                            lambda q, h: iter(["联网回答"]))
        half = len(KB_NO_INFO_REPLY) // 2
        pipe = WebFallbackPipeline(FakeInner([KB_NO_INFO_REPLY[:half], KB_NO_INFO_REPLY[half:]]))
        out = _collect(pipe)
        assert out == ["联网回答"]

    def test_fallback_failure_yields_refusal(self, monkeypatch):
        def _boom(q, h):
            raise RuntimeError("dashscope down")
            yield  # pragma: no cover
        monkeypatch.setattr("kiosk_server.web_fallback._web_search_stream", _boom)
        pipe = WebFallbackPipeline(FakeInner([META, KB_NO_INFO_REPLY]))
        out = _collect(pipe)
        assert out[0] is META
        assert out[1] == KB_NO_INFO_REPLY           # 优雅回退固定话术，不崩

    def test_fallback_receives_history(self, monkeypatch):
        seen = {}

        def _fake(q, h):
            seen["q"], seen["h"] = q, h
            yield "ok"
        monkeypatch.setattr("kiosk_server.web_fallback._web_search_stream", _fake)
        pipe = WebFallbackPipeline(FakeInner([KB_NO_INFO_REPLY]))
        list(pipe.query_stream(question="q1", conversation_history=[{"role": "user", "content": "hi"}]))
        assert seen["q"] == "q1" and seen["h"] == [{"role": "user", "content": "hi"}]


class TestPassthrough:
    def test_normal_answer_untouched(self, monkeypatch):
        called = []
        monkeypatch.setattr("kiosk_server.web_fallback._web_search_stream",
                            lambda q, h: called.append(1) or iter([]))
        inner = FakeInner([META, "家博会开放时间是", "9点到17点。"])
        out = _collect(WebFallbackPipeline(inner))
        assert out == [META, "家博会开放时间是", "9点到17点。"]
        assert called == []                          # 正常回答绝不触发联网

    def test_meta_only_stream(self):
        out = _collect(WebFallbackPipeline(FakeInner([META])))
        assert out == [META]

    def test_disabled_passthrough(self, monkeypatch):
        called = []
        monkeypatch.setattr("kiosk_server.web_fallback._web_search_stream",
                            lambda q, h: called.append(1) or iter([]))
        pipe = WebFallbackPipeline(FakeInner([META, KB_NO_INFO_REPLY]), enabled=False)
        out = _collect(pipe)
        assert out == [META, KB_NO_INFO_REPLY]       # 开关关闭：行为与内核完全一致
        assert called == []

    def test_prefix_constant_sane(self):
        assert REFUSAL_PREFIX
        assert KB_NO_INFO_REPLY.startswith(REFUSAL_PREFIX)


class TestWebSearchStreamParams:
    """web-040：兜底流参数——max_tokens 硬限长、历史裁剪、Markdown ** 剥离（离线假 Generation）。"""

    def test_params_history_trim_and_strip(self, monkeypatch):
        import types

        captured = {}

        def fake_call(**kw):
            captured.update(kw)
            msg = types.SimpleNamespace(content="你好**世界**")
            choice = types.SimpleNamespace(message=msg)
            resp = types.SimpleNamespace(status_code=200,
                                         output=types.SimpleNamespace(choices=[choice]))
            return iter([resp])
        monkeypatch.setattr("dashscope.Generation.call", fake_call)

        from kiosk_server.web_fallback import (FALLBACK_HISTORY, FALLBACK_MAX_TOKENS,
                                               _web_search_stream)
        hist = [{"role": "user", "content": f"h{i}"} for i in range(6)]
        out = list(_web_search_stream("问题", hist))
        assert captured["max_tokens"] == FALLBACK_MAX_TOKENS      # 硬限长
        assert captured["enable_search"] is True
        # system + 裁剪后历史 + 当前问题
        assert len(captured["messages"]) == 1 + min(len(hist), FALLBACK_HISTORY) + 1
        assert captured["messages"][-2:] == [
            {"role": "user", "content": "h5"}, {"role": "user", "content": "问题"}]
        assert out == ["你好世界"]                                # ** 剥离
