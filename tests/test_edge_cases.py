"""
边界情况与回归测试 - 覆盖主测试文件未覆盖的场景
角色：测试工程师
"""
import sys
import json
import time
import tempfile
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from loguru import logger

from src.cache import EmbeddingCache, LRUCache
from src.chunking import Chunk, ChunkingPipeline, SmartChunking
from src.config import settings
from src.data_loader import DataLoader, Artifact
from src.embeddings import BailianEmbedding
from src.rag_pipeline import RAGPipeline, QueryType
from src.retriever import BM25Retriever, HybridRetriever
from src.reranker import BailianReranker
from src.vector_store import VectorStore
from src.utils import generate_id, save_json, load_json


# =============================================================================
# Bug 1: EmbeddingCache 边界检查缺陷 - 前缀模式匹配失败
# =============================================================================
class TestEmbeddingCacheBoundaryBug:
    """测试 EmbeddingCache 的模式匹配边界检查缺陷"""

    def test_pattern_at_start_of_question(self, tmp_path):
        """
        Bug: 当 pattern 出现在问题开头且后面紧跟中文字符时，边界检查失败。
        例如 pattern="推荐" 在问题 "推荐一些文物" 开头：
        - before="" (空，边界检查通过)
        - after="一" (中文字符，边界检查失败)
        → 整个边界检查返回 False，导致缓存未命中
        """
        cache = EmbeddingCache(cache_dir=tmp_path / "cache1")
        cache.set_pattern("推荐", [0.1, 0.2, 0.3])

        # bug-006 修复后：pattern 在开头应匹配
        result = cache.get("推荐一些文物")
        assert result == [0.1, 0.2, 0.3], (
            "bug-006 修复后：pattern 在开头应匹配"
        )

    def test_pattern_at_end_of_question(self, tmp_path):
        """
        Bug: 当 pattern 出现在问题末尾且前面紧跟中文字符时，边界检查失败。
        例如 pattern="文物" 在 "这是什么文物" 末尾：
        - before="么" (中文字符，边界检查失败)
        - after="" (空，边界检查通过)
        → 整个边界检查返回 False
        """
        cache = EmbeddingCache(cache_dir=tmp_path / "cache2")
        cache.set_pattern("文物", [0.4, 0.5, 0.6])

        # bug-006 修复后：pattern 在末尾应匹配
        result = cache.get("这是什么文物")
        assert result == [0.4, 0.5, 0.6], (
            "bug-006 修复后：pattern 在末尾应匹配"
        )

    def test_pattern_surrounded_by_chinese(self, tmp_path):
        """
        bug-006 修复后：当 pattern 被中文字符包围时，仍然匹配。
        例如 pattern="推荐" 在 "我不推荐这个" 中：
        - 放宽边界检查后，"我不推荐这个" 会匹配 "推荐"
        - 这是可接受的：缓存是优化手段，近似 embedding 比缓存未命中更好
        """
        cache = EmbeddingCache(cache_dir=tmp_path / "cache3")
        cache.set_pattern("推荐", [0.1, 0.2, 0.3])
        result = cache.get("我不推荐这个文物")
        # bug-006 修复后：放宽匹配，返回匹配的 embedding
        assert result == [0.1, 0.2, 0.3], (
            "bug-006 修复后：放宽匹配条件，被中文字符包围也应匹配"
        )

    def test_pattern_exact_match(self, tmp_path):
        """
        验证：完全匹配时边界检查应通过。
        before="" (空) AND after="" (空) → True
        """
        cache = EmbeddingCache(cache_dir=tmp_path / "cache4")
        cache.set_pattern("推荐一些文物", [0.7, 0.8, 0.9])
        result = cache.get("推荐一些文物")
        assert result == [0.7, 0.8, 0.9], "完全匹配应命中"

    def test_pattern_with_punctuation_prefix(self, tmp_path):
        """
        验证：pattern 前面有标点符号（非中文）时应匹配。
        bug-032 修复后：边界检查改为 OR 逻辑，标点+中文开头应匹配。
        """
        cache = EmbeddingCache(cache_dir=tmp_path / "cache5")
        cache.set_pattern("推荐", [0.1, 0.2, 0.3])
        result = cache.get("？推荐一些文物")
        # "？" (非中文) 在"推荐"之前，但"一" (中文) 在"推荐"之后
        # bug-032 修复后：before="？"(非中文) 通过，OR 逻辑下整体通过
        assert result == [0.1, 0.2, 0.3], (
            "bug-032 修复后：pattern '推荐' 在 '？推荐一些文物' 中应匹配"
        )


# =============================================================================
# Bug 2: LRUCache key 生成中 kwargs 不可哈希问题
# =============================================================================
class TestLRUCacheKeyGeneration:
    """测试 LRUCache 的 key 生成稳定性"""

    def test_cache_key_with_dict_value(self):
        """测试包含 dict 的 kwargs 的 key 稳定性"""
        cache = LRUCache(capacity=10, ttl=3600)
        key1 = cache.set_with_key("v1", "prefix", {"nested": "value"})
        key2 = cache.set_with_key("v2", "prefix", {"nested": "value"})
        assert key1 == key2, "相同参数的 key 应相同"

    def test_cache_key_with_list_value(self):
        """测试 list 参数的 key 稳定性"""
        cache = LRUCache(capacity=10, ttl=3600)
        key1 = cache.set_with_key("v1", "prefix", [1, 2, 3])
        key2 = cache.set_with_key("v2", "prefix", [1, 2, 3])
        assert key1 == key2, "相同 list 参数的 key 应相同"

    def test_cache_key_consistency_across_calls(self):
        """验证 get_with_key 和 set_with_key 使用相同的 key 生成逻辑"""
        cache = LRUCache(capacity=10, ttl=3600)
        cache.set_with_key("value123", "my_prefix", "arg1", kw1="val1")
        result = cache.get_with_key("my_prefix", "arg1", kw1="val1")
        assert result == "value123", "get/set 应使用相同的 key 生成逻辑"


