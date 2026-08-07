"""
意图理解分层分类（L0 规则 + L1 语义 + L2 LLM 兜底）单元测试

覆盖：
  - L1 SemanticIntentClassifier：余弦相似度、置信度、原型向量缓存、embedding 失败降级
  - L2 classify_with_llm：成功/带噪声输出/无法解析/无 API Key/调用失败
  - 级联 _classify_intent：L1 高置信 → semantic；低置信 → LLM；LLM 失败 → rules
  - query/query_stream 路由：L1/L2 识别出闲聊 → 转闲聊分支；知识库问题用语义分类结果
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.config import settings
from src.intent_classifier import (
    LLM_INTENT_PROMPT,
    SemanticIntentClassifier,
    classify_with_llm,
    cosine_similarity,
)
from src.rag_pipeline import RAGPipeline, QueryType


# ========== 工具：构造可控向量与 mock embedding ==========

def _basis(dim: int, idx: int) -> list:
    """基向量：仅第 idx 维为 1，其余为 0（用于构造可分离的原型向量）"""
    v = [0.0] * dim
    v[idx] = 1.0
    return v


def _make_embedding(vectors_by_text):
    """embed_query / embed_batch 均按文本返回固定向量的 mock embedding"""
    emb = MagicMock()

    def embed_query(query, use_cache=True):
        return list(vectors_by_text.get(query, []))

    def embed_batch(texts):
        return [list(vectors_by_text.get(t, [])) for t in texts]

    emb.embed_query.side_effect = embed_query
    emb.embed_batch.side_effect = embed_batch
    return emb


def _uniform(dim: int) -> list:
    """全等值向量：与任何基向量的余弦相似度相同（低区分度）"""
    return [1.0] * dim


# ========== L1：cosine_similarity ==========

class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert cosine_similarity([1.0, 2.0], [1.0, 2.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_dim_mismatch_returns_zero(self):
        assert cosine_similarity([1.0, 0.0], [1.0]) == 0.0

    def test_zero_vector_returns_zero(self):
        assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_empty_vectors_returns_zero(self):
        assert cosine_similarity([], []) == 0.0


# ========== L1：SemanticIntentClassifier ==========

class TestSemanticIntentClassifier:
    """原型向量：5 类意图各用不同基向量方向，问题向量对齐某方向即可确定分类"""

    @pytest.fixture(autouse=True)
    def _isolate_embedding_cache(self, monkeypatch):
        """隔离全局 EmbeddingCache：原型计算不读真实持久化缓存（避免真实 API 残留干扰）"""
        monkeypatch.setattr(
            "src.intent_classifier.embedding_cache.get", lambda q: None
        )
        monkeypatch.setattr(
            "src.intent_classifier.embedding_cache.set_pattern",
            lambda *a, **k: None,
        )

    def _classifier(self, dim=5):
        protos = {}
        for idx, intent in enumerate(SemanticIntentClassifier.INTENT_PROTOTYPES):
            for p in SemanticIntentClassifier.INTENT_PROTOTYPES[intent]:
                protos[p] = _basis(dim, idx)
        emb = _make_embedding(protos)
        clf = SemanticIntentClassifier(embedding=emb, min_confidence=0.55)
        # 预计算原型向量（用固定映射），后续修改 side_effect 只影响问题向量
        clf.warmup()
        return clf, emb

    def test_classify_high_confidence(self):
        clf, emb = self._classifier()
        # 问题向量对齐 recommendation 方向（idx 0）
        emb.embed_query.side_effect = lambda q, use_cache=True: _basis(5, 0)
        intent, confidence = clf.classify("推荐点好东西")
        assert intent == "recommendation"
        assert confidence == pytest.approx(1.0)

    def test_classify_each_intent(self):
        clf, emb = self._classifier()
        for idx, intent in enumerate(
            ["recommendation", "factual", "comparison", "open_ended", "chitchat"]
        ):
            emb.embed_query.side_effect = lambda q, i=idx, use_cache=True: _basis(5, i)
            got, conf = clf.classify(f"测试问题{idx}")
            assert got == intent, f"期望 {intent}，实际 {got}"

    def test_classify_low_confidence(self):
        clf, emb = self._classifier()
        # 均匀向量与所有基向量的余弦 ≈ 1/√5 ≈ 0.447 < 0.55
        emb.embed_query.side_effect = lambda q, use_cache=True: _uniform(5)
        intent, confidence = clf.classify("模糊问题")
        assert intent is not None
        assert confidence < clf.min_confidence

    def test_classify_empty_question(self):
        clf, emb = self._classifier()
        assert clf.classify("") == (None, 0.0)
        assert clf.classify("   ") == (None, 0.0)

    def test_classify_embedding_failure(self):
        clf, emb = self._classifier()
        emb.embed_query.side_effect = RuntimeError("API 失败")
        intent, confidence = clf.classify("有问题")
        assert intent is None
        assert confidence == 0.0

    def test_classify_embedding_returns_empty(self):
        clf, emb = self._classifier()
        emb.embed_query.side_effect = lambda q, use_cache=True: []
        intent, confidence = clf.classify("有问题")
        assert intent is None
        assert confidence == 0.0

    def test_prototype_vectors_cached(self):
        calls = []

        def embed_query(query, use_cache=True):
            calls.append(query)
            return _basis(5, 0)

        emb = MagicMock()
        emb.embed_query.side_effect = embed_query
        emb.embed_batch.side_effect = lambda texts: [
            embed_query(t) for t in texts
        ]
        clf = SemanticIntentClassifier(embedding=emb)
        clf.classify("问题1")
        after_first = len(calls)
        clf.classify("问题2")
        # 第二次只新增 1 次调用（问题自身的 embedding），原型不再重新计算
        assert len(calls) == after_first + 1

    def test_warmup_precomputes(self):
        emb = MagicMock()
        embs = []

        def embed_query(query, use_cache=True):
            embs.append(query)
            return _basis(5, 0)

        emb.embed_query.side_effect = embed_query
        emb.embed_batch.side_effect = lambda texts: [
            embed_query(t) for t in texts
        ]
        clf = SemanticIntentClassifier(embedding=emb)
        clf.warmup()
        n_protos = sum(len(v) for v in SemanticIntentClassifier.INTENT_PROTOTYPES.values())
        assert len(embs) == n_protos
        # warmup 后再 classify，原型不再重复计算（只有问题渲染）
        clf.classify("问题")
        assert len(embs) == n_protos + 1

    def test_partial_embedding_failure_skips(self):
        """个别原型 embedding 失败时跳过，不影响其他意图分类"""
        protos = {}
        for idx, intent in enumerate(SemanticIntentClassifier.INTENT_PROTOTYPES):
            for p in SemanticIntentClassifier.INTENT_PROTOTYPES[intent]:
                protos[p] = _basis(5, idx)
        emb = MagicMock()

        def embed_query(query, use_cache=True):
            if query.startswith("推荐"):
                raise RuntimeError("失败")
            return list(protos.get(query, []))

        emb.embed_query.side_effect = embed_query
        emb.embed_batch.side_effect = lambda texts: [
            embed_query(t) for t in texts
        ]
        clf = SemanticIntentClassifier(embedding=emb)
        clf.warmup()  # 推荐类原型失败被跳过，其余正常
        # 问题对齐 factual（idx 1），推荐类原型虽失败仍能正确分类
        intent, confidence = clf.classify("在哪里展出")
        assert intent == "factual"
        assert confidence == pytest.approx(1.0)


# ========== L2：classify_with_llm ==========

class TestClassifyWithLLM:
    def test_success(self):
        llm = MagicMock()
        llm.chat.return_value = "recommendation"
        with patch.object(settings, "dashscope_api_key", "test-key"):
            assert classify_with_llm(llm, "推荐几个") == "recommendation"

    def test_output_with_noise(self):
        llm = MagicMock()
        llm.chat.return_value = "comparison。\n（以上）"
        with patch.object(settings, "dashscope_api_key", "test-key"):
            assert classify_with_llm(llm, "两个有什么区别") == "comparison"

    def test_output_intent_prefix(self):
        llm = MagicMock()
        llm.chat.return_value = "Intent: factual"
        with patch.object(settings, "dashscope_api_key", "test-key"):
            assert classify_with_llm(llm, "有多重") == "factual"

    def test_unparseable_output(self):
        llm = MagicMock()
        llm.chat.return_value = "我不太确定"
        with patch.object(settings, "dashscope_api_key", "test-key"):
            assert classify_with_llm(llm, "随便") is None

    def test_empty_output(self):
        llm = MagicMock()
        llm.chat.return_value = ""
        with patch.object(settings, "dashscope_api_key", "test-key"):
            assert classify_with_llm(llm, "随便") is None

    def test_no_api_key_skips_call(self):
        llm = MagicMock()
        with patch.object(settings, "dashscope_api_key", ""):
            assert classify_with_llm(llm, "随便") is None
        llm.chat.assert_not_called()

    def test_llm_failure(self):
        llm = MagicMock()
        llm.chat.side_effect = RuntimeError("API 失败")
        with patch.object(settings, "dashscope_api_key", "test-key"):
            assert classify_with_llm(llm, "随便") is None

    def test_prompt_contains_question(self):
        llm = MagicMock()
        llm.chat.return_value = "chitchat"
        with patch.object(settings, "dashscope_api_key", "test-key"):
            classify_with_llm(llm, "你好呀")
        sent = llm.chat.call_args.kwargs["messages"][0]["content"]
        assert "你好呀" in sent
        assert LLM_INTENT_PROMPT[:10] in sent


# ========== 级联：RAGPipeline._classify_intent ==========

class TestIntentCascade:
    @pytest.fixture
    def pipeline(self):
        return RAGPipeline(local_mode=True)

    def test_semantic_high_confidence(self, pipeline):
        with patch.object(pipeline.intent_classifier, "classify",
                          return_value=("recommendation", 0.9)), \
             patch.object(pipeline, "classify_query") as mock_rules:
            qt, method = pipeline._classify_intent("推荐点东西")
        assert qt == QueryType.RECOMMENDATION
        assert method == "semantic"
        mock_rules.assert_not_called()

    def test_llm_fallback_low_confidence(self, pipeline):
        with patch.object(pipeline.intent_classifier, "classify",
                          return_value=("factual", 0.3)), \
             patch.object(pipeline.llm, "chat", return_value="comparison"):
            qt, method = pipeline._classify_intent("这两个有啥区别")
        assert qt == QueryType.COMPARISON
        assert method == "llm"

    def test_llm_fallback_embedding_failure(self, pipeline):
        """L1 embedding 失败（None）时仍尝试 L2 LLM"""
        with patch.object(pipeline.intent_classifier, "classify",
                          return_value=(None, 0.0)), \
             patch.object(pipeline.llm, "chat", return_value="open_ended"):
            qt, method = pipeline._classify_intent("谈谈")
        assert qt == QueryType.OPEN_ENDED
        assert method == "llm"

    def test_rules_fallback_on_llm_failure(self, pipeline):
        with patch.object(pipeline.intent_classifier, "classify",
                          return_value=(None, 0.0)), \
             patch.object(settings, "dashscope_api_key", "test-key"), \
             patch.object(pipeline.llm, "chat", side_effect=RuntimeError("失败")):
            qt, method = pipeline._classify_intent("司母戊鼎有多重")
        assert method == "rules"
        assert qt == QueryType.FACTUAL

    def test_rules_fallback_no_api_key(self, pipeline):
        """无 API Key 时 L2 直接跳过，走规则评分"""
        with patch.object(pipeline.intent_classifier, "classify",
                          return_value=("comparison", 0.3)), \
             patch.object(settings, "dashscope_api_key", ""), \
             patch.object(pipeline, "classify_query",
                          return_value=QueryType.RECOMMENDATION) as mock_rules:
            qt, method = pipeline._classify_intent("推荐一下")
        assert qt == QueryType.RECOMMENDATION
        assert method == "rules"
        mock_rules.assert_called_once()

    def test_semantic_disabled_uses_rules(self, pipeline):
        with patch.object(settings, "intent_semantic_enabled", False), \
             patch.object(pipeline, "classify_query",
                          return_value=QueryType.OPEN_ENDED) as mock_rules:
            qt, method = pipeline._classify_intent("谈谈")
        assert qt == QueryType.OPEN_ENDED
        assert method == "rules"
        mock_rules.assert_called_once()

    def test_llm_fallback_disabled_uses_rules(self, pipeline):
        with patch.object(pipeline.intent_classifier, "classify",
                          return_value=("comparison", 0.3)), \
             patch.object(settings, "intent_llm_fallback_enabled", False), \
             patch.object(pipeline, "classify_query",
                          return_value=QueryType.RECOMMENDATION) as mock_rules:
            qt, method = pipeline._classify_intent("推荐一下")
        assert qt == QueryType.RECOMMENDATION
        assert method == "rules"
        mock_rules.assert_called_once()

    def test_semantic_chitchat_mapped(self, pipeline):
        with patch.object(pipeline.intent_classifier, "classify",
                          return_value=("chitchat", 0.9)):
            qt, method = pipeline._classify_intent("你好呀")
        assert qt == QueryType.CHITCHAT
        assert method == "semantic"


# ========== 集成：query / query_stream 路由 ==========

class TestQueryRoutingIntegration:
    @pytest.fixture
    def pipeline(self):
        return RAGPipeline(local_mode=True)

    def test_query_kb_uses_semantic_intent(self, pipeline):
        """知识库问题：语义分类结果进入返回 query_type"""
        with patch.object(pipeline, "is_kb_related", return_value=True), \
             patch.object(pipeline, "_classify_intent",
                          return_value=(QueryType.RECOMMENDATION, "semantic")), \
             patch.object(pipeline, "_ensure_knowledge_base") as mock_kb, \
             patch.object(pipeline.hybrid_retriever, "retrieve", return_value=[]), \
             patch.object(pipeline.llm, "chat", return_value="mock 回答"):
            result = pipeline.query("推荐一些文物")
        assert result["query_type"] == "recommendation"
        assert result["from_kb"] is True
        mock_kb.assert_called_once()

    def test_query_routes_chitchat_from_semantic(self, pipeline):
        """L1 识别出规则层漏掉的闲聊 → 转闲聊分支（from_kb=False）"""
        with patch.object(pipeline, "is_kb_related", return_value=True), \
             patch.object(pipeline, "_classify_intent",
                          return_value=(QueryType.CHITCHAT, "semantic")), \
             patch.object(pipeline.llm, "chat", return_value="闲聊回答"):
            result = pipeline.query("帮我推荐个电影")
        assert result["query_type"] == "chitchat"
        assert result["from_kb"] is False
        assert result["retrieved_chunks"] == []
        assert result["context"] == ""

    def test_query_routes_chitchat_from_llm(self, pipeline):
        """L2 LLM 兜底识别出闲聊 → 转闲聊分支"""
        with patch.object(pipeline, "is_kb_related", return_value=True), \
             patch.object(pipeline, "_classify_intent",
                          return_value=(QueryType.CHITCHAT, "llm")), \
             patch.object(pipeline.llm, "chat", return_value="闲聊回答"):
            result = pipeline.query("谢谢啦")
        assert result["query_type"] == "chitchat"
        assert result["from_kb"] is False

    def test_query_rules_route_unchanged(self, pipeline):
        """规则层闲聊（is_kb_related=False）行为不变：不触发语义分类"""
        with patch.object(pipeline, "is_kb_related", return_value=False), \
             patch.object(pipeline, "_classify_intent") as mock_cls, \
             patch.object(pipeline.llm, "chat", return_value="你好你好"):
            result = pipeline.query("你好")
        assert result["query_type"] == "chitchat"
        assert result["from_kb"] is False
        mock_cls.assert_not_called()

    def test_query_stream_routes_chitchat_from_semantic(self, pipeline):
        """流式：L1 识别出闲聊 → 第一个 meta 为 from_kb=False"""
        with patch.object(pipeline, "is_kb_related", return_value=True), \
             patch.object(pipeline, "_classify_intent",
                          return_value=(QueryType.CHITCHAT, "semantic")), \
             patch.object(pipeline.llm, "chat_stream", return_value=iter(["好"])):
            events = list(pipeline.query_stream("帮我推荐个电影"))
        assert events[0]["type"] == "meta"
        assert events[0]["from_kb"] is False
        assert events[0]["query_type"] == "chitchat"

    def test_query_stream_kb_uses_semantic_intent(self, pipeline):
        """流式：知识库问题 meta 携带语义分类结果"""
        with patch.object(pipeline, "is_kb_related", return_value=True), \
             patch.object(pipeline, "_classify_intent",
                          return_value=(QueryType.FACTUAL, "semantic")), \
             patch.object(pipeline, "_ensure_knowledge_base"), \
             patch.object(pipeline.hybrid_retriever, "retrieve", return_value=[]), \
             patch.object(pipeline.llm, "chat_stream", return_value=iter(["答"])):
            events = list(pipeline.query_stream("司母戊鼎有多重"))
        assert events[0]["from_kb"] is True
        assert events[0]["query_type"] == "factual"

    def test_query_type_enum_has_chitchat(self):
        assert QueryType.CHITCHAT.value == "chitchat"

    def test_good_acknowledgement_routed_to_chitchat(self):
        """'好的吧' 等口头应答词应被规则层识别为闲聊（不触发语义分类）"""
        p = RAGPipeline(local_mode=True)
        assert p.is_kb_related("好的吧") is False
        assert p.is_kb_related("好的") is False
        # 含实质内容的查询不受影响
        assert p.is_kb_related("好的文物有哪些") is True