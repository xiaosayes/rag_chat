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


class TestFallbackPromptBroadcastFriendly:
    """web-043：兜底提示词约束播报友好度——口语短句、无列表/编号、避免英文术语。"""

    def test_prompt_forbids_list_and_numbering(self):
        from kiosk_server.web_fallback import FALLBACK_SYSTEM_PROMPT
        assert "列表" in FALLBACK_SYSTEM_PROMPT
        assert "编号" in FALLBACK_SYSTEM_PROMPT

    def test_prompt_avoids_english_terms(self):
        from kiosk_server.web_fallback import FALLBACK_SYSTEM_PROMPT
        assert "英文" in FALLBACK_SYSTEM_PROMPT

    def test_prompt_keeps_existing_constraints(self):
        """既有约束不回退：口语化/100字以内/禁 Markdown/不编造/不提实现细节。"""
        from kiosk_server.web_fallback import FALLBACK_SYSTEM_PROMPT
        assert "口语化" in FALLBACK_SYSTEM_PROMPT
        assert "100字以内" in FALLBACK_SYSTEM_PROMPT
        assert "Markdown" in FALLBACK_SYSTEM_PROMPT
        assert "编造" in FALLBACK_SYSTEM_PROMPT


class TestFallbackLocalProvider:
    """web-044：provider=local 时兜底改走本地 OpenAI 兼容模型
    （无实时联网能力，按模型自有知识作答；提示词/限长/裁历史不变）。"""

    def test_local_provider_routes_to_local_stream(self, monkeypatch):
        from src.config import settings
        monkeypatch.setattr(settings, "llm_provider", "local")
        monkeypatch.setattr("kiosk_server.web_fallback._local_answer_stream",
                            lambda q, h: iter(["本地模型作答"]))
        pipe = WebFallbackPipeline(FakeInner([META, KB_NO_INFO_REPLY]))
        out = _collect(pipe)
        assert out[0] is META
        assert "本地模型作答" in out
        assert KB_NO_INFO_REPLY not in out

    def test_dashscope_provider_keeps_generation_path(self, monkeypatch):
        """默认（dashscope）路径零变化：仍走 Generation.call 联网流式。"""
        import types

        from src.config import settings
        monkeypatch.setattr(settings, "llm_provider", "dashscope")
        called = {}

        def _fake_local(q, h):
            called["local"] = True
            yield "不应出现"

        monkeypatch.setattr("kiosk_server.web_fallback._local_answer_stream", _fake_local)
        msg = types.SimpleNamespace(content="百炼联网答")
        choice = types.SimpleNamespace(message=msg)
        resp = types.SimpleNamespace(status_code=200,
                                     output=types.SimpleNamespace(choices=[choice]))
        monkeypatch.setattr("dashscope.Generation.call", lambda **kw: iter([resp]))

        from kiosk_server.web_fallback import _web_search_stream
        out = list(_web_search_stream("问题", []))
        assert out == ["百炼联网答"]
        assert "local" not in called

    def test_local_answer_stream_params_strip_and_history(self, monkeypatch):
        """_local_answer_stream：本地模型参数接线 + 320 硬限 + 剥 emoji/** + 历史裁 1 轮。"""
        from src.config import settings
        monkeypatch.setattr(settings, "llm_provider", "local")
        monkeypatch.setattr(settings, "local_llm_model", "m-x")
        monkeypatch.setattr(settings, "local_llm_base_url", "http://local:9/v1")
        monkeypatch.setattr(settings, "local_llm_api_key", "sekret")

        captured = {}

        class _FakeLocalLLM:
            def __init__(self, **kw):
                captured.update(kw)

            def chat_stream(self, messages, system_prompt=None, enable_search=False):
                captured["messages"] = messages
                captured["system_prompt"] = system_prompt
                captured["enable_search"] = enable_search
                yield "你😀好**世**界"

        monkeypatch.setattr("src.llm.LocalOpenAILLM", _FakeLocalLLM)

        from kiosk_server.web_fallback import (FALLBACK_HISTORY, FALLBACK_MAX_TOKENS,
                                               FALLBACK_SYSTEM_PROMPT,
                                               _local_answer_stream)
        hist = [{"role": "user", "content": f"h{i}"} for i in range(4)]
        out = list(_local_answer_stream("问题", hist))
        assert "".join(out) == "你好世界"                    # emoji + ** 剥离
        assert captured["model"] == "m-x"
        assert captured["base_url"] == "http://local:9/v1"
        assert captured["api_key"] == "sekret"
        assert captured["max_tokens"] == min(settings.llm_max_tokens,
                                             FALLBACK_MAX_TOKENS)   # 硬限长
        assert captured["system_prompt"] == FALLBACK_SYSTEM_PROMPT  # 湘小图人设不变
        assert captured["enable_search"] is True             # 语义保留（本地实现忽略并告警）
        assert captured["messages"][-2:] == [
            {"role": "user", "content": "h3"},
            {"role": "user", "content": "问题"}]             # 历史裁至 FALLBACK_HISTORY 条
        assert len(captured["messages"]) == FALLBACK_HISTORY + 1