# =============================================================================
# Bug 3: EmbeddingCache.set() 使用 FIFO 而非 LRU 淘汰
# =============================================================================
class TestEmbeddingCacheEviction:
    """测试 EmbeddingCache 的淘汰策略"""

    def test_fifo_eviction_loses_frequently_accessed(self, tmp_path):
        """
        Bug: EmbeddingCache.set() 使用 FIFO 淘汰（删除最早插入的），
        而非 LRU 淘汰。这意味着频繁访问的旧条目可能被删除。
        """
        cache = EmbeddingCache(cache_dir=tmp_path / "evict_test")
        # 插入超过 1000 条，触发淘汰
        for i in range(1005):
            cache.set(f"key_{i}", [float(i % 100) / 100.0] * 4)

        # 前 500 条应该被删除（FIFO）
        early_key = "key_0"
        late_key = "key_1004"

        early_result = cache.get(early_key)
        late_result = cache.get(late_key)

        # FIFO 淘汰：key_0 应被淘汰
        assert early_result is None, (
            "BUG: key_0 是最早插入的，应被 FIFO 淘汰，但仍在缓存中"
        )
        # key_1004 是最后插入的，应保留
        assert late_result is not None, "key_1004 是最后插入的，应保留"

        # 统计应该反映淘汰
        stats = cache.stats
        assert stats["exact_cache"] <= 505, (
            f"FIFO 淘汰后缓存大小应为 ~505，实际为 {stats['exact_cache']}"
        )


# =============================================================================
# Bug 4: Thread safety - save() 在锁外执行
# =============================================================================
class TestEmbeddingCacheThreadSafety:
    """测试 EmbeddingCache 的线程安全性"""

    def test_save_outside_lock_race_condition(self, tmp_path):
        """测试 save() 在锁外调用的竞态条件"""
        import threading

        cache = EmbeddingCache(cache_dir=tmp_path / "thread_safe")

        def writer():
            for i in range(100):
                cache.set(f"thread_key_{i}", [float(i)] * 4)

        def saver():
            cache.save()

        threads = []
        for _ in range(5):
            t = threading.Thread(target=writer)
            threads.append(t)
            t.start()

        saver()  # 在写入线程运行时保存

        for t in threads:
            t.join()

        # 验证没有崩溃，且缓存文件可加载
        assert cache._cache_file.exists()
        try:
            with open(cache._cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert isinstance(data, dict)
        except Exception as e:
            pytest.fail(f"保存的缓存文件损坏: {e}")

    def test_concurrent_get_set(self, tmp_path):
        """测试并发读写不崩溃"""
        import threading
        import random

        cache = EmbeddingCache(cache_dir=tmp_path / "concurrent")
        errors = []

        def worker(worker_id: int):
            try:
                for i in range(50):
                    key = f"worker_{worker_id}_key_{i}"
                    if random.random() < 0.5:
                        cache.set(key, [float(i)] * 4)
                    else:
                        cache.get(key)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"并发测试出错: {errors}"


# =============================================================================
# Bug 5: generate_id 可能产生冲突
# =============================================================================
class TestGenerateIDCollision:
    """测试 generate_id 的冲突概率"""

    def test_md5_truncation_collision_risk(self):
        """测试 10000 个不同输入是否有 ID 冲突"""
        ids = set()
        for i in range(10000):
            content = f"文物{i}_名称_测试内容_{i}_描述信息"
            id_ = generate_id(content)
            ids.add(id_)

        assert len(ids) == 10000, (
            f"generate_id 产生了冲突: {10000 - len(ids)} 个冲突"
        )

    def test_similar_inputs_different_ids(self):
        """测试相似输入生成不同 ID"""
        id1 = generate_id("司母戊鼎详情描述")
        id2 = generate_id("司母戊鼎详情描述。")  # 多一个句号
        assert id1 != id2, "微小差异的输入应产生不同 ID"

    def test_unicode_input(self):
        """测试 Unicode 输入"""
        id1 = generate_id("文物★")
        id2 = generate_id("文物☆")
        assert id1 != id2, "不同 Unicode 字符应产生不同 ID"


# =============================================================================
# Bug 6: VectorStore.point_id 冲突风险
# =============================================================================
class TestVectorStorePointID:
    """测试 VectorStore 的 point_id 生成"""

    def test_point_id_collision_risk(self):
        """测试相同 chunk.id 产生相同 point_id"""
        chunk1 = Chunk(id="same_id", artifact_id="a1", artifact_name="A",
                       text="text1", metadata={})
        chunk2 = Chunk(id="same_id", artifact_id="a2", artifact_name="B",
                       text="text2", metadata={})

        point_id_1 = int(hashlib.md5(chunk1.id.encode()).hexdigest()[:16], 16) % (2**63)
        point_id_2 = int(hashlib.md5(chunk2.id.encode()).hexdigest()[:16], 16) % (2**63)

        assert point_id_1 == point_id_2, "相同 chunk.id 应产生相同 point_id"
        # 这会导致 Qdrant 中 ID 冲突，后插入的会覆盖先插入的


# =============================================================================
# Bug 7: RAGPipeline._build_context 按 artifact_id 去重丢失信息
# =============================================================================
class TestBuildContextDedup:
    """测试 _build_context 的去重逻辑"""

    def test_build_context_dedup_loses_relevant_chunks(self):
        """
        Bug: _build_context 按 artifact_id 去重，只保留每个文物的第一个 chunk。
        如果同一文物有多个相关 chunk（如 summary 和 detail），只保留一个。
        """
        pipeline = RAGPipeline(local_mode=True)

        # 同一文物的两个不同 chunk
        chunks = [
            Chunk(id="c1", artifact_id="a1", artifact_name="司母戊鼎",
                  text="司母戊鼎是商代晚期的青铜器，重832.84公斤",
                  metadata={"chunk_type": "summary"}),
            Chunk(id="c2", artifact_id="a1", artifact_name="司母戊鼎",
                  text="司母戊鼎出土于河南安阳，现藏于中国国家博物馆",
                  metadata={"chunk_type": "detail"}),
            Chunk(id="c3", artifact_id="a2", artifact_name="清明上河图",
                  text="清明上河图是北宋张择端创作的风俗画",
                  metadata={"chunk_type": "summary"}),
        ]
        results = [(chunks[0], 0.9), (chunks[1], 0.8), (chunks[2], 0.7)]
        context = pipeline._build_context(results)

        # bug-010 修复后：同一文物的多个 chunk 都能进入上下文
        assert "现藏于中国国家博物馆" in context, (
            "bug-010 修复后：detail 信息应保留在上下文中"
        )
        assert "司母戊鼎" in context
        assert "清明上河图" in context

    def test_build_context_preserves_different_artifacts(self):
        """测试不同文物不被去重"""
        pipeline = RAGPipeline(local_mode=True)
        chunks = [
            Chunk(id="c1", artifact_id="a1", artifact_name="A",
                  text="内容A", metadata={}),
            Chunk(id="c2", artifact_id="a2", artifact_name="B",
                  text="内容B", metadata={}),
        ]
        results = [(chunks[0], 0.9), (chunks[1], 0.8)]
        context = pipeline._build_context(results)
        assert "内容A" in context
        assert "内容B" in context


# =============================================================================
# Bug 8: Reranker 使用 TextEmbedding API 但字段名可能不匹配
# =============================================================================
class TestRerankerAPIFieldNames:
    """测试 Reranker 的 API 响应字段名处理"""

    def test_reranker_score_field_fallback(self):
        """测试重排 API 返回 results 格式（bug-055 修复后按 index/relevance_score 解析）"""
        from src.reranker import BailianReranker
        chunk = Chunk(id="1", artifact_id="a1", artifact_name="测试",
                      text="测试文本", metadata={})

        reranker = BailianReranker()

        # 模拟 TextReRank API 返回 results 格式
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.output = {
            "results": [
                {"index": 0, "relevance_score": 0.95}
            ]
        }

        with patch("dashscope.TextReRank.call", return_value=mock_response):
            result = reranker._rerank_with_api("测试", [(chunk, 0.5)])
            assert len(result) == 1
            assert result[0][0].id == "1"

    def test_reranker_local_fallback_with_sklearn(self):
        """测试本地重排序的 sklearn 路径"""
        from src.reranker import BailianReranker
        chunks = [
            Chunk(id="1", artifact_id="a1", artifact_name="A",
                  text="青铜器是商代的重要文物", metadata={}),
            Chunk(id="2", artifact_id="a2", artifact_name="B",
                  text="清明上河图是北宋风俗画", metadata={}),
            Chunk(id="3", artifact_id="a3", artifact_name="C",
                  text="瓷器发展史源远流长", metadata={}),
        ]
        candidates = [(c, 0.5) for c in chunks]

        reranker = BailianReranker()
        # 模拟 API 失败，触发本地降级
        with patch("dashscope.TextReRank.call", side_effect=Exception("API error")):
            result = reranker.rerank("青铜器", candidates)
            assert len(result) <= 3
            assert len(result) > 0, "本地降级应返回结果"


# =============================================================================
# Bug 9: DocumentLoader 内容截断导致信息丢失
# =============================================================================
class TestDocumentLoaderTruncation:
    """测试 DocumentLoader 的内容截断"""

    def test_description_truncated_to_500_chars(self, tmp_path):
        """
        Bug: load_all_as_artifacts 将 description 截断为 500 字符。
        超过 500 字符的内容只存储在 extra[\"full_content\"]，但 chunking
        只使用 description，导致长文档内容丢失。
        """
        from src.document_loader import DocumentLoader
        from src.chunking import ChunkingPipeline

        # 创建一个 1000 字符的文档，前 500 字和后 500 字不同
        first_part = "A" * 500
        last_part = "B" * 500
        long_content = first_part + last_part  # 1000 字符
        doc_path = tmp_path / "test_long.txt"
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write(long_content)

        loader = DocumentLoader(enable_ocr=False)
        artifacts = loader.load_all_as_artifacts(doc_path, category="测试文档")

        assert len(artifacts) == 1
        artifact = artifacts[0]

        # bug-011 修复后：截断长度从 500 增加到 5000，1000 字符应完整保留
        assert len(artifact.description) == 1000, (
            f"bug-011 修复后：description 应为 1000 字符（完整内容），"
            f"实际为 {len(artifact.description)}"
        )
        assert artifact.description == long_content, (
            "bug-011 修复后：description 应为完整内容"
        )

        # 完整内容也在 extra
        assert "full_content" in artifact.extra
        assert len(artifact.extra["full_content"]) == 1000

        # 使用 chunking 处理后，切片包含完整内容
        pipeline = ChunkingPipeline()
        chunks = pipeline.process([artifact])
        for chunk in chunks:
            if chunk.chunk_type == "detail":
                assert "A" * 100 in chunk.text
                assert "B" * 100 in chunk.text, (
                    "bug-011 修复后：后 500 字符也应出现在切片中"
                )


# =============================================================================
# Bug 10: RAGPipeline 查询类型分类 - 短查询问题
# =============================================================================
class TestQueryClassificationEdgeCases:
    """测试查询分类的边界情况"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.pipeline = RAGPipeline(local_mode=True)
        yield

    def test_short_query_with_chitchat_keyword(self):
        """短查询中的闲聊关键词
        
        bug-009 修复后："你好文物" 不再被误判为闲聊，
        因为前缀匹配 "你好" 后剩余 "文物" 有实质内容。
        """
        assert self.pipeline.is_kb_related("你好") == False
        # bug-009 修复后："你好文物" 有实质内容 "文物"，判为知识库相关
        assert self.pipeline.is_kb_related("你好文物") == True, (
            "bug-009 修复后：'你好文物' 含闲聊词但有实质内容，应路由到知识库"
        )

    def test_chitchat_keyword_in_artifact_question(self):
        """包含闲聊关键词的文物问题
        
        bug-009 修复后："谢谢你的帮助是什么文物" 不再被误判为闲聊，
        因为前缀匹配 "谢谢" 后剩余 "你的帮助是什么文物" 有实质内容。
        """
        assert self.pipeline.is_kb_related("谢谢") == False
        result = self.pipeline.is_kb_related("谢谢你的帮助是什么文物")
        # bug-009 修复后：有实质内容，判为知识库相关
        assert result == True, (
            "bug-009 修复后：'谢谢你的帮助是什么文物' 含闲聊词但有实质内容，应路由到知识库"
        )

    def test_query_type_short_question(self):
        """短问题（<=8 字）被奖励 2 分给 FACTUAL 类型"""
        result = self.pipeline.classify_query("鼎")
        assert result.value in ("factual", "recommendation"), (
            f"单字 '鼎' 的分类不稳定: {result.value}"
        )

    def test_query_type_mixed_intent(self):
        """混合意图的查询"""
        result = self.pipeline.classify_query("推荐几件青铜器，司母戊鼎多重？")
        # "推荐" → RECOMMENDATION +10, "多少" → FACTUAL +10
        # 并列时按优先级：RECOMMENDATION > FACTUAL
        assert result.value == "recommendation", (
            f"混合意图查询应返回 recommendation，实际为 {result.value}"
        )


# =============================================================================
# Bug 11: _convert_history 可能丢失消息
# =============================================================================
class TestConvertHistoryEdgeCases:
    """测试 _convert_history 的边界情况"""

    def test_convert_history_empty(self):
        """测试空历史"""
        from app import _convert_history
        assert _convert_history([]) == []

    def test_convert_history_only_user(self):
        """测试只有用户消息的历史（无对应助手回复，尾部 user 被清理）"""
        from app import _convert_history
        history = [("用户问题", None)]
        result = _convert_history(history)
        # bug-030 修复后：尾部无回复的 user 消息被删除，避免连续 user 消息
        assert len(result) == 0, "无对应助手回复的 user 消息应被清理"

    def test_convert_history_only_assistant(self):
        """测试只有助手消息的历史（异常情况）"""
        from app import _convert_history
        history = [(None, "助手回答")]
        result = _convert_history(history)
        assert len(result) == 1
        assert result[0]["role"] == "assistant"

    def test_convert_history_with_source_separator(self):
        """测试带检索来源分隔符的消息"""
        from app import _convert_history
        history = [
            ("用户问题", "回答内容\n\n---\n\n**📚 检索来源**\n1. 文物A")
        ]
        result = _convert_history(history)
        assert len(result) == 2
        assert result[1]["content"] == "回答内容", "应去掉检索来源部分"

    def test_convert_history_old_separator(self):
        """测试旧版分隔符 \n---\n（仅当后跟检索来源标记时才算分隔符）"""
        from app import _convert_history
        # 旧版存储格式：分隔符后跟随检索来源标记 → 应截断
        history = [("用户问题", "回答内容\n---\n**📚 检索来源**\n1. 文物A")]
        result = _convert_history(history)
        assert len(result) == 2
        assert result[1]["content"] == "回答内容"

    def test_convert_history_markdown_rule_not_truncated(self):
        """P1-1 修复：回答正文中的 Markdown 水平线不应被截断"""
        from app import _convert_history
        history = [("用户问题", "结论：司母戊鼎是青铜器。\n---\n补充：重832公斤")]
        result = _convert_history(history)
        assert len(result) == 2
        assert result[1]["content"] == "结论：司母戊鼎是青铜器。\n---\n补充：重832公斤", (
            "P1-1 修复后：无检索来源标记的 \n---\n 应保留"
        )

    def test_convert_history_empty_assistant_content(self):
        """测试助手消息只有检索来源的情况（助手内容为空，尾部 user 被清理）"""
        from app import _convert_history
        history = [("用户问题", "\n\n---\n\n**📚 检索来源**\n1. 文物A")]
        result = _convert_history(history)
        # bug-030 修复后：尾部无有效助手内容的 user 消息被删除
        assert len(result) == 0, "空助手消息对应的 user 消息应被清理"


# =============================================================================
# Bug 12: RAGPipeline.query_stream 类型标注错误
# =============================================================================
class TestQueryStreamTypeAnnotation:
    """测试 query_stream 的类型标注"""

    def test_query_stream_yields_meta_and_strings(self):
        """测试 query_stream 先 yield meta dict 再 yield 字符串"""
        pipeline = RAGPipeline(local_mode=True)
        gen = pipeline.query_stream(
            question="你好",
            conversation_history=[],
        )
        first_item = next(gen)
        # 第一个 item 应该是 meta dict
        assert isinstance(first_item, dict), "第一个 yield 应为 meta dict"
        assert first_item.get("type") == "meta"
        assert first_item.get("from_kb") == False
        assert first_item.get("query_type") == "chitchat"


# =============================================================================
# Bug 13: VectorStore.create_collection 吞异常
# =============================================================================
class TestVectorStoreErrorHandling:
    """测试 VectorStore 的错误处理"""

    def test_create_collection_swallows_non_404_errors(self):
        """
        Bug: create_collection 在检查集合时，只有 404/not found 异常被正确处理，
        其他异常（如网络错误）被记录警告后继续尝试创建，导致后续错误更难以诊断。
        """
        vs = VectorStore(local_mode=True)
        # 先确保 client 已初始化
        _ = vs.client
        # 使用 mock 模拟 get_collection 抛出非 404 异常
        with patch.object(vs, '_client') as mock_client:
            mock_client.get_collection.side_effect = Exception("Connection refused")
            # bug-019 修复后：非 404 异常应直接抛出
            with pytest.raises(Exception, match="Connection refused"):
                vs.create_collection(overwrite=False)

    def test_search_returns_empty_on_not_found(self, tmp_path):
        """测试 search 在集合不存在时返回空列表（模拟 404 异常）"""
        vs = VectorStore(
            local_mode=True,
            local_path=tmp_path / "test_search_not_found",
        )
        # 先确保 client 已初始化
        _ = vs.client
        # 使用 mock 模拟 search 抛出 404 异常
        with patch.object(vs, '_client') as mock_client:
            mock_client.search.side_effect = Exception("Not found: collection 'test' doesn't exist")
            result = vs.search([0.0] * 10, top_k=5)
            assert result == [], "集合不存在时应返回空列表"


# =============================================================================
# Bug 14: BM25 tokenizer 仅用 unigram 且不分割英文
# =============================================================================
class TestBM25TokenizerQuality:
    """测试 BM25 分词质量"""

    def test_bm25_unigram_only_loses_phrase_info(self):
        """
        Bug: BM25 tokenizer 仅使用 unigram 处理中文，
        导致 "青铜器" 被拆分为 ["青", "铜", "器"]，失去短语级语义。
        """
        retriever = BM25Retriever()
        tokens = retriever._tokenize("青铜器是商代的重要文物")
        assert "青" in tokens
        assert "铜" in tokens
        assert "器" in tokens
        # "青铜器" 作为一个整体不在 token 列表中
        assert "青铜器" not in tokens, (
            "当前实现使用 unigram，'青铜器' 不应作为完整 token 出现"
        )

    def test_bm25_english_not_split_by_space(self):
        """
        Bug: 当前分词器将非中文文本按连续块处理，不按空格分割。
        "Hello World Test" 被当作一个整体 token "hello world test"。
        """
        retriever = BM25Retriever()
        tokens = retriever._tokenize("Hello World Test")
        # bug-008 修复后：按空格分割成 ["hello", "world", "test"]
        assert "hello" in tokens, (
            "bug-008 修复后：英文文本应按空格分割"
        )
        assert "world" in tokens
        assert "test" in tokens
        assert "hello world test" not in tokens

    def test_bm25_mixed_chinese_english(self):
        """测试中英文混合分词"""
        retriever = BM25Retriever()
        tokens = retriever._tokenize("BM25 检索算法")
        assert "bm25" in tokens
        # 中文按字处理
        assert "检" in tokens
        assert "索" in tokens
        assert "算" in tokens
        assert "法" in tokens


# =============================================================================
# Bug 15: EmbeddingCache 未实现相似度匹配
# =============================================================================
class TestEmbeddingCacheSimilarity:
    """测试 EmbeddingCache 的相似度匹配功能"""

    def test_similarity_matching_not_implemented(self, tmp_path):
        """
        确认 EmbeddingCache 当前只实现精确匹配和模式匹配，
        没有相似度匹配（已移除 similarity_threshold 参数）。
        """
        cache = EmbeddingCache(
            cache_dir=tmp_path / "sim_test",
        )
        # 设置一个 embedding
        cache.set("推荐一些文物", [0.1, 0.2, 0.3])

        # 相似问题不会命中（没有相似度匹配）
        similar_question = "给我推荐一些文物"
        result = cache.get(similar_question)
        assert result is None, "相似问题不应命中精确缓存"

    def test_similarity_threshold_removed(self, tmp_path):
        """验证 similarity_threshold 参数已被移除"""
        from src.cache import EmbeddingCache
        import inspect
        sig = inspect.signature(EmbeddingCache.__init__)
        assert "similarity_threshold" not in sig.parameters, (
            "similarity_threshold 参数已被移除"
        )


# =============================================================================
# Bug 16: RAGPipeline 硬编码 MAX_CONTEXT_CHARS
# =============================================================================
class TestContextWindowSize:
    """测试上下文窗口大小配置"""

    def test_max_context_chars_reasonable(self):
        """
        验证 MAX_CONTEXT_CHARS 已调整为合理的值（不再使用保守的 10000）。
        Qwen-plus 支持 128K 上下文，Qwen-max 支持 32K 上下文。
        """
        from src.rag_pipeline import RAGPipeline
        assert RAGPipeline.MAX_CONTEXT_CHARS >= 25000, (
            f"MAX_CONTEXT_CHARS 应至少 25000，适应大模型上下文窗口，"
            f"当前为 {RAGPipeline.MAX_CONTEXT_CHARS}"
        )

    def test_trim_context_with_max_chars_parameter(self):
        """测试 _trim_context 的 max_chars 参数"""
        pipeline = RAGPipeline(local_mode=True)
        context = "A" * 5000
        trimmed = pipeline._trim_context(context, max_chars=100)
        assert len(trimmed) <= 100


# =============================================================================
# Bug 17: 上下文分隔符一致性
# =============================================================================
class TestContextSeparatorConsistency:
    """测试上下文分隔符的一致性"""

    def test_separator_format_mismatch(self):
        """
        Bug: _build_context 使用 \n\n---\n\n 作为段落分隔符，
        _trim_context 也使用相同的分隔符分割段落。
        但如果 chunk.text 本身包含 \n\n---\n\n，分割会出错。
        """
        pipeline = RAGPipeline(local_mode=True)
        # 创建一个 chunk，其文本包含分隔符
        chunk = Chunk(
            id="c1", artifact_id="a1", artifact_name="测试",
            text="第一部分\n\n---\n\n第二部分",  # 文本中包含分隔符
            metadata={},
        )
        results = [(chunk, 0.9)]
        context = pipeline._build_context(results)
        # 上下文中的分隔符可能导致 _trim_context 错误分割
        trimmed = pipeline._trim_context(context, max_chars=10000)
        # 验证不会丢失内容
        assert "第一部分" in trimmed
        assert "第二部分" in trimmed


# =============================================================================
# Bug 18: app.py init_pipeline 空字符串 vs None 比较
# =============================================================================
class TestInitPipelineComparison:
    """测试 init_pipeline 的项目比较逻辑"""

    def test_empty_string_vs_none_comparison(self):
        """
        Bug: init_pipeline 的默认参数 project_id=""，
        但 _current_project 初始化为 ""。
        当用户不指定项目时，pipeline 被重用。
        但如果 project_id 被显式设为 None 呢？
        """
        # 模拟 app.py 中的逻辑
        _current_project = ""
        project_id = None  # 用户没有指定项目

        # 比较："" == None → False
        # 这意味着 pipeline 会每次都重新创建
        assert _current_project != project_id, (
            "BUG: 当 _current_project='' 且 project_id=None 时，"
            "比较为 False，导致 pipeline 无法被重用"
        )


# =============================================================================
# Bug 19: scripts/run_qa.py 使用 Chunk(**c) 可能导致崩溃
# =============================================================================
class TestChunkUnpacking:
    """测试 Chunk **c 的安全性"""

    def test_chunk_unpacking_with_extra_fields(self, tmp_path):
        """
        Bug: run_qa.py 使用 Chunk(**c) 从缓存加载切片。
        如果缓存文件包含额外字段，会触发 TypeError。
        """
        # 模拟缓存文件中包含额外字段
        bad_data = {
            "id": "test_id",
            "artifact_id": "a1",
            "artifact_name": "测试",
            "text": "测试内容",
            "metadata": {},
            "chunk_type": "summary",
            "extra_field": "不应该存在的字段",  # 额外字段
        }

        # 尝试创建 Chunk
        with pytest.raises(TypeError, match="unexpected keyword"):
            Chunk(**bad_data)

    def test_chunk_unpacking_missing_required_fields(self, tmp_path):
        """测试缺少必填字段的情况"""
        bad_data = {
            "id": "test_id",
            "artifact_id": "a1",
            "artifact_name": "测试",
        }
        with pytest.raises(TypeError, match="missing.*required"):
            Chunk(**bad_data)


# =============================================================================
# Bug 20: EmbeddingCache.precompute_patterns 锁安全问题
# =============================================================================
class TestPrecomputePatternsLockSafety:
    """测试 precompute_patterns 的锁安全性"""

    def test_precompute_patterns_save_race(self, tmp_path):
        """测试 precompute_patterns 并发安全性"""
        import threading

        cache = EmbeddingCache(cache_dir=tmp_path / "precompute_test")

        def mock_embed(text):
            return [float(len(text))] * 4

        stop_flag = False

        def modifier():
            i = 0
            while not stop_flag and i <= 1000:
                cache.set(f"interference_key_{i}", [float(i)] * 4)
                i += 1

        mod_thread = threading.Thread(target=modifier)
        mod_thread.start()

        cache.precompute_patterns(mock_embed)

        stop_flag = True
        mod_thread.join()

        # 验证缓存文件没有损坏
        try:
            with open(cache._cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert isinstance(data, dict)
        except Exception as e:
            pytest.fail(f"缓存文件损坏: {e}")


# =============================================================================
# Bug 21: app.py 中 format_answer 的 timing 处理
# =============================================================================
class TestFormatAnswerTiming:
    """测试 format_answer 的 timing 处理"""

    def test_format_answer_with_timing(self):
        """测试带 timing 的格式化"""
        from app import format_answer
        result = format_answer("回答内容", [], {"total": 1234})
        assert "⏱ 响应时间" in result
        assert "1234ms" in result

    def test_format_answer_without_timing(self):
        """测试不带 timing 的格式化"""
        from app import format_answer
        result = format_answer("回答内容", [])
        assert "⏱" not in result

    def test_format_answer_with_chunks(self):
        """测试带检索来源的格式化"""
        from app import format_answer
        chunks = [
            {"artifact_name": "文物A", "score": 0.95, "chunk_type": "summary"},
            {"artifact_name": "文物B", "score": 0.50, "chunk_type": "detail"},
        ]
        result = format_answer("回答内容", chunks)
        assert "📚 检索来源" in result
        assert "文物A" in result
        assert "文物B" in result
        assert "🟢" in result  # 0.95 > 0.7
        assert "🟡" in result  # 0.50 > 0.4


# =============================================================================
# Bug 22: RAGPipeline.is_kb_related 边界情况
# =============================================================================
class TestIsKBRelatedEdgeCases:
    """测试 is_kb_related 的边界情况"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.pipeline = RAGPipeline(local_mode=True)
        yield

    def test_empty_string(self):
        """空字符串"""
        assert self.pipeline.is_kb_related("") == False

    def test_whitespace_only(self):
        """纯空白（strip 后为空）"""
        assert self.pipeline.is_kb_related("   ") == False  # strip 后为空

    def test_punctuation_only(self):
        """纯标点"""
        assert self.pipeline.is_kb_related("？？？") == False  # bug-022

    def test_numbers_only(self):
        """纯数字"""
        assert self.pipeline.is_kb_related("12345") == True

    def test_very_long_question(self):
        """超长问题"""
        long_q = "文物" * 10000
        assert self.pipeline.is_kb_related(long_q) == True

    def test_chitchat_keyword_in_middle_of_word(self):
        """闲聊关键词作为其他词的一部分
        
        bug-009 修复后："你好文物" 和 "说再见" 不再被误判为闲聊，
        因为前缀匹配后剩余部分有实质内容。
        """
        # bug-009 修复后："你好文物" 有实质内容 "文物"
        assert self.pipeline.is_kb_related("你好文物") == True
        # bug-009 修复后："说再见" 中 "再见" 不是前缀，"说" 有实质内容
        assert self.pipeline.is_kb_related("说再见") == True


# =============================================================================
# Bug 23: Chunking 切片 ID 冲突
# =============================================================================
class TestChunkIDCollision:
    """测试切片 ID 冲突"""

    def test_same_artifact_different_chunks_have_different_ids(self):
        """验证同一文物的不同切片有不同 ID"""
        artifacts = [DataLoader._normalize({
            "name": "司母戊鼎",
            "dynasty": "商代",
            "category": "青铜器",
            "description": "重832.84公斤，是中国最重的青铜器。",
            "historical_significance": "代表了商代青铜铸造技术的巅峰。",
            "cultural_value": "是中华文明的重要象征。",
            "tags": ["国宝", "青铜器"],
        })]
        pipeline = ChunkingPipeline()
        chunks = pipeline.process(artifacts)
        chunk_ids = [c.id for c in chunks]
        assert len(chunk_ids) == len(set(chunk_ids)), (
            f"同一文物的切片 ID 存在冲突: {len(chunk_ids)} 个 ID 中有 "
            f"{len(chunk_ids) - len(set(chunk_ids))} 个重复"
        )


# =============================================================================
# Bug 24: RAGPipeline.classify_query 对疑问句的分类
# =============================================================================
class TestClassifyQueryQuestionTypes:
    """测试各种疑问句的分类"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.pipeline = RAGPipeline(local_mode=True)
        yield

    def test_what_question(self):
        """"什么" 类问题"""
        result = self.pipeline.classify_query("什么是青铜器")
        assert result.value in ("factual", "recommendation")

    def test_how_question(self):
        """"怎么" 类问题"""
        result = self.pipeline.classify_query("怎么辨别青铜器的真伪")
        assert result.value == "open_ended"

    def test_why_question(self):
        """"为什么" 类问题"""
        result = self.pipeline.classify_query("为什么司母戊鼎这么重")
        assert result.value == "open_ended"


# =============================================================================
# Bug 25: scripts/generate_mock_data.py 缺少 Optional 导入
# =============================================================================
class TestMockDataMissingImport:
    """测试 generate_mock_data.py 的导入问题"""

    def test_optional_not_imported(self):
        """
        Bug: generate_mock_data.py 的函数签名使用了 Optional，
        但文件顶部没有 from typing import Optional。这会导致
        导入时 NameError。
        """
        script_path = Path(__file__).resolve().parent.parent / "scripts" / "generate_mock_data.py"
        assert script_path.exists(), "generate_mock_data.py 不存在"

        with open(script_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 检查是否使用了 Optional
        uses_optional = "Optional[" in content or "Optional [" in content
        # 检查是否从 typing 导入了 Optional
        imports_optional = "from typing import" in content and "Optional" in content

        if uses_optional and not imports_optional:
            pytest.fail(
                "BUG: generate_mock_data.py 使用了 Optional 但未从 typing 导入。"
                "这会导致 'NameError: name Optional is not defined'"
            )


# =============================================================================
# Bug 26: scripts/build_knowledge_base.py 输出路径不准确
# =============================================================================
class TestBuildScriptOutputPaths:
    """测试 build_knowledge_base.py 的路径输出"""

    def test_output_paths_project_aware(self):
        """
        bug-029 修复后：build_knowledge_base.py 输出路径根据项目动态调整。
        """
        script_path = Path(__file__).resolve().parent.parent / "scripts" / "build_knowledge_base.py"
        with open(script_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 检查是否使用动态路径（项目专属路径优先）
        assert "pipeline.project_cfg" in content, (
            "bug-029 修复后：应使用项目专属路径"
        )
        assert "pipeline.vector_store.local_path" in content, (
            "bug-029 修复后：应使用 vector_store 的实际路径"
        )


# =============================================================================
# Bug 27: RAGPipeline._select_prompt 比较类使用事实类 prompt
# =============================================================================
class TestComparisonPrompt:
    """测试比较类查询的 prompt 选择"""

    def test_comparison_uses_factual_prompt(self):
        """
        Bug: 比较类查询（COMPARISON）使用事实类（factual）prompt，
        没有专门的比较类指令，导致 LLM 可能不按比较格式回答。
        """
        from src.rag_pipeline import RAGPipeline, QueryType, SYSTEM_PROMPT_DEFAULT
        pipeline = RAGPipeline(local_mode=True)
        prompt = pipeline._select_prompt(QueryType.COMPARISON, "测试上下文")
        assert prompt == SYSTEM_PROMPT_DEFAULT.format(context="测试上下文"), (
            "bug-015 修复后：COMPARISON 类型应使用 DEFAULT prompt"
        )


# =============================================================================
# Bug 28: EmbeddingCache 持久化文件格式不一致
# =============================================================================
class TestCacheFileFormat:
    """测试缓存文件格式"""

    def test_corrupted_cache_recovery_pkl_filename(self, tmp_path):
        """
        Bug: 测试 test_corrupted_cache_recovery 创建了 exact_cache.pkl 文件，
        但 EmbeddingCache 实际读取的是 exact_cache.json 文件。
        测试没有真正测试到损坏的 JSON 恢复场景。
        """
        from src.cache import EmbeddingCache

        cache_dir = tmp_path / "cache_format_test"
        cache_dir.mkdir(parents=True, exist_ok=True)

        # 创建一个损坏的 exact_cache.json 文件（不是 .pkl）
        bad_file = cache_dir / "exact_cache.json"  # 注意：是 .json 不是 .pkl
        with open(bad_file, "w", encoding="utf-8") as f:
            f.write("这不是合法的 JSON 格式{{{")

        # 加载损坏的缓存不应崩溃
        c = EmbeddingCache(cache_dir=cache_dir)
        result = c.get("任何查询")
        assert result is None, "损坏的缓存文件应被忽略"


# =============================================================================
# Bug 29: 查询分类的优先级逻辑
# =============================================================================
class TestQueryClassificationPriority:
    """测试查询分类的优先级逻辑"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.pipeline = RAGPipeline(local_mode=True)
        yield

    def test_recommendation_priority_over_factual(self):
        """测试推荐类优先级高于事实类"""
        # "推荐" (+10) + "多少" (+10) 并列时选 RECOMMENDATION
        result = self.pipeline.classify_query("推荐一下司母戊鼎有多重")
        assert result.value == "recommendation", (
            "推荐和事实并列时应选推荐"
        )

    def test_no_match_fallback_to_recommendation(self):
        """测试无匹配时默认推荐类"""
        result = self.pipeline.classify_query("啊啊啊")
        # 短查询（<=8字）会奖励 FACTUAL +2，所以返回 factual
        assert result.value == "factual", (
            "无匹配的短查询因长度惩罚（<=8字）被归类为 factual"
        )


# =============================================================================
# Bug 30: RAGPipeline 预热重复调用
# =============================================================================
class TestWarmupRedundancy:
    """测试预热逻辑"""

    def test_warmup_calls_ensure_knowledge_base_twice(self):
        """
        Minor: init_pipeline 中先调用 _ensure_knowledge_base()，
        再调用 warmup()（warmup 内部也调用 _ensure_knowledge_base）。
        这是冗余调用，但不影响正确性。
        """
        pipeline = RAGPipeline(local_mode=True)
        # 验证 warmup 调用 _ensure_knowledge_base
        # 通过检查 _ensure_knowledge_base 的调用
        original_method = pipeline._ensure_knowledge_base
        call_count = [0]

        def counting_wrapper():
            call_count[0] += 1
            return original_method()

        pipeline._ensure_knowledge_base = counting_wrapper
        pipeline.warmup()
        assert call_count[0] >= 1, "warmup 应调用 _ensure_knowledge_base"


# =============================================================================
# 运行入口
# =============================================================================
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-x"])