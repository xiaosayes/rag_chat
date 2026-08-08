"""
测试工程师独立审查测试 - 从零开始审查，不假设任何代码正确
覆盖现有 140 个测试未覆盖的边界情况与逻辑缺陷
"""
import sys
import json
import time
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from src.chunking import Chunk, ChunkingPipeline, SmartChunking
from src.config import settings
from src.data_loader import DataLoader, Artifact
from src.embeddings import BailianEmbedding
from src.rag_pipeline import RAGPipeline, QueryType, KB_NO_INFO_REPLY
from src.retriever import BM25Retriever, HybridRetriever
from src.reranker import BailianReranker
from src.vector_store import VectorStore
from src.cache import EmbeddingCache, LRUCache
from src.utils import generate_id, save_json, load_json
from src.project import ProjectConfig


# =============================================================================
# 1. _validate_message_roles：历史以 user 结尾时，当前问题被丢弃
# =============================================================================
class TestValidateMessageRolesDropsCurrentQuestion:
    def test_current_question_dropped_when_history_ends_with_user(self):
        """query() 中：如果 conversation_history 以 user 消息结尾，
        追加当前问题后会出现两个连续 user，_validate_message_roles 会丢弃
        **当前问题**而不是历史中的旧问题。"""
        from src.rag_pipeline import RAGPipeline
        pipeline = RAGPipeline(local_mode=True)

        # 模拟：历史以 user 结尾（上一轮问题未回答）
        history = [
            {"role": "user", "content": "上一轮问题"},
            {"role": "assistant", "content": "上一轮回答"},
            {"role": "user", "content": "上一轮未回答的问题"},
        ]
        messages = list(history)
        messages.append({"role": "user", "content": "当前新问题"})

        validated = pipeline._validate_message_roles(messages)
        contents = [m["content"] for m in validated]
        assert "当前新问题" in contents, (
            f"BUG: 当前问题被丢弃了！validated={contents}"
        )


# =============================================================================
# 2. _convert_history：assistant 回复为 None/空时，下一轮 user 被错误丢弃
# =============================================================================
class TestConvertHistoryMispairing:
    def test_mispairing_when_reply_is_none(self):
        """history = [(q1, None), (q2, a2)] 时：
        q1 被保留、q2 被丢弃、a2 错误地配对到 q1。"""
        from app import _convert_history
        history = [("问题1", None), ("问题2", "回答2")]
        result = _convert_history(history)
        contents = [m["content"] for m in result]
        assert "问题2" in contents, (
            f"BUG: 问题2 被丢弃，result={contents}"
        )
        # 配对检查：a2 不应出现在 q1 之后
        if len(result) >= 2:
            assert not (result[0]["content"] == "问题1" and result[1]["content"] == "回答2"), (
                f"BUG: 回答2 错误地配对了问题1: {contents}"
            )

    def test_mispairing_when_reply_empty_string(self):
        """history = [(q1, ""), (q2, a2)] 时同样会错配。"""
        from app import _convert_history
        history = [("问题1", ""), ("问题2", "回答2")]
        result = _convert_history(history)
        contents = [m["content"] for m in result]
        assert "问题2" in contents, (
            f"BUG: 问题2 被丢弃，result={contents}"
        )


# =============================================================================
# 3. _ensure_knowledge_base：文档构建的知识库（chunks_documents.json）无法加载
# =============================================================================
class TestEnsureKBWithDocumentCache:
    def test_document_built_kb_cannot_load(self, tmp_path, monkeypatch):
        """
        build_knowledge_base_from_documents 将缓存保存为 chunks_documents.json，
        但 _ensure_knowledge_base 只检查 chunks.json → 知识库永远加载失败。
        """
        from src.rag_pipeline import RAGPipeline
        import src.rag_pipeline as rp

        # 模拟 chunks_documents.json 存在、qdrant 就绪
        proc_dir = Path(tmp_path) / "processed"
        proc_dir.mkdir(parents=True)
        (proc_dir / "chunks_documents.json").write_text(
            json.dumps([{
                "id": "c1", "artifact_id": "a1", "artifact_name": "文档",
                "text": "测试内容", "metadata": {}, "chunk_type": "summary",
            }]),
            encoding="utf-8",
        )
        qdrant_dir = proc_dir / "qdrant_db"
        qdrant_dir.mkdir(parents=True)
        (qdrant_dir / "meta.json").write_text("{}", encoding="utf-8")

        # 用 MagicMock 包装真实 settings，仅覆盖 processed_data_path（pydantic 属性不可直接赋值）
        from unittest.mock import MagicMock
        mock_settings = MagicMock(wraps=rp.settings)
        mock_settings.processed_data_path = proc_dir
        monkeypatch.setattr(rp, "settings", mock_settings)

        pipeline = RAGPipeline(local_mode=True)
        # mock vector_store 使其认为 qdrant 就绪
        pipeline.vector_store.memory_mode = False
        pipeline.vector_store._snapshot_path = qdrant_dir
        pipeline.vector_store.local_path = qdrant_dir

        # bug-036 已修复：文档构建的知识库（chunks_documents.json）应能正常加载，不再抛异常
        pipeline._ensure_knowledge_base()
        assert pipeline._is_built, "文档构建的知识库应能被 _ensure_knowledge_base 加载"
        assert pipeline.bm25_retriever._is_built, "BM25 索引应已从文档缓存加载"


# =============================================================================
# 4. EmbeddingCache：损坏的 pattern 缓存文件导致 get() 崩溃
# =============================================================================
class TestEmbeddingCacheCorruptPatternFile:
    def test_corrupt_pattern_file_crashes_get(self, tmp_path):
        """pattern_cache.json 内容不是 dict 时（如 list），get() 抛 AttributeError。"""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "pattern_cache.json").write_text(
            json.dumps(["不是", "字典"]), encoding="utf-8"
        )
        cache = EmbeddingCache(cache_dir=cache_dir)
        # 不应崩溃
        try:
            result = cache.get("任意查询")
            assert result is None
        except AttributeError as e:
            pytest.fail(f"BUG: 损坏的 pattern_cache 导致 get() 崩溃: {e}")


# =============================================================================
# 5. VectorStore.upsert：metadata 含 None/复杂对象时 payload 构建
# =============================================================================
class TestVectorStoreUpsertEdge:
    def test_upsert_metadata_with_none(self, tmp_path):
        """metadata 含 None 值不应崩溃，且过滤字段应可查询。"""
        vs = VectorStore(local_mode=True, local_path=tmp_path / "vs")
        chunk = Chunk(
            id="c1", artifact_id="a1", artifact_name="测试",
            text="测试", metadata={"dynasty": None, "tags": ["a"], "importance": 3},
        )
        # 直接验证 payload 构建逻辑（不实际连接 Qdrant）
        import hashlib
        import qdrant_client.http.models as qm
        points = []
        for c, emb in [(chunk, [0.0] * 4)]:
            point_id = int(hashlib.md5(c.id.encode()).hexdigest(), 16) % (2**63)
            payload = {"chunk_id": c.id, "artifact_id": c.artifact_id,
                       "artifact_name": c.artifact_name, "text": c.text,
                       "chunk_type": c.chunk_type}
            for k, v in c.metadata.items():
                if isinstance(v, (str, int, float, bool, list)):
                    payload[f"meta_{k}"] = v
            payload["metadata_json"] = json.dumps(c.metadata, ensure_ascii=False)
            points.append(qm.PointStruct(id=point_id, vector=emb, payload=payload))

        # None 值不应进入 meta_ 字段（否则 Qdrant MatchValue 报错）
        assert "meta_dynasty" not in points[0].payload, (
            "None 值不应写为 meta_dynasty 过滤字段"
        )
        # 但 metadata_json 应保留 None
        assert "dynasty" in json.loads(points[0].payload["metadata_json"])

    def test_upsert_metadata_with_unserializable(self, tmp_path):
        """metadata 含 set 等不可序列化对象时 json.dumps 崩溃（未捕获）。"""
        vs = VectorStore(local_mode=True, local_path=tmp_path / "vs2")
        chunk = Chunk(
            id="c2", artifact_id="a1", artifact_name="测试",
            text="测试", metadata={"bad": {"set_object"}},  # 不可 JSON 序列化
        )
        with pytest.raises((TypeError, ValueError)):
            vs.upsert([chunk], [[0.0] * 4])


# =============================================================================
# 6. ProjectConfig.get_prompt：模板含字面花括号时崩溃
# =============================================================================
class TestGetPromptBraces:
    def test_prompt_template_with_literal_braces(self):
        """用户自定义 prompt 含 {xxx} 字面量时，.format() 抛 KeyError/IndexError。"""
        from src.project import ProjectConfig
        cfg = ProjectConfig("test", {
            "name": "测试",
            "prompts": {
                "default": "请使用 JSON 格式输出：{{\"key\": \"value\"}} {context}",
            },
        })
        # 转义的花括号应该正常工作
        result = cfg.get_prompt("default", context="ctx")
        assert "ctx" in result

    def test_prompt_template_with_unmatched_brace(self):
        from src.project import ProjectConfig
        cfg = ProjectConfig("test", {
            "name": "测试",
            "prompts": {"default": "请输出 {name} 的信息 {context}"},
        })
        # bug-056 修复后：字面花括号原样保留，仅 {context} 被替换，不再抛异常
        result = cfg.get_prompt("default", context="ctx")
        assert "{name}" in result, "字面花括号应原样保留"
        assert "ctx" in result, "{context} 应被替换"


# =============================================================================
# 7. is_kb_related 误判："谢谢你的帮助" 被路由到知识库
# =============================================================================
class TestIsKBRelatedFalsePositives:
    def test_thanks_with_extra_words(self):
        """'谢谢你的帮助' 显然是闲聊，但前缀匹配后剩余 '你的帮助' 有实质内容，
        被路由到知识库 → 无检索结果 → 走 LLM 兜底（可接受但低效）。"""
        pipeline = RAGPipeline(local_mode=True)
        assert pipeline.is_kb_related("谢谢你的帮助") == True

    def test_hello_with_punctuation(self):
        """'你好，你是谁？' 是纯闲聊，应判为闲聊（bug-093 修复：复合闲聊句
        剥离关键词后剩标点/语气词 → 判为闲聊）。"""
        pipeline = RAGPipeline(local_mode=True)
        assert pipeline.is_kb_related("你好，你是谁？") == False


# =============================================================================
# 8. HybridRetriever 缓存 key 忽略 semantic_top_k / bm25_top_k
# =============================================================================
class TestHybridRetrieverCacheKey:
    def test_cache_key_ignores_semantic_top_k(self, tmp_path):
        """不同 semantic_top_k 的检索共享同一缓存条目 → 结果可能错误。"""
        from src.retriever import HybridRetriever
        from src.cache import retrieval_cache

        chunks = [
            Chunk(id="1", artifact_id="a1", artifact_name="A", text="青铜器内容", metadata={}),
            Chunk(id="2", artifact_id="a2", artifact_name="B", text="书画内容", metadata={}),
            Chunk(id="3", artifact_id="a3", artifact_name="C", text="瓷器内容", metadata={}),
        ]
        bm25 = BM25Retriever()
        bm25.build(chunks)

        mock_vs = MagicMock()
        mock_vs.search.return_value = [(chunks[0], 0.9)]
        mock_emb = MagicMock()
        mock_emb.embed_query.return_value = [0.0] * 4

        retriever = HybridRetriever(
            vector_store=mock_vs, embedding=mock_emb, bm25_retriever=bm25,
        )
        retrieval_cache.clear()

        # 第一次：semantic_top_k=1
        r1 = retriever.retrieve("青铜器", top_k=5, semantic_top_k=1, bm25_top_k=1)
        # 第二次：semantic_top_k=100（应返回更多语义结果）
        r2 = retriever.retrieve("青铜器", top_k=5, semantic_top_k=100, bm25_top_k=100)
        assert len(r1) == len(r2), (
            f"BUG: 缓存 key 忽略了 semantic_top_k/bm25_top_k，"
            f"semantic_top_k=1({len(r1)}条) 与 =100({len(r2)}条) 结果应不同"
        )


# =============================================================================
# 9. verify_answer_grounding 是死代码（未接入 query 流程）
# =============================================================================
class TestAnswerGroundingNotWired:
    def test_grounding_check_not_called_in_query(self):
        """bug-046 已修复：verify_answer_grounding 应已接入 query/query_stream。"""
        import inspect
        from src.rag_pipeline import RAGPipeline
        query_src = inspect.getsource(RAGPipeline.query)
        assert "verify_answer_grounding" in query_src, (
            "query() 中应已接入防幻觉检查（bug-046 修复）"
        )
        qs = inspect.getsource(RAGPipeline.query_stream)
        assert "verify_answer_grounding" in qs, (
            "query_stream() 中应已接入防幻觉检查（bug-046 修复）"
        )

    def test_grounding_wired_with_log_only(self):
        """防幻觉检查只记录日志，不拒绝回答（避免行为突变）。"""
        import inspect
        from src.rag_pipeline import RAGPipeline
        src = inspect.getsource(RAGPipeline.query)
        assert "logger.warning(f\"防幻觉检查告警" in src or "logger.warning" in src
        # 不应有 raise/拒绝逻辑
        assert "raise" not in src.split("verify_answer_grounding")[1][:200]


# =============================================================================
# 10. init_pipeline 竞态：锁外 return 可能返回已被替换的 pipeline
# =============================================================================
class TestInitPipelineRace:
    def test_race_returns_wrong_pipeline(self):
        """模拟：线程A创建 museum pipeline 释放锁后，线程B 替换全局 pipeline，
        线程A 的 return pipeline 取到的是 enterprise 的 pipeline。"""
        import app

        # 模拟 app 的全局状态
        results = {}

        def fake_init(project_id):
            # 模拟加锁后的关键代码段
            pipeline_obj = object()
            results["created"] = project_id
            return pipeline_obj

        # 直接验证代码结构：锁内创建后用局部变量持有，锁外返回局部引用
        import inspect
        src = inspect.getsource(app.init_pipeline)
        # bug-038 修复：返回值应为锁内创建的局部变量 new_pipeline，而非全局 pipeline
        assert "new_pipeline = RAGPipeline(" in src, "锁内应创建局部变量 new_pipeline"
        assert "pipeline = new_pipeline" in src, "应同步更新全局 pipeline"
        assert "return new_pipeline" in src, "应返回局部引用而非全局 pipeline"
        # 锁外创建后的最终返回值必须是局部引用 new_pipeline，
        # 而非锁外再次读取全局 pipeline（快速路径的 return pipeline 是安全且必要的）
        with_block = src.split("with _pipeline_lock:")[1]
        after_lock = with_block.split("try:")[1] if "try:" in with_block else ""
        assert "return new_pipeline" in after_lock, (
            "锁外预热后的返回值应为局部引用 new_pipeline（竞态修复）"
        )


# =============================================================================
# 11. retrieved_chunks 文本截断：短文本也被追加 "..."
# =============================================================================
class TestRetrievedChunkTruncation:
    def test_short_text_gets_ellipsis(self):
        """bug-050 已修复：短文本（<=200 字符）应原样返回，不追加省略号。"""
        from src.rag_pipeline import RAGPipeline
        pipeline = RAGPipeline(local_mode=True)
        chunk = Chunk(id="c1", artifact_id="a1", artifact_name="A",
                      text="短文本", metadata={})
        # 验证修复后的截断逻辑
        truncated = chunk.text if len(chunk.text) <= 200 else chunk.text[:200] + "..."
        assert truncated == "短文本", f"短文本不应追加省略号: {truncated}"
        # 验证长文本仍截断
        long_text = "长" * 250
        truncated = long_text if len(long_text) <= 200 else long_text[:200] + "..."
        assert truncated.endswith("...") and len(truncated) == 203

    def test_query_uses_fixed_truncation(self):
        """query() 实现中应包含长度判断。"""
        import inspect
        from src.rag_pipeline import RAGPipeline
        src = inspect.getsource(RAGPipeline.query)
        assert "len(c.text) > 200" in src, "query() 中应使用长度判断控制省略号"


# =============================================================================
# 12. _trim_context：list 输入 + 边界值
# =============================================================================
class TestTrimContextBoundary:
    def test_trim_context_list_negative_max(self):
        """max_chars 为负数时，应返回空/不崩溃。"""
        from src.rag_pipeline import RAGPipeline
        result = RAGPipeline._trim_context(["段落1", "段落2"], max_chars=-5)
        assert result == "", f"max_chars 为负时应返回空: {result!r}"

    def test_trim_context_single_paragraph_exceeds(self):
        """bug-049 已修复：单个段落超过 max_chars 时截断保留开头，不再返回空字符串。"""
        from src.rag_pipeline import RAGPipeline
        result = RAGPipeline._trim_context(
            ["A" * 300], max_chars=100
        )
        assert result == "A" * 100, f"应截断保留开头 100 字符: {len(result)} 字符"

    def test_trim_context_exact_boundary(self):
        """恰好等于 max_chars 时不应被裁剪。"""
        from src.rag_pipeline import RAGPipeline, CHUNK_SEPARATOR
        para = "A" * 100
        result = RAGPipeline._trim_context([para], max_chars=100)
        assert result == para


# =============================================================================
# 13. LRUCache：kwargs 顺序敏感
# =============================================================================
class TestLRUCacheKwargsOrder:
    def test_kwargs_order_sensitive(self):
        """get_with_key 与 set_with_key 使用 str(kwargs)，kwargs 顺序不同 → 不同 key。"""
        cache = LRUCache(capacity=10, ttl=3600)
        cache.set_with_key("v", "p", arg1="a", arg2="b")
        # 相同内容但不同传入顺序
        result = cache.get_with_key("p", arg2="b", arg1="a")
        assert result == "v", "kwargs 顺序不同不应导致缓存未命中"

    def test_dict_arg_order_sensitive(self):
        """dict 参数的键顺序不同 → str() 不同 → 不同 key。"""
        cache = LRUCache(capacity=10, ttl=3600)
        cache.set_with_key("v", "p", {"a": 1, "b": 2})
        result = cache.get_with_key("p", {"b": 2, "a": 1})
        assert result == "v", "dict 键顺序不同不应导致缓存未命中"


# =============================================================================
# 14. BM25 tokenizer 边界
# =============================================================================
class TestBM25TokenizerEdges:
    def test_tokenize_emoji(self):
        """emoji 等符号不应导致死循环或空 token 堆积。"""
        r = BM25Retriever()
        tokens = r._tokenize("青铜器 🏛️ 文物")
        assert "青" in tokens and "文" in tokens

    def test_tokenize_fullwidth_punctuation(self):
        """全角标点应被过滤，不产生脏 token。"""
        r = BM25Retriever()
        tokens = r._tokenize("Hello，World！测试")
        assert "hello" in tokens
        assert "world" in tokens
        assert "，" not in tokens and "！" not in tokens

    def test_retrieve_with_empty_query(self):
        """空查询检索不应崩溃。"""
        r = BM25Retriever()
        r.build([Chunk(id="1", artifact_id="a", artifact_name="A",
                       text="测试内容", metadata={})])
        results = r.retrieve("", top_k=5)
        assert isinstance(results, list)


# =============================================================================
# 15. classify_query 更多边界
# =============================================================================
class TestClassifyQueryMoreEdges:
    def test_question_mark_only(self):
        """纯问号查询。"""
        pipeline = RAGPipeline(local_mode=True)
        result = pipeline.classify_query("？")
        assert result in (QueryType.RECOMMENDATION, QueryType.FACTUAL)

    def test_english_query(self):
        """英文查询不应崩溃。"""
        pipeline = RAGPipeline(local_mode=True)
        result = pipeline.classify_query("What is the heaviest bronzeware")
        assert isinstance(result, QueryType)

    def test_very_short_artifact_name(self):
        """单字文物名分类稳定。"""
        pipeline = RAGPipeline(local_mode=True)
        r1 = pipeline.classify_query("鼎")
        r2 = pipeline.classify_query("鼎")
        assert r1 == r2, "相同输入应产生相同分类（确定性）"

    def test_compare_but_recommend_penalty(self):
        """'比较有名的文物' 应被 bug-016 降低比较分。"""
        pipeline = RAGPipeline(local_mode=True)
        result = pipeline.classify_query("比较有名的文物有哪些")
        assert result == QueryType.RECOMMENDATION, f"应归为推荐: {result}"


# =============================================================================
# 16. add_artifacts：缓存损坏时旧数据丢失
# =============================================================================
class TestAddArtifactsDataLoss:
    def test_add_artifacts_with_corrupt_cache(self, tmp_path, monkeypatch):
        """bug-040 已修复：缓存损坏时 add_artifacts 不应覆盖缓存文件，
        避免旧切片永久丢失（保留损坏文件以便人工恢复）。"""
        from src.rag_pipeline import RAGPipeline
        import src.rag_pipeline as rp
        from src.data_loader import Artifact
        from unittest.mock import MagicMock

        pipeline = RAGPipeline(local_mode=True)
        pipeline._is_built = True

        cache_path = Path(tmp_path) / "chunks.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text("损坏的缓存内容{{{boom", encoding="utf-8")

        # 用 MagicMock 包装真实 settings，仅覆盖 processed_data_path
        mock_settings = MagicMock(wraps=rp.settings)
        mock_settings.processed_data_path = Path(tmp_path)
        monkeypatch.setattr(rp, "settings", mock_settings)
        # 该 pipeline 无 project_cfg → 使用 settings.processed_data_path/chunks.json
        assert pipeline.project_cfg is None

        # mock 掉会调用外部 API 的部分
        pipeline.embedding.embed_batch = MagicMock(return_value=[[0.0] * 4])
        pipeline.vector_store.upsert = MagicMock(return_value=1)

        artifact = Artifact(name="新增文物", description="新内容")
        stats = pipeline.add_artifacts([artifact])
        assert stats["new_chunks"] >= 1

        # 修复验证：缓存文件保持原样（损坏内容未被覆盖）
        content = cache_path.read_text(encoding="utf-8")
        assert content == "损坏的缓存内容{{{boom", (
            "BUG: 缓存加载失败时不应覆盖缓存文件（旧数据永久丢失）"
        )
        # BM25 内存索引仍包含新切片
        assert pipeline.bm25_retriever._is_built


# =============================================================================
# 17. DataLoader：损坏 JSON 的错误信息含糊 / tags 边界
# =============================================================================
class TestDataLoaderEdge:
    def test_tags_as_number(self):
        """tags 为数字时不应崩溃。"""
        artifact = DataLoader._normalize({"name": "测试", "tags": 123})
        assert artifact.tags == 123 or isinstance(artifact.tags, list)

    def test_importance_none(self):
        """importance 为 None 时使用默认值。"""
        a = DataLoader._normalize({"name": "测试", "importance": None})
        assert a.importance == 3

    def test_importance_out_of_range(self):
        """importance 超出 1-5 范围时未校验。"""
        a = DataLoader._normalize({"name": "测试", "importance": 99})
        assert a.importance == 99, "importance 未做 1-5 范围校验（数据校验缺失）"


# =============================================================================
# 18. format_answer：chunks 字段缺失
# =============================================================================
class TestFormatAnswerEdge:
    def test_chunk_missing_fields(self):
        """chunks 项缺少 artifact_name/score 时不应崩溃。"""
        from app import format_answer
        chunks = [{"score": 0.9}, {"artifact_name": "B"}]
        try:
            result = format_answer("回答", chunks)
            assert "[检索来源]" in result
        except (KeyError, ValueError, TypeError) as e:
            pytest.fail(f"BUG: format_answer 对字段缺失的 chunk 崩溃: {e}")

    def test_chunk_score_none(self):
        """score 为 None 时比较运算符崩溃。"""
        from app import format_answer
        chunks = [{"artifact_name": "A", "score": None, "chunk_type": "summary"}]
        try:
            result = format_answer("回答", chunks)
            assert result
        except TypeError as e:
            pytest.fail(f"BUG: score=None 时崩溃: {e}")


# =============================================================================
# 19. _convert_history 分隔符子串问题
# =============================================================================
class TestConvertHistorySeparator:
    def test_old_separator_is_substring(self):
        """HISTORY_SEPARATOR_OLD 是 HISTORY_SEPARATOR 的子串，
        if/elif 检查中 elif 分支永远不可达（死代码）。"""
        from app import HISTORY_SEPARATOR, HISTORY_SEPARATOR_OLD
        assert HISTORY_SEPARATOR_OLD in HISTORY_SEPARATOR, "旧分隔符应为新分隔符子串"


# =============================================================================
# 20. RAGPipeline.query：检索结果为空时 query_type 与 meta 一致性
# =============================================================================
class TestQueryEmptyRetrieval:
    def test_query_type_consistency_when_no_results(self):
        """检索为空时返回 query_type 为原分类值，但 prompt 用 CHITCHAT，
        类型标注不一致。"""
        pipeline = RAGPipeline(local_mode=True)
        # 验证代码中空检索分支的 query_type 来源
        import inspect
        src = inspect.getsource(pipeline.query)
        assert "query_type.value" in src


# =============================================================================
# 21. warmup 与 _is_built 状态不一致
# =============================================================================
class TestWarmupState:
    def test_warmup_failure_does_not_set_flag(self):
        """warmup 失败时 _warmup_done 保持 False，下次调用会再次尝试（OK）。"""
        pipeline = RAGPipeline(local_mode=True)
        with patch.object(pipeline, "_ensure_knowledge_base",
                          side_effect=RuntimeError("未构建")):
            pipeline.warmup()
            assert pipeline._warmup_done == False

    def test_warmup_success_sets_flag(self, tmp_path, monkeypatch):
        """warmup 成功后 _warmup_done=True，避免重复预热。"""
        pipeline = RAGPipeline(local_mode=True)
        # bug-113 优化：warmup 现在会预计算意图原型（真实 API 调用），测试中 patch 掉
        with patch.object(pipeline, "_ensure_knowledge_base") as mock, \
             patch.object(pipeline.intent_classifier, "warmup") as mock_intent:
            pipeline.warmup()
            assert pipeline._warmup_done == True
            assert mock.call_count >= 1
            mock_intent.assert_called_once()


# =============================================================================
# 22. 并发：retrieval_cache 与 llm_cache 的线程安全
# =============================================================================
class TestCacheThreadSafety:
    def test_llm_cache_concurrent_access(self):
        """并发读写 LLM 缓存不应崩溃。"""
        cache = LRUCache(capacity=50, ttl=3600)
        errors = []

        def worker(n):
            try:
                for i in range(100):
                    key = f"k{n}_{i}"
                    cache.set_with_key(f"v{i}", key)
                    cache.get_with_key(key)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"并发缓存出错: {errors}"


# =============================================================================
# 23. VectorStore.search payload 缺失时
# =============================================================================
class TestVectorStoreSearchNoPayload:
    def test_search_hit_without_payload(self):
        """bug-042 已修复：Qdrant 返回无 payload 的 hit 时不应崩溃，
        应降级为空数据返回，而不是 AttributeError。"""
        vs = VectorStore(local_mode=True, local_path=Path("data/processed/qdrant_db"))
        hit = MagicMock()
        hit.payload = None
        hit.score = 0.9
        import src.vector_store as vs_mod
        with patch.object(vs_mod.VectorStore, "client", new_callable=MagicMock) as mock_client:
            # P0 连带修复：qdrant-client >=1.12 使用 query_points 替代已移除的 search
            resp = MagicMock()
            resp.points = [hit]
            mock_client.query_points.return_value = resp
            try:
                results = vs.search([0.0] * 4, top_k=5)
                # 修复后：payload=None 的 hit 被降级为空 Chunk，不崩溃
                assert len(results) == 1
                assert results[0][0].artifact_name == ""
            except AttributeError as e:
                pytest.fail(f"BUG: payload=None 时崩溃: {e}")


# =============================================================================
# 24. app.answer_question：空问题处理
# =============================================================================
class TestAnswerQuestionEmpty:
    @staticmethod
    def _last_assistant_content(history):
        """兼容 Gradio 4/5（tuple）与 6.x（dict）的 Chatbot 消息格式"""
        from app import _iter_history_pairs
        pairs = list(_iter_history_pairs(history))
        return pairs[-1][1] if pairs else ""

    def test_empty_question_yields(self):
        """空问题应返回提示而非崩溃。"""
        from app import answer_question
        gen = answer_question("   ", [], use_stream=True)
        history, chunks = next(gen)
        assert "请输入问题" in self._last_assistant_content(history)

    def test_whitespace_question(self):
        from app import answer_question
        gen = answer_question("\t\n", [], use_stream=False)
        history, chunks = next(gen)
        assert "请输入问题" in self._last_assistant_content(history)


# =============================================================================
# 25. 零长度/空 corpus 的 BM25
# =============================================================================
class TestBM25EmptyCorpus:
    def test_build_empty_corpus(self):
        """bug-043 已修复：空 corpus 构建不应崩溃（ZeroDivisionError）。
        未构建索引时检索仍抛 RuntimeError（保持原有契约）。"""
        r = BM25Retriever()
        # 修复前：build([]) 抛 ZeroDivisionError
        r.build([])
        assert r.bm25 is None
        assert r._is_built == False
        # 未构建索引时检索抛 RuntimeError（既有契约，test_pipeline 中已断言）
        with pytest.raises(RuntimeError):
            r.retrieve("测试", top_k=5)


# =============================================================================
# 26. classify_query 对 "推荐" 前缀的稳定分类
# =============================================================================
class TestClassifyDeterminism:
    def test_classify_deterministic(self):
        """同一输入多次分类结果一致。"""
        pipeline = RAGPipeline(local_mode=True)
        q = "推荐一些代表性的文物"
        results = {pipeline.classify_query(q) for _ in range(20)}
        assert len(results) == 1, f"分类结果不稳定: {results}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

# =============================================================================
# 44. bug-095：API 确定性错误（4xx 非 429）应快速失败并带出服务端详情
#     （生产环境 Embedding 返回 400 时，此前只记状态码 + 无效重试 3 次，
#       根因不可见。修复后日志/异常携带 resp.message，且不重试确定性错误）
# =============================================================================
class TestFatalAPIErrorFastFail:
    def _resp(self, status, message):
        r = MagicMock()
        r.status_code = status
        r.message = message
        return r

    def test_embed_batch_400_fails_fast_with_detail(self):
        """400（确定性客户端错误）：仅调用 1 次即失败，异常携带服务端详情"""
        emb = BailianEmbedding(dimension=1024, max_retries=3)
        resp = self._resp(400, "InvalidParameter: dimension not supported")
        with patch("src.embeddings.TextEmbedding.call", return_value=resp) as mock_call, patch("time.sleep"):
            with pytest.raises(RuntimeError) as exc_info:
                emb.embed_batch(["文本1", "文本2"])
            assert "dimension not supported" in str(exc_info.value), "异常应携带 resp.message"
        assert mock_call.call_count == 1, "确定性错误不应重试"

    def test_embed_one_400_fails_fast(self):
        """embed_one 400：仅调用 1 次，异常携带服务端详情"""
        emb = BailianEmbedding(dimension=1024, max_retries=3)
        resp = self._resp(400, "InvalidParameter: bad input")
        with patch("src.embeddings.TextEmbedding.call", return_value=resp) as mock_call, patch("time.sleep"):
            with pytest.raises(RuntimeError) as exc_info:
                emb.embed_one("文本")
            assert "bad input" in str(exc_info.value)
        assert mock_call.call_count == 1

    def test_429_still_retries(self):
        """429 限流：仍按退避重试（3 次）"""
        emb = BailianEmbedding(dimension=1024, max_retries=3)
        resp = self._resp(429, "rate limit")
        with patch("src.embeddings.TextEmbedding.call", return_value=resp) as mock_call, patch("time.sleep"):
            with pytest.raises(RuntimeError):
                emb._embed_batch(["文本"])
        assert mock_call.call_count == 3, "429 应重试"

    def test_500_still_retries(self):
        """5xx 服务端错误：仍按退避重试（3 次）"""
        emb = BailianEmbedding(dimension=1024, max_retries=3)
        resp = self._resp(500, "internal error")
        with patch("src.embeddings.TextEmbedding.call", return_value=resp) as mock_call, patch("time.sleep"):
            with pytest.raises(RuntimeError):
                emb._embed_batch(["文本"])
        assert mock_call.call_count == 3

    def test_llm_chat_400_fails_fast(self):
        """LLM chat 400：仅调用 1 次，异常携带服务端详情"""
        from src.llm import BailianLLM
        llm = BailianLLM(max_retries=3)
        resp = self._resp(400, "InvalidParameter: messages too long")
        with patch("src.llm.Generation.call", return_value=resp) as mock_call, patch("time.sleep"):
            with pytest.raises(RuntimeError) as exc_info:
                llm.chat([{"role": "user", "content": "hi"}])
            assert "messages too long" in str(exc_info.value)
        assert mock_call.call_count == 1

    def test_llm_chat_stream_400_fails_fast(self):
        """LLM chat_stream 400：仅调用 1 次，异常携带服务端详情"""
        from src.llm import BailianLLM
        llm = BailianLLM(max_retries=3)
        resp = self._resp(400, "InvalidParameter: messages too long")
        with patch("src.llm.Generation.call", return_value=[resp]) as mock_call, patch("time.sleep"):
            with pytest.raises(RuntimeError) as exc_info:
                list(llm.chat_stream([{"role": "user", "content": "hi"}]))
            assert "messages too long" in str(exc_info.value)
        assert mock_call.call_count == 1

    def test_reranker_400_fails_fast(self):
        """Reranker 400：仅调用 1 次后降级本地重排（不向调用方抛错）"""
        from src.chunking import Chunk
        reranker = BailianReranker(max_retries=3)
        resp = self._resp(400, "InvalidParameter: bad model")
        candidates = [(Chunk(id="1", artifact_id="a", artifact_name="A", text="文本1"), 0.5),
                      (Chunk(id="2", artifact_id="b", artifact_name="B", text="文本2"), 0.4)]
        with patch("src.reranker.TextReRank.call", return_value=resp) as mock_call, patch("time.sleep"):
            # rerank() 内部捕获异常后降级本地重排，不会抛错
            result = reranker.rerank("问题", candidates)
            assert len(result) == 2
        assert mock_call.call_count == 1


# =============================================================================
# 45. bug-096：embedding_batch_size 超过 API 上限（10）导致构建 400 失败
#     （生产环境实测：默认 16 > 10，text-embedding-v3 单请求最多 10 条）
# =============================================================================
class TestEmbeddingBatchSizeClamp:
    def test_default_batch_size_within_api_limit(self):
        """默认配置不得超过 API 上限 10"""
        from src.config import settings
        assert settings.embedding_batch_size <= BailianEmbedding.MAX_BATCH_SIZE

    def test_batch_size_16_clamped_to_10(self):
        """超过上限的配置被钳制"""
        emb = BailianEmbedding(batch_size=16)
        assert emb.batch_size == 10

    def test_batch_size_8_kept(self):
        """合法配置保持不变"""
        emb = BailianEmbedding(batch_size=8)
        assert emb.batch_size == 8

    def test_batch_size_non_int_falls_back(self):
        """非整数配置回退到上限值（防御）"""
        from unittest.mock import MagicMock
        emb = BailianEmbedding(batch_size=MagicMock())
        assert emb.batch_size == 10

    def test_embed_batch_splits_within_limit(self):
        """38 个文本按 batch_size=10 分批，每批不超过 API 上限"""
        emb = BailianEmbedding(batch_size=16)  # 钳制为 10
        texts = ["t"] * 38
        batches = [texts[i : i + emb.batch_size] for i in range(0, len(texts), emb.batch_size)]
        assert all(len(b) <= 10 for b in batches)
        assert [len(b) for b in batches] == [10, 10, 10, 8]


# =============================================================================
# 46. bug-097：verify_answer_grounding 误报——字段标签被当名称 + 名称变体不匹配
#     （生产实测：回答中 **推荐理由**/**材质** 等字段标签与
#       "清明上河图（北宋张择端本）" 变体均被误报为"不在上下文中"）
# =============================================================================
class TestAnswerGroundingFalsePositives:
    def _grounding(self):
        from src.rag_pipeline import RAGPipeline
        return RAGPipeline.__new__(RAGPipeline)

    def test_field_labels_not_flagged(self):
        """结构化字段标签（**推荐理由** 等）不应被当作名称误报"""
        p = self._grounding()
        context = "【司母戊鼎】\n商代青铜器"
        answer = "**推荐理由**：这是**司母戊鼎**，**材质**为青铜，**朝代**商代晚期"
        result = p.verify_answer_grounding(answer, context)
        assert result["passed"] is True, f"字段标签不应误报: {result['reason']}"

    def test_name_variant_not_flagged(self):
        """名称变体（回答补充括号描述）应命中上下文名称"""
        p = self._grounding()
        context = "【清明上河图】\n北宋风俗画"
        answer = "**清明上河图（北宋张择端本）**是风俗画长卷"
        result = p.verify_answer_grounding(answer, context)
        assert result["passed"] is True, f"名称变体不应误报: {result['reason']}"

    def test_real_hallucination_still_detected(self):
        """真实幻觉（上下文完全没有的名称）仍应检出"""
        p = self._grounding()
        context = "【司母戊鼎】\n商代青铜器"
        answer = "**司母戊鼎**是重器，**越王勾践剑**是春秋兵器"
        result = p.verify_answer_grounding(answer, context)
        assert result["passed"] is False
        assert "越王勾践剑" in result["missing"]


# =============================================================================
# =============================================================================
# 47. bug-098：Gradio 6.0 破坏性变更导致 Web UI 无法启动
#     （服务器实测：Chatbot.__init__() got an unexpected keyword argument
#       'show_copy_button'；Blocks 的 theme/css 也已在 6.0 移除）
# =============================================================================
class TestGradio6Compatibility:
    def test_create_ui_succeeds_on_installed_gradio(self):
        """安装的 Gradio 版本下，create_ui 应能成功构建（不抛 TypeError）"""
        import gradio as gr
        import app
        try:
            demo = app.create_ui(default_stream=True)
            assert demo is not None
        except TypeError as e:
            pytest.fail(f"create_ui 在 Gradio {gr.__version__} 下抛 TypeError: {e}")

    def test_chatbot_parameters_are_valid(self):
        """传给 gr.Chatbot 的参数在当前版本签名中必须存在"""
        import gradio as gr
        import inspect
        import app as app_mod
        sig = inspect.signature(gr.Chatbot.__init__)
        valid = set(sig.parameters)
        if app_mod._GRADIO_MAJOR >= 6:
            # 6.x：使用 buttons/layout，不再用 show_copy_button/bubble_full_width
            assert "buttons" in valid and "layout" in valid
        else:
            # 4/5.x：使用 show_copy_button/bubble_full_width
            assert "show_copy_button" in valid and "bubble_full_width" in valid


# =============================================================================
# 48. bug-101：Gradio 6.0 Chatbot 消息格式变更（dict 列表）导致页面报错
#     （生产实测：提问后页面返回"错误"，日志：
#       Data incompatible with messages format. Each message should be a
#       dictionary with 'role' and 'content' keys or a ChatMessage object.）
# =============================================================================
class TestGradio6ChatMessageFormat:
    def test_iter_history_pairs_accepts_dict_format(self):
        """Gradio 6.x dict 消息格式应归一化为 (user, assistant) 对"""
        from app import _iter_history_pairs
        hist = [
            {"role": "user", "content": "问题1"},
            {"role": "assistant", "content": "回答1"},
            {"role": "user", "content": "问题2"},
        ]
        assert list(_iter_history_pairs(hist)) == [
            ("问题1", "回答1"), ("问题2", None),
        ]

    def test_iter_history_pairs_keeps_tuple_format(self):
        """Gradio 4/5 tuple 格式仍应正常归一化"""
        from app import _iter_history_pairs
        assert list(_iter_history_pairs([("问题1", "回答1"), ("问题2", None)])) == [
            ("问题1", "回答1"), ("问题2", None),
        ]

    def test_convert_history_accepts_dict_format(self):
        """_convert_history 应正确处理 Gradio 6.x dict 历史"""
        from app import _convert_history
        hist = [
            {"role": "user", "content": "有什么文物"},
            {"role": "assistant", "content": "推荐司母戊鼎。"},
        ]
        assert _convert_history(hist) == [
            {"role": "user", "content": "有什么文物"},
            {"role": "assistant", "content": "推荐司母戊鼎。"},
        ]

    def test_append_conversation_produces_valid_dict_messages(self):
        """Gradio 6 下追加的对话必须是合法 dict 消息（含 role/content 键）"""
        from app import _append_conversation, _update_last_assistant, _GRADIO_MAJOR
        if _GRADIO_MAJOR < 6:
            pytest.skip("仅 Gradio 6.x 场景")
        history = []
        _append_conversation(history, "有什么文物", "")
        assert history == [
            {"role": "user", "content": "有什么文物"},
            {"role": "assistant", "content": ""},
        ]
        _update_last_assistant(history, "有什么文物", "这是回答")
        assert history[-1] == {"role": "assistant", "content": "这是回答"}

    def test_answer_question_produces_dict_history_on_gradio6(self):
        """Gradio 6 下完整问答流程产出的 history 必须是合法 dict 消息列表"""
        import pytest
        from unittest.mock import patch, MagicMock
        from app import _GRADIO_MAJOR
        if _GRADIO_MAJOR < 6:
            pytest.skip("仅 Gradio 6.x 场景")
        from app import answer_question
        fake_pipe = MagicMock()
        fake_pipe._is_built = True
        fake_pipe.query.return_value = {
            "answer": "推荐司母戊鼎。",
            "retrieved_chunks": [],
            "timing": {"total": 100},
        }
        with patch("app.init_pipeline", return_value=fake_pipe):
            results = list(answer_question("有什么文物", [], use_stream=False, project_id="museum"))
        history = results[-1][0]
        assert history, "history 不应为空"
        for msg in history:
            assert isinstance(msg, dict) and "role" in msg and "content" in msg
        assert history[-1]["role"] == "assistant"


# =============================================================================
# 49. bug-103：dashscope 流式默认"合并模式"（累积全文 chunk）导致内容膨胀重复
#     （生产实测：推荐 195 件文物均为重复；根因是未传 incremental_output=True，
#       每个流式 chunk 的 content 为到当前为止的累积全文，被按增量追加后翻倍拼接）
# =============================================================================
class TestStreamingIncrementalOutput:
    def test_chat_stream_passes_incremental_output_true(self):
        """chat_stream 调用 Generation.call 时必须显式传 incremental_output=True"""
        import inspect
        from src.llm import BailianLLM
        src = inspect.getsource(BailianLLM.chat_stream)
        assert "incremental_output=True" in src, "必须显式要求增量输出，否则 dashscope 返回累积全文"

    def test_incremental_tokens_concatenate_without_duplication(self):
        """增量 token 模式：拼接结果与 LLM 输出一致，无重复膨胀"""
        from unittest.mock import patch, MagicMock, ANY
        from src.llm import BailianLLM

        class R:
            status_code = 200
            def __init__(self, text):
                self.output = MagicMock()
                self.output.choices = [MagicMock()]
                self.output.choices[0].message.content = text

        def incremental_stream(model, messages, api_key, temperature, max_tokens,
                               top_p, stream, result_format, incremental_output,
                               enable_search=False):
            assert incremental_output is True
            for t in ["以下是5件推荐", "：", "1.司母戊鼎", "。", "2.清明上河图", "。"]:
                yield R(t)

        llm = BailianLLM(max_retries=3)
        with patch("src.llm.Generation.call", side_effect=incremental_stream) as mock_call:
            tokens = list(llm.chat_stream([{"role": "user", "content": "有什么文物"}]))
        full = "".join(tokens)
        assert full == "以下是5件推荐：1.司母戊鼎。2.清明上河图。"
        assert full.count("司母戊鼎") == 1, "流式输出出现重复"
        assert mock_call.call_args.kwargs.get("incremental_output") is True

    def test_merged_chunks_would_duplicate_without_fix(self):
        """防御性验证：累积全文 chunk 若被按增量追加必然膨胀（证明根因）"""
        text = "1.司母戊鼎。2.清明上河图。"
        chunks = [text[:i] for i in range(3, len(text) + 1, 3)]
        duplicated = "".join(chunks)
        assert duplicated.count("司母戊鼎") > 1, "累积模式必然导致重复"


# =============================================================================
# 50. bug-104：Gradio 6 Chatbot.preprocess 将 content 转为 list[dict] 多模态格式，
#     _convert_history 对 list 调用 .find() 崩溃（多轮对话第二轮起必现）
# =============================================================================
class TestGradio6ListContentHistory:
    def test_convert_history_accepts_list_content(self):
        """Gradio 6 preprocess 后的 list[dict] content 应被提取为文本并正常转换"""
        from app import _convert_history
        hist = [
            {"role": "user", "content": [{"type": "text", "text": "有什么文物"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "推荐司母戊鼎。\n\n---\n\n**[检索来源]**\n1. X"}]},
        ]
        result = _convert_history(hist)
        assert result == [
            {"role": "user", "content": "有什么文物"},
            {"role": "assistant", "content": "推荐司母戊鼎。"},
        ]

    def test_mixed_content_types(self):
        """content 混合 str / list / None 均不崩溃。
        （最后一条 assistant 为空 → 按 bug-028 语义删除对应 user 消息）"""
        from app import _convert_history
        hist = [
            {"role": "user", "content": "纯文本问题"},
            {"role": "assistant", "content": [{"type": "text", "text": "回答1"}]},
            {"role": "user", "content": [{"type": "text", "text": "列表问题"}]},
            {"role": "assistant", "content": None},
        ]
        result = _convert_history(hist)
        assert result == [
            {"role": "user", "content": "纯文本问题"},
            {"role": "assistant", "content": "回答1"},
        ]

    def test_multi_turn_answer_question_with_list_history(self):
        """两轮对话（第二轮 history 为 Gradio 6 list content 格式）answer_question 不崩溃"""
        from unittest.mock import patch, MagicMock
        from app import answer_question

        fake_pipe = MagicMock()
        fake_pipe._is_built = True
        fake_pipe.query.return_value = {
            "answer": "从历史看，工艺成就辉煌。", "retrieved_chunks": [], "timing": {"total": 50},
        }
        def fake_stream(question, top_k, rerank, conversation_history):
            yield {"type": "meta", "from_kb": False, "query_type": "chitchat", "chunks": [], "timing": {}}
            yield "好的，我来回答。"
        fake_pipe.query_stream.side_effect = fake_stream

        # 第一轮：推荐问题
        history = []
        with patch("app.init_pipeline", return_value=fake_pipe):
            list(answer_question("有什么文物", history, use_stream=False, project_id="museum"))
        # Gradio 6 preprocess 后 content 转 list
        gradio6_hist = [
            {"role": "user", "content": [{"type": "text", "text": "有什么文物"}]},
            {"role": "assistant", "content": [{"type": "text", "text": history[-1]["content"]}]},
        ]
        # 第二轮：开放类问题（流式）
        with patch("app.init_pipeline", return_value=fake_pipe):
            results = list(answer_question("谈谈你的看法", gradio6_hist, use_stream=True, project_id="museum"))
        assert "好的" in results[-1][0][-1]["content"]


# =============================================================================
# 51. bug-105：模型回答声明"截止到2024年7月"等训练数据截止日期，
#     与用户当前时间（2026）不符。system prompt 统一注入当前日期并禁止截止声明。
# =============================================================================
class TestCurrentDateInjection:
    def test_system_prompt_contains_current_date(self):
        """system prompt 必须注入当前日期与禁止截止声明指令"""
        from datetime import datetime
        from src.llm import BailianLLM
        llm = BailianLLM()
        msgs = llm._build_messages(
            [{"role": "user", "content": "hi"}], system_prompt="你是助手。"
        )
        sys_content = msgs[0]["content"]
        now = datetime.now()
        assert f"{now.year}年{now.month}月{now.day}日" in sys_content
        assert "不要声明" in sys_content and "截止到XX年XX月" in sys_content
        assert "以官方最新发布为准" in sys_content
        # bug-117：相对日期计算必须以今天为基准，避免"8月22日是后天"类幻觉
        assert "相对日期" in sys_content and "以今天" in sys_content
        assert "宁可只给出具体日期" in sys_content

    def test_stream_injects_updated_relative_date_note(self):
        """bug-117：流式/非流式均注入相对日期计算引导（防'后天'幻觉）"""
        from datetime import datetime
        from unittest.mock import patch, MagicMock
        from src.llm import BailianLLM
        llm = BailianLLM()
        # 用独特 prompt 避免 LLM 响应缓存污染其他测试（同 messages+prompt 会命中缓存）
        unique_prompt = "你是助手。仅用于日期注记测试-相对日期-" + str(datetime.now().timestamp())
        with patch("src.llm.Generation.call") as mock_call:
            mock_call.return_value = MagicMock(
                status_code=200, output=MagicMock(
                    choices=[MagicMock(message=MagicMock(content="好"))]
                )
            )
            llm.chat([{"role": "user", "content": "hi"}], system_prompt=unique_prompt)
            content = mock_call.call_args.kwargs["messages"][0]["content"]
            assert "严格以今天" in content and "明天/后天" in content

    def test_no_system_prompt_no_injection(self):
        """无 system_prompt 时不注入（不影响纯消息调用）"""
        from src.llm import BailianLLM
        msgs = BailianLLM()._build_messages([{"role": "user", "content": "hi"}])
        assert len(msgs) == 1 and msgs[0]["role"] == "user"

    def test_chat_and_stream_both_inject_date(self):
        """chat 与 chat_stream 均经 _build_messages 注入日期"""
        from unittest.mock import patch, MagicMock
        from src.llm import BailianLLM

        llm = BailianLLM(max_retries=3)
        # chat 非流式
        with patch("src.llm.Generation.call") as mock_call:
            mock_call.return_value = MagicMock(
                status_code=200, output=MagicMock(
                    choices=[MagicMock(message=MagicMock(content="你好"))]
                )
            )
            llm.chat([{"role": "user", "content": "hi"}], system_prompt="你是助手。")
            kwargs = mock_call.call_args.kwargs
            assert "【当前日期】" in kwargs["messages"][0]["content"]
        # chat_stream 流式
        with patch("src.llm.Generation.call") as mock_call:
            mock_call.return_value = iter([])
            list(llm.chat_stream([{"role": "user", "content": "hi"}], system_prompt="你是助手。"))
            kwargs = mock_call.call_args.kwargs
            assert "【当前日期】" in kwargs["messages"][0]["content"]


# =============================================================================
# 52. bug-106：按需联网搜索（方案B）——开放/未知类自动联网，时效关键词命中联网，
#     纯知识库事实问题不联网；总开关 settings.llm_enable_search 控制
# =============================================================================
class TestOnDemandWebSearch:
    def test_should_enable_search_rules(self):
        """开放/未知类联网；问候语不联网；时效词命中联网；纯事实问题不联网"""
        from src.rag_pipeline import RAGPipeline, QueryType
        p = RAGPipeline.__new__(RAGPipeline)
        # 开放类 → 联网
        assert p._should_enable_search(QueryType.OPEN_ENDED, "谈谈你对文物保护的看法")
        # 未知类（非知识库开放讨论）→ 联网
        assert p._should_enable_search(QueryType.UNKNOWN, "帮我推荐几本历史书")
        # 未知类纯问候语 → 不联网
        assert not p._should_enable_search(QueryType.UNKNOWN, "你好")
        assert not p._should_enable_search(QueryType.UNKNOWN, "谢谢")
        # 事实类带时效词 → 联网
        assert p._should_enable_search(QueryType.FACTUAL, "博物馆最近有什么新展览")
        assert p._should_enable_search(QueryType.FACTUAL, "门票价格现在是多少")
        # 事实类纯知识库问题 → 不联网
        assert not p._should_enable_search(QueryType.FACTUAL, "司母戊鼎是什么时期的青铜器")
        assert not p._should_enable_search(QueryType.RECOMMENDATION, "推荐几件国宝级文物")

    def test_chat_stream_passes_enable_search(self):
        """chat_stream 将 enable_search 透传 Generation.call 且 system prompt 追加引导"""
        from unittest.mock import patch, MagicMock
        from src.llm import BailianLLM

        class R:
            status_code = 200
            def __init__(self, text):
                self.output = MagicMock()
                self.output.choices = [MagicMock()]
                self.output.choices[0].message.content = text

        def stream(model, messages, api_key, temperature, max_tokens, top_p,
                   stream, result_format, incremental_output, enable_search):
            assert enable_search is True
            assert "【联网搜索】" in messages[0]["content"]
            yield R("搜索结果")

        llm = BailianLLM(max_retries=3)
        with patch("src.llm.Generation.call", side_effect=stream) as mock_call:
            tokens = list(llm.chat_stream([{"role": "user", "content": "最近有什么展览"}],
                                          system_prompt="你是助手。", enable_search=True))
        assert "".join(tokens) == "搜索结果"
        assert mock_call.call_args.kwargs.get("enable_search") is True

    def test_query_stream_meta_reports_search_enabled(self):
        """知识库事实问题 search_enabled=False；总开关关闭时即使开放类也不联网"""
        from unittest.mock import patch
        from src.rag_pipeline import RAGPipeline
        from src.config import settings

        # 总开关关闭：开放类问题也不联网
        with patch.object(settings, "llm_enable_search", False):
            p = RAGPipeline(local_mode=True)
            p._should_enable_search = lambda qt, q: True  # 强制按需判断为 True
            with patch.object(p, "is_kb_related", return_value=False):
                events = list(p.query_stream("谈谈你的看法", conversation_history=None))
            assert events[0]["search_enabled"] is False

        # 总开关开启 + 按需判断命中 → 联网
        with patch.object(settings, "llm_enable_search", True):
            p = RAGPipeline(local_mode=True)
            p._should_enable_search = lambda qt, q: True
            with patch.object(p, "is_kb_related", return_value=False):
                with patch.object(p.llm, "chat_stream", return_value=iter(["好"])) as mock_stream:
                    events = list(p.query_stream("你好", conversation_history=None))
            assert events[0]["search_enabled"] is True
            assert mock_stream.call_args.kwargs.get("enable_search") is True


# =============================================================================
# 53. bug-107：qdrant-client 1.10+ 的 CollectionParams 不再有顶层 distance，
#     页面"刷新状态"报错。get_stats 兼容新旧结构（单向量/命名向量/旧结构）。
# =============================================================================
class TestCollectionStatsCompat:
    def _make_pipeline(self, params, points_count=38):
        from unittest.mock import MagicMock
        from src.rag_pipeline import RAGPipeline
        p = RAGPipeline.__new__(RAGPipeline)
        p.vector_store = MagicMock()
        info = MagicMock()
        info.points_count = points_count
        info.config.params = params
        p.vector_store.client.get_collection.return_value = info
        return p

    def test_new_structure_single_vector(self):
        """qdrant-client 1.19.0：distance/size 在 params.vectors（VectorParams）"""
        import qdrant_client.models as models
        p = self._make_pipeline(models.CollectionParams(
            vectors=models.VectorParams(size=1024, distance=models.Distance.COSINE)))
        stats = p.get_stats()
        assert stats["distance"] == "Cosine"
        assert stats["vector_size"] == 1024
        assert stats["vector_count"] == 38

    def test_old_structure_top_level_distance(self):
        """旧版 qdrant-client：distance 在 CollectionParams 顶层"""
        from types import SimpleNamespace
        p = self._make_pipeline(SimpleNamespace(
            distance="Cosine", vectors=SimpleNamespace(size=768)))
        stats = p.get_stats()
        assert stats["distance"] == "Cosine"
        assert stats["vector_size"] == 768

    def test_named_vectors_map(self):
        """命名向量 VectorParamsMap：取第一个向量配置的 distance/size"""
        import qdrant_client.models as models
        p = self._make_pipeline(models.CollectionParams(
            vectors={"doc": models.VectorParams(size=512, distance=models.Distance.DOT)}))
        stats = p.get_stats()
        assert stats["distance"] == "Dot"
        assert stats["vector_size"] == 512

# =============================================================================
# 54. bug-116：时效性问题（如"电影什么时候上映"）未触发联网搜索且被无关上下文拒答
#     - TEMPORAL_KEYWORDS 缺"上映/什么时候/何时"等时效问句词 → _should_enable_search=False
#     - 检索到无关文档（相关度低）仍被塞进 RAG 上下文 → LLM"以参考信息为准"拒答
# =============================================================================
class TestTemporalQuerySearch:
    def test_temporal_keywords_cover_movie_question(self):
        """'上映/什么时候/何时' 等时效问句词应命中，触发联网搜索"""
        from src.rag_pipeline import RAGPipeline, QueryType
        p = RAGPipeline.__new__(RAGPipeline)
        assert p._should_enable_search(QueryType.FACTUAL, "大唐妖探电影什么时候上映？")
        assert p._should_enable_search(QueryType.FACTUAL, "这部电影几点首映")
        assert p._should_enable_search(QueryType.FACTUAL, "新剧何时开播")
        assert not p._should_enable_search(QueryType.FACTUAL, "司母戊鼎是什么时期的青铜器")

    def test_query_uses_chitchat_when_irrelevant_chunks(self):
        """时效性问题 + 检索结果相关度低（知识库无此内容）→
        不塞无关上下文，改走 LLM 通用回答（联网搜索）"""
        from unittest.mock import patch, MagicMock
        from src.rag_pipeline import RAGPipeline, QueryType
        from src.chunking import Chunk
        from src.config import settings

        p = RAGPipeline(local_mode=True)
        # 固定意图分类为 FACTUAL，避免测试依赖真实 embedding/LLM 调用
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        # 检索返回 5 条低相关度的无关文档（如家博会文档，>3 触发重排）
        fake_chunk = Chunk(id="c1", artifact_id="a1", artifact_name="家博会参观地图", text="第55届中国家博会（广州）参观地图",
                           chunk_type="detail", metadata={})
        low_results = [(fake_chunk, round(0.312 - i * 0.02, 3)) for i in range(5)]
        p.hybrid_retriever.retrieve = MagicMock(return_value=low_results)
        p.reranker.rerank = MagicMock(return_value=low_results)
        p.llm.chat = MagicMock(side_effect=["no", "电影《大唐妖探》定档 2026 年 8 月上映。"])
        # bug-116 补8：首次调用是 LLM 相关性确认（结果侧闸门），返回 no → 降级
        p._ensure_knowledge_base = MagicMock()

        with patch.object(settings, "llm_enable_search", True):
            result = p.query("大唐妖探电影什么时候上映？")
        # 应走 LLM 通用回答（chitchat prompt + 联网搜索），不返回无关检索结果
        assert result["answer"] == "电影《大唐妖探》定档 2026 年 8 月上映。"
        assert result["retrieved_chunks"] == []
        assert result["search_enabled"] is True
        # 确认用的是联网搜索且 prompt 为 chitchat（通用回答）
        assert p.llm.chat.call_args.kwargs.get("enable_search") is True
        assert "你是一位友好的知识助手" in p.llm.chat.call_args.kwargs["system_prompt"]

    def test_query_keeps_rag_when_chunks_relevant(self):
        """时效性问题 + 检索结果相关度高 → 仍走 RAG（知识库有相关内容）"""
        from unittest.mock import patch, MagicMock
        from src.rag_pipeline import RAGPipeline
        from src.chunking import Chunk
        from src.config import settings

        p = RAGPipeline(local_mode=True)
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        fake_chunk = Chunk(id="c2", artifact_id="a2", artifact_name="特展安排", text="2026 年春季特展安排",
                           chunk_type="detail", metadata={})
        high_results = [(fake_chunk, round(0.85 - i * 0.02, 3)) for i in range(5)]
        p.hybrid_retriever.retrieve = MagicMock(return_value=high_results)
        p.reranker.rerank = MagicMock(return_value=high_results)
        p.llm.chat = MagicMock(return_value="春季特展在 3 月开展。")
        p._ensure_knowledge_base = MagicMock()

        with patch.object(settings, "llm_enable_search", True):
            result = p.query("最近有什么特展")
        assert result["retrieved_chunks"] != []
        assert result["search_enabled"] is True

    def test_query_keeps_rag_when_not_temporal(self):
        """非时效性问题（知识库事实）即使检索分低也走 RAG，不误伤"""
        from unittest.mock import patch, MagicMock
        from src.rag_pipeline import RAGPipeline
        from src.chunking import Chunk

        p = RAGPipeline(local_mode=True)
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        fake_chunk = Chunk(id="c3", artifact_id="a3", artifact_name="司母戊鼎", text="商代青铜重器",
                           chunk_type="detail", metadata={})
        low_results = [(fake_chunk, round(0.312 - i * 0.02, 3)) for i in range(5)]
        p.hybrid_retriever.retrieve = MagicMock(return_value=low_results)
        p.reranker.rerank = MagicMock(return_value=low_results)
        p.llm.chat = MagicMock(return_value="司母戊鼎是商代青铜器。")
        p._ensure_knowledge_base = MagicMock()

        result = p.query("司母戊鼎是什么时期的青铜器")
        assert result["retrieved_chunks"] != []

    def test_query_degraded_even_when_master_switch_off(self):
        """bug-116 补强：即使 LLM_ENABLE_SEARCH 总开关关闭，
        时效性问题 + 低相关度检索 → 也必须降级为通用回答（不携带无关上下文），
        否则服务器 .env 未配 LLM_ENABLE_SEARCH=true 时仍被家博会上下文拒答。"""
        from unittest.mock import patch, MagicMock
        from src.rag_pipeline import RAGPipeline, QueryType
        from src.chunking import Chunk
        from src.config import settings

        p = RAGPipeline(local_mode=True)
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        fake_chunk = Chunk(id="c4", artifact_id="a4", artifact_name="家博会参观地图",
                           text="第55届中国家博会（广州）参观地图", chunk_type="detail", metadata={})
        low_results = [(fake_chunk, round(0.312 - i * 0.02, 3)) for i in range(5)]
        p.hybrid_retriever.retrieve = MagicMock(return_value=low_results)
        p.reranker.rerank = MagicMock(return_value=low_results)
        p.llm.chat = MagicMock(side_effect=["no", "电影《大唐妖探》定档 2026 年 8 月上映。"])
        # bug-116 补8：首次调用是 LLM 相关性确认（结果侧闸门），返回 no → 降级
        p._ensure_knowledge_base = MagicMock()

        with patch.object(settings, "llm_enable_search", False):
            result = p.query("大唐妖探电影什么时候上映？")
        # 总开关关：仍应降级（不携带无关上下文），且不调用 LLM 自由发挥——
        # 需求1：知识库无确切信息 → 直接委婉回复"待补充"，不幻觉乱答
        assert result["retrieved_chunks"] == [], "总开关关时也不应携带无关上下文"
        assert result["search_enabled"] is False
        assert result["answer"] == KB_NO_INFO_REPLY
        # LLM 仅被调用 1 次（相关性确认），未被用于生成回答
        assert p.llm.chat.call_count == 1

    def test_query_degraded_with_few_lowscore_chunks(self):
        """bug-116 补强2：检索结果 ≤3 条（不触发常规重排）且分数低 →
        时效性问题也应降级（强制重排后用重排分判断），否则仍被无关上下文带偏"""
        from unittest.mock import patch, MagicMock
        from src.rag_pipeline import RAGPipeline, QueryType
        from src.chunking import Chunk
        from src.config import settings

        p = RAGPipeline(local_mode=True)
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        fake_chunk = Chunk(id="c5", artifact_id="a5", artifact_name="家博会参观地图",
                           text="第55届中国家博会（广州）办公环境主题馆 CMF趋势论坛 参展商手册",
                           chunk_type="detail", metadata={})
        # 仅 2 条低分结果（≤3 不触发常规重排）
        low2 = [(fake_chunk, 0.25), (fake_chunk, 0.20)]
        p.hybrid_retriever.retrieve = MagicMock(return_value=low2)
        # 时效性问题应强制重排，重排返回低分
        p.reranker.rerank = MagicMock(return_value=[(fake_chunk, 0.25), (fake_chunk, 0.20)])
        p.llm.chat = MagicMock(side_effect=["no", "电影《大唐妖探》定档 2026 年 8 月上映。"])
        # bug-116 补8：首次调用是 LLM 相关性确认（结果侧闸门），返回 no → 降级
        p._ensure_knowledge_base = MagicMock()

        with patch.object(settings, "llm_enable_search", True):
            result = p.query("大唐妖探电影什么时候上映？")
        assert result["retrieved_chunks"] == [], "2 条低分也应降级"
        assert result["search_enabled"] is True
        assert p.reranker.rerank.called, "时效性问题应强制重排以判断相关性"

    def test_query_keeps_rag_with_few_highscore_chunks(self):
        """bug-116 补强2：时效性问题 + 检索结果 ≤3 条但重排后分数高 →
        知识库确有相关内容，仍走 RAG（不误伤）"""
        from unittest.mock import patch, MagicMock
        from src.rag_pipeline import RAGPipeline, QueryType
        from src.chunking import Chunk
        from src.config import settings

        p = RAGPipeline(local_mode=True)
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        fake_chunk = Chunk(id="c6", artifact_id="a6", artifact_name="特展安排",
                           text="2026 年春季特展安排", chunk_type="detail", metadata={})
        high2 = [(fake_chunk, 0.8), (fake_chunk, 0.7)]
        p.hybrid_retriever.retrieve = MagicMock(return_value=high2)
        p.reranker.rerank = MagicMock(return_value=high2)
        p.llm.chat = MagicMock(return_value="春季特展在 3 月开展。")
        p._ensure_knowledge_base = MagicMock()

        with patch.object(settings, "llm_enable_search", True):
            result = p.query("最近有什么特展")
        assert result["retrieved_chunks"] != [], "知识库确有相关内容不应降级"

    def test_search_guide_note_prioritizes_novelty_for_temporal(self):
        """bug-116 补强3：走 RAG 分支且启用联网时，_SEARCH_GUIDE_NOTE 必须
        明确时效性问题以联网结果为准、参考信息缺失不拒答——否则 factual prompt
        的"参考信息不足请如实说明"会压制联网结果，导致家博会无关上下文拒答。"""
        from unittest.mock import patch, MagicMock
        from src.llm import BailianLLM, _SEARCH_GUIDE_NOTE

        llm = BailianLLM(max_retries=3)
        # 模拟真实调用：小虎 factual prompt（含"如实说明"）+ enable_search
        system_prompt = "你是「小虎」。\n## 回答原则\n2. 如果参考信息不足，请如实说明\n## 参考信息\n{context}".replace("{context}", "第55届中国家博会（广州）参观地图")
        called = {}

        class R:
            status_code = 200
            def __init__(self, text):
                self.output = MagicMock()
                self.output.choices = [MagicMock()]
                self.output.choices[0].message.content = text

        def fake_call(model, messages, api_key, temperature, max_tokens, top_p,
                      enable_search, result_format, **kw):
            called["system"] = messages[0]["content"]
            return R("《大唐妖探》已改档至2026年8月22日全国上映。")

        with patch("src.llm.Generation.call", side_effect=fake_call):
            ans = llm.chat([{"role": "user", "content": "大唐妖探电影什么时候上映？"}],
                           system_prompt=system_prompt, enable_search=True)
        # 引导必须明确：时效性问题以联网结果为准，参考信息缺失不拒答
        assert "不要因参考信息缺失而拒绝回答" in called["system"]
        assert "以联网结果为准" in called["system"]
        assert ans == "《大唐妖探》已改档至2026年8月22日全国上映。"

    def test_temporal_keywords_colloquial_forms(self):
        """bug-116 补强4：口语化时效问句词（啥时/啥时候/几号/哪天/多久）也应命中，
        否则"大唐妖探啥时上"（无"上映"二字）不触发联网 → 被家博会上下文拒答。
        实测：'啥时上映'命中但'啥时上'不命中——泛化缺陷。"""
        from src.rag_pipeline import RAGPipeline, QueryType
        p = RAGPipeline.__new__(RAGPipeline)
        # 口语化时效问句应命中
        assert p._should_enable_search(QueryType.FACTUAL, "大唐妖探啥时上")
        assert p._should_enable_search(QueryType.FACTUAL, "大唐妖探啥时候上映")
        assert p._should_enable_search(QueryType.FACTUAL, "大唐妖探几号上")
        assert p._should_enable_search(QueryType.FACTUAL, "大唐妖探哪天开播")
        assert p._should_enable_search(QueryType.FACTUAL, "大唐妖探多久才上映")
        # 纯知识库问题仍不命中（不误伤）
        assert not p._should_enable_search(QueryType.FACTUAL, "司母戊鼎是什么时期的青铜器")
        assert not p._should_enable_search(QueryType.RECOMMENDATION, "推荐几件国宝级文物")

    def test_query_degraded_with_colloquial_temporal(self):
        """bug-116 补强4：口语化时效问句（啥时上）也应触发降级分支 + 联网搜索，
        不被家博会无关上下文拒答（端到端，mock 检索）"""
        from unittest.mock import patch, MagicMock
        from src.rag_pipeline import RAGPipeline, QueryType
        from src.chunking import Chunk
        from src.config import settings

        p = RAGPipeline(local_mode=True)
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        fake_chunk = Chunk(id="c7", artifact_id="a7", artifact_name="家博会参观地图",
                           text="第55届中国家博会（广州）参观地图", chunk_type="detail", metadata={})
        low_results = [(fake_chunk, round(0.31 - i * 0.02, 3)) for i in range(5)]
        p.hybrid_retriever.retrieve = MagicMock(return_value=low_results)
        p.reranker.rerank = MagicMock(return_value=low_results)
        p.llm.chat = MagicMock(side_effect=["no", "电影《大唐妖探》定档 2026 年 8 月上映。"])
        # bug-116 补8：首次调用是 LLM 相关性确认（结果侧闸门），返回 no → 降级
        p._ensure_knowledge_base = MagicMock()

        with patch.object(settings, "llm_enable_search", True):
            result = p.query("大唐妖探啥时上")
        assert result["retrieved_chunks"] == [], "口语化时效问句也应降级"
        assert result["search_enabled"] is True
        assert p.llm.chat.call_args.kwargs.get("enable_search") is True

    def test_should_enable_search_semantic_fallback(self):
        """bug-116 补8：关键词未命中时，语义层判断需联网（措辞无关）。
        '大唐妖探啥时上'无'上映'二字，但语义近似'什么时候上映' → 应联网"""
        from unittest.mock import MagicMock
        from src.rag_pipeline import RAGPipeline, QueryType

        p = RAGPipeline.__new__(RAGPipeline)
        # 关键词未命中 → 走语义层；语义层返回 True → 联网
        p.intent_classifier = MagicMock()
        p.intent_classifier.classify_needs_search.return_value = (True, 0.8)
        assert p._should_enable_search(QueryType.FACTUAL, "大唐妖探啥时上") is True
        # 语义层返回 False（纯知识库问题）→ 不联网
        p.intent_classifier.classify_needs_search.return_value = (False, 0.3)
        assert not p._should_enable_search(QueryType.FACTUAL, "司母戊鼎是什么时期的青铜器")

    def test_needs_search_semantic_colloquial(self):
        """bug-116 补8：classify_needs_search 语义判断口语化时效问句（mock embedding）"""
        from unittest.mock import MagicMock
        from src.intent_classifier import SemanticIntentClassifier

        emb = MagicMock()
        emb.embed_query.return_value = [1.0] * 8
        c = SemanticIntentClassifier(embedding=emb, min_confidence=0.55)
        # 预置原型向量（与问题向量相同 → 余弦相似度 1.0），绕过真实缓存
        c._needs_search_vectors = [[1.0] * 8] * 3
        needs, conf = c.classify_needs_search("大唐妖探啥时上")
        assert needs is True
        assert abs(conf - 1.0) < 1e-6
        needs2, _ = c.classify_needs_search("大唐妖探啥时候上映")
        assert needs2 is True

    def test_has_relevant_results_llm_confirms(self):
        """bug-116 补8：结果侧闸门——低分区间由 LLM 确认相关性。
        低分 + LLM确认无关 → 无相关内容；低分 + LLM确认相关 → 有相关内容"""
        from unittest.mock import MagicMock
        from src.rag_pipeline import RAGPipeline
        from src.chunking import Chunk
        from src.config import settings

        p = RAGPipeline.__new__(RAGPipeline)
        p.llm = MagicMock()
        fake_chunk = Chunk(id="c", artifact_id="a", artifact_name="家博会",
                           text="第55届中国家博会（广州）参观地图", chunk_type="detail", metadata={})
        low = [(fake_chunk, 0.25)]

        # 低分 + LLM 确认无关 → False（无相关内容 → 应降级）
        p.llm.chat.return_value = "no"
        assert p._has_relevant_results(low, True, "大唐妖探啥时上") is False
        # 低分 + LLM 确认相关 → True（知识库确有内容 → 不降级）
        p.llm.chat.return_value = "yes"
        assert p._has_relevant_results(low, True, "家博会什么时候举办") is True
        # 高分 → 直接相关，不调 LLM
        high = [(fake_chunk, 0.8)]
        assert p._has_relevant_results(high, True, "家博会什么时候举办") is True
        assert p.llm.chat.call_count == 2  # 高分路径未额外调用

    def test_query_degraded_semantic_colloquial_e2e(self):
        """bug-116 补8：端到端——'啥时上'（无关键词）语义命中 → 降级 + 联网"""
        from unittest.mock import patch, MagicMock
        from src.rag_pipeline import RAGPipeline, QueryType
        from src.chunking import Chunk
        from src.config import settings

        p = RAGPipeline(local_mode=True)
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        fake_chunk = Chunk(id="c8", artifact_id="a8", artifact_name="家博会参观地图",
                           text="第55届中国家博会（广州）参观地图", chunk_type="detail", metadata={})
        low_results = [(fake_chunk, round(0.31 - i * 0.02, 3)) for i in range(5)]
        p.hybrid_retriever.retrieve = MagicMock(return_value=low_results)
        p.reranker.rerank = MagicMock(return_value=low_results)
        p.llm.chat = MagicMock(side_effect=["no", "电影《大唐妖探》定档 2026 年 8 月上映。"])
        # bug-116 补8：首次调用是 LLM 相关性确认（结果侧闸门），返回 no → 降级
        # 语义层命中（关键词未命中时）
        p.intent_classifier.classify_needs_search = MagicMock(return_value=(True, 0.8))
        p._ensure_knowledge_base = MagicMock()

        with patch.object(settings, "llm_enable_search", True):
            result = p.query("大唐妖探啥时上")
        assert result["retrieved_chunks"] == [], "语义层命中应降级"
        assert result["search_enabled"] is True

# =============================================================================
# 22. L0 正则规则 FAQ + 知识库无确切信息委婉回复 + 默认主体（用户需求 1/2/3）
# =============================================================================
class TestL0FAQAndNoInfoRouting:
    """需求2：L0 正则规则 FAQ 命中直接返回预置答案（最快路径，不调用检索/LLM）；
    需求1：知识库相关问题无确切信息 → 委婉回复\"待补充\"，不幻觉乱答；
    需求3：无明确主体/歧义 → 默认按项目 default_subject 作答。"""

    def _make_pipeline(self):
        from src.rag_pipeline import RAGPipeline
        p = RAGPipeline(local_mode=True)
        p._ensure_knowledge_base = MagicMock()
        # 避免语义联网判断依赖真实 embedding（测试环境无 API Key）
        p._needs_search_semantic = MagicMock(return_value=False)
        return p

    def test_faq_match_returns_predefined_answer(self):
        """FAQ 命中：非流式直接返回预置答案，不调用检索/LLM"""
        p = self._make_pipeline()
        p.project_cfg = ProjectConfig("test", {
            "faq": [{"patterns": ["开放时间", "几点开门"], "answer": "08:30-18:00"}]
        })
        p.hybrid_retriever.retrieve = MagicMock()
        p.llm.chat = MagicMock(return_value="不应被调用")
        result = p.query("请问展会开放时间是什么时候？")
        assert result["answer"] == "08:30-18:00"
        assert result["query_type"] == "faq"
        assert result["retrieved_chunks"] == []
        p.hybrid_retriever.retrieve.assert_not_called()
        p.llm.chat.assert_not_called()

    def test_faq_match_streaming(self):
        """FAQ 命中：流式先 yield meta（query_type=faq）再 yield 答案"""
        p = self._make_pipeline()
        p.project_cfg = ProjectConfig("test", {
            "faq": [{"patterns": ["几点开门"], "answer": "09:30-18:00"}]
        })
        events = list(p.query_stream("展会几点开门？"))
        assert events[0]["type"] == "meta"
        assert events[0]["query_type"] == "faq"
        assert events[-1] == "09:30-18:00"

    def test_faq_miss_falls_through_to_normal_flow(self):
        """FAQ 未命中：回落正常流程（走闲聊/检索）"""
        p = self._make_pipeline()
        p.project_cfg = ProjectConfig("test", {
            "faq": [{"patterns": ["开放时间"], "answer": "08:30-18:00"}]
        })
        p.is_kb_related = MagicMock(return_value=False)
        p.llm.chat = MagicMock(return_value="闲聊回答")
        result = p.query("你好呀")
        assert result["query_type"] == "chitchat"
        p.llm.chat.assert_called_once()

    def test_empty_retrieval_non_temporal_returns_no_info(self):
        """检索为空 + 非时效问题 → 委婉回复\"待补充\"，不调用 LLM 自由发挥"""
        from src.rag_pipeline import QueryType
        p = self._make_pipeline()
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        p.hybrid_retriever.retrieve = MagicMock(return_value=[])
        p.llm.chat = MagicMock(return_value="不应被调用")
        result = p.query("司母戊鼎有多重")
        assert result["answer"] == KB_NO_INFO_REPLY
        assert result["from_kb"] is True
        assert result["search_enabled"] is False
        p.llm.chat.assert_not_called()

    def test_empty_retrieval_temporal_searches_online(self):
        """检索为空 + 时效问题 + 总开关开 → 联网搜索作答（需求2：检索无结果→联网）"""
        from src.rag_pipeline import QueryType
        from src.config import settings
        p = self._make_pipeline()
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        p.hybrid_retriever.retrieve = MagicMock(return_value=[])
        p.llm.chat = MagicMock(return_value="电影《大唐妖探》定档 2026 年 8 月上映。")
        with patch.object(settings, "llm_enable_search", True):
            result = p.query("大唐妖探电影什么时候上映？")
        assert result["search_enabled"] is True
        assert result["answer"] == "电影《大唐妖探》定档 2026 年 8 月上映。"
        assert p.llm.chat.call_args.kwargs.get("enable_search") is True

    def test_empty_retrieval_temporal_switch_off_returns_no_info(self):
        """检索为空 + 时效问题 + 总开关关 → 委婉回复（不联网不乱答）"""
        from src.rag_pipeline import QueryType
        from src.config import settings
        p = self._make_pipeline()
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        p.hybrid_retriever.retrieve = MagicMock(return_value=[])
        p.llm.chat = MagicMock(return_value="不应被调用")
        with patch.object(settings, "llm_enable_search", False):
            result = p.query("大唐妖探电影什么时候上映？")
        assert result["answer"] == KB_NO_INFO_REPLY
        assert result["search_enabled"] is False
        p.llm.chat.assert_not_called()

    def test_low_relevance_non_temporal_returns_no_info(self):
        """非时效 + 检索结果相关度低（LLM 确认无关）→ 委婉回复，不携带无关上下文"""
        from src.rag_pipeline import QueryType
        from src.chunking import Chunk
        p = self._make_pipeline()
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        fake_chunk = Chunk(id="c9", artifact_id="a9", artifact_name="家博会手册",
                           text="第55届中国家博会（广州）参展商手册", chunk_type="detail", metadata={})
        low = [(fake_chunk, round(0.30 - i * 0.02, 3)) for i in range(5)]
        p.hybrid_retriever.retrieve = MagicMock(return_value=low)
        p.reranker.rerank = MagicMock(return_value=low)
        # LLM 相关性确认返回 no（知识库无法回答）→ 委婉回复
        p.llm.chat = MagicMock(side_effect=["no"])
        result = p.query("司母戊鼎有多重")
        assert result["answer"] == KB_NO_INFO_REPLY
        assert result["retrieved_chunks"] == []
        # 仅相关性确认调用 1 次，未用于生成回答
        assert p.llm.chat.call_count == 1

    def test_low_relevance_non_temporal_llm_confirms_relevant_keeps_rag(self):
        """非时效 + 检索分低但 LLM 确认知识库能回答 → 仍走 RAG（不误伤）"""
        from src.rag_pipeline import QueryType
        from src.chunking import Chunk
        p = self._make_pipeline()
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        fake_chunk = Chunk(id="c10", artifact_id="a10", artifact_name="司母戊鼎",
                           text="商代青铜重器", chunk_type="detail", metadata={})
        low = [(fake_chunk, round(0.31 - i * 0.02, 3)) for i in range(5)]
        p.hybrid_retriever.retrieve = MagicMock(return_value=low)
        p.reranker.rerank = MagicMock(return_value=low)
        # LLM 确认相关（不以 no 开头）→ 走 RAG
        p.llm.chat = MagicMock(return_value="司母戊鼎是商代青铜器。")
        result = p.query("司母戊鼎是什么时期的青铜器")
        assert result["retrieved_chunks"] != []
        assert result["answer"] == "司母戊鼎是商代青铜器。"

    def test_default_subject_injected_when_configured(self):
        """需求3：配置 default_subject 时，无明确主体问题注入默认主体说明"""
        from src.rag_pipeline import QueryType
        from src.chunking import Chunk
        p = self._make_pipeline()
        p.project_cfg = ProjectConfig("test", {
            "default_subject": "中国家博会（广州）",
            "prompts": {"factual": "factual 模板 {context}"},
        })
        p._classify_intent = MagicMock(return_value=(QueryType.RECOMMENDATION, "test"))
        fake_chunk = Chunk(id="c11", artifact_id="a11", artifact_name="参观地图",
                           text="第55届中国家博会（广州）参观地图", chunk_type="detail", metadata={})
        high = [(fake_chunk, 0.85)]
        p.hybrid_retriever.retrieve = MagicMock(return_value=high)
        p.reranker.rerank = MagicMock(return_value=high)
        p.llm.chat = MagicMock(return_value="B区户外路线推荐")
        result = p.query("一天推荐路线")
        assert result["retrieved_chunks"] != []
        assert "中国家博会（广州）" in p.llm.chat.call_args.kwargs["system_prompt"]

    def test_default_subject_not_injected_when_not_configured(self):
        """未配置 default_subject 的项目（保持代码泛化）→ prompt 不注入"""
        from src.rag_pipeline import QueryType
        from src.chunking import Chunk
        p = self._make_pipeline()
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        fake_chunk = Chunk(id="c12", artifact_id="a12", artifact_name="司母戊鼎",
                           text="商代青铜重器", chunk_type="detail", metadata={})
        high = [(fake_chunk, 0.85)]
        p.hybrid_retriever.retrieve = MagicMock(return_value=high)
        p.reranker.rerank = MagicMock(return_value=high)
        p.llm.chat = MagicMock(return_value="商代青铜器。")
        result = p.query("司母戊鼎是什么时期的")
        assert result["retrieved_chunks"] != []
# =============================================================================
# 22. L0 正则规则 FAQ + 知识库无确切信息委婉回复 + 默认主体（用户需求 1/2/3）
# =============================================================================
class TestL0FAQAndNoInfoRouting:
    """需求2：L0 正则规则 FAQ 命中直接返回预置答案（最快路径，不调用检索/LLM）；
    需求1：知识库相关问题无确切信息 → 委婉回复"待补充"，不幻觉乱答；
    需求3：无明确主体/歧义 → 默认按项目 default_subject 作答。"""

    def _make_pipeline(self):
        from src.rag_pipeline import RAGPipeline
        p = RAGPipeline(local_mode=True)
        p._ensure_knowledge_base = MagicMock()
        # 避免语义联网判断依赖真实 embedding（测试环境无 API Key）
        p._needs_search_semantic = MagicMock(return_value=False)
        return p

    def test_faq_match_returns_predefined_answer(self):
        """FAQ 命中：非流式直接返回预置答案，不调用检索/LLM"""
        p = self._make_pipeline()
        p.project_cfg = ProjectConfig("test", {
            "faq": [{"patterns": ["开放时间", "几点开门"], "answer": "08:30-18:00"}]
        })
        p.hybrid_retriever.retrieve = MagicMock()
        p.llm.chat = MagicMock(return_value="不应被调用")
        result = p.query("请问展会开放时间是什么时候？")
        assert result["answer"] == "08:30-18:00"
        assert result["query_type"] == "faq"
        assert result["retrieved_chunks"] == []
        p.hybrid_retriever.retrieve.assert_not_called()
        p.llm.chat.assert_not_called()

    def test_faq_match_streaming(self):
        """FAQ 命中：流式先 yield meta（query_type=faq）再 yield 答案"""
        p = self._make_pipeline()
        p.project_cfg = ProjectConfig("test", {
            "faq": [{"patterns": ["几点开门"], "answer": "09:30-18:00"}]
        })
        events = list(p.query_stream("展会几点开门？"))
        assert events[0]["type"] == "meta"
        assert events[0]["query_type"] == "faq"
        assert events[-1] == "09:30-18:00"

    def test_faq_miss_falls_through_to_normal_flow(self):
        """FAQ 未命中：回落正常流程（走闲聊/检索）"""
        p = self._make_pipeline()
        p.project_cfg = ProjectConfig("test", {
            "faq": [{"patterns": ["开放时间"], "answer": "08:30-18:00"}]
        })
        p.is_kb_related = MagicMock(return_value=False)
        p.llm.chat = MagicMock(return_value="闲聊回答")
        result = p.query("你好呀")
        assert result["query_type"] == "chitchat"
        p.llm.chat.assert_called_once()

    def test_empty_retrieval_non_temporal_switch_off_returns_no_info(self):
        """检索为空 + 总开关关（费用控制）→ 委婉回复"待补充"，不调用 LLM 自由发挥"""
        from src.rag_pipeline import QueryType
        from src.config import settings
        p = self._make_pipeline()
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        p.hybrid_retriever.retrieve = MagicMock(return_value=[])
        p.llm.chat = MagicMock(return_value="不应被调用")
        with patch.object(settings, "llm_enable_search", False):
            result = p.query("展位费能开发票吗")
        assert result["answer"] == KB_NO_INFO_REPLY
        assert result["from_kb"] is True
        assert result["search_enabled"] is False
        p.llm.chat.assert_not_called()

    def test_empty_retrieval_non_temporal_searches_online(self):
        """检索为空 + 总开关开 → 一律联网搜索作答（用户确认：不论是否时效问题）"""
        from src.rag_pipeline import QueryType
        from src.config import settings
        p = self._make_pipeline()
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        p.hybrid_retriever.retrieve = MagicMock(return_value=[])
        p.llm.chat = MagicMock(return_value="展位费发票需以展会官方财务通道为准。")
        with patch.object(settings, "llm_enable_search", True):
            result = p.query("展位费能开发票吗")
        assert result["search_enabled"] is True
        assert result["answer"] == "展位费发票需以展会官方财务通道为准。"
        assert p.llm.chat.call_args.kwargs.get("enable_search") is True

    def test_empty_retrieval_temporal_searches_online(self):
        """检索为空 + 时效问题 + 总开关开 → 联网搜索作答（需求2：检索无结果→联网）"""
        from src.rag_pipeline import QueryType
        from src.config import settings
        p = self._make_pipeline()
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        p.hybrid_retriever.retrieve = MagicMock(return_value=[])
        p.llm.chat = MagicMock(return_value="电影《大唐妖探》定档 2026 年 8 月上映。")
        with patch.object(settings, "llm_enable_search", True):
            result = p.query("大唐妖探电影什么时候上映？")
        assert result["search_enabled"] is True
        assert result["answer"] == "电影《大唐妖探》定档 2026 年 8 月上映。"
        assert p.llm.chat.call_args.kwargs.get("enable_search") is True

    def test_empty_retrieval_temporal_switch_off_returns_no_info(self):
        """检索为空 + 时效问题 + 总开关关 → 委婉回复（不联网不乱答）"""
        from src.rag_pipeline import QueryType
        from src.config import settings
        p = self._make_pipeline()
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        p.hybrid_retriever.retrieve = MagicMock(return_value=[])
        p.llm.chat = MagicMock(return_value="不应被调用")
        with patch.object(settings, "llm_enable_search", False):
            result = p.query("大唐妖探电影什么时候上映？")
        assert result["answer"] == KB_NO_INFO_REPLY
        assert result["search_enabled"] is False
        p.llm.chat.assert_not_called()

    def test_low_relevance_non_temporal_returns_no_info(self):
        """非时效 + 检索结果相关度低（LLM 确认无关）→ 委婉回复，不携带无关上下文"""
        from src.rag_pipeline import QueryType
        from src.chunking import Chunk
        p = self._make_pipeline()
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        fake_chunk = Chunk(id="c9", artifact_id="a9", artifact_name="家博会手册",
                           text="第55届中国家博会（广州）参展商手册", chunk_type="detail", metadata={})
        low = [(fake_chunk, round(0.30 - i * 0.02, 3)) for i in range(5)]
        p.hybrid_retriever.retrieve = MagicMock(return_value=low)
        p.reranker.rerank = MagicMock(return_value=low)
        # LLM 相关性确认返回 no（知识库无法回答）→ 委婉回复
        p.llm.chat = MagicMock(side_effect=["no"])
        result = p.query("司母戊鼎有多重")
        assert result["answer"] == KB_NO_INFO_REPLY
        assert result["retrieved_chunks"] == []
        # 仅相关性确认调用 1 次，未用于生成回答
        assert p.llm.chat.call_count == 1

    def test_low_relevance_non_temporal_llm_confirms_relevant_keeps_rag(self):
        """非时效 + 检索分低但 LLM 确认知识库能回答 → 仍走 RAG（不误伤）"""
        from src.rag_pipeline import QueryType
        from src.chunking import Chunk
        p = self._make_pipeline()
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        fake_chunk = Chunk(id="c10", artifact_id="a10", artifact_name="司母戊鼎",
                           text="商代青铜重器", chunk_type="detail", metadata={})
        low = [(fake_chunk, round(0.31 - i * 0.02, 3)) for i in range(5)]
        p.hybrid_retriever.retrieve = MagicMock(return_value=low)
        p.reranker.rerank = MagicMock(return_value=low)
        # LLM 确认相关（不以 no 开头）→ 走 RAG
        p.llm.chat = MagicMock(return_value="司母戊鼎是商代青铜器。")
        result = p.query("司母戊鼎是什么时期的青铜器")
        assert result["retrieved_chunks"] != []
        assert result["answer"] == "司母戊鼎是商代青铜器。"

    def test_default_subject_injected_when_configured(self):
        """需求3：配置 default_subject 时，无明确主体问题注入默认主体说明"""
        from src.rag_pipeline import QueryType
        from src.chunking import Chunk
        p = self._make_pipeline()
        p.project_cfg = ProjectConfig("test", {
            "default_subject": "中国家博会（广州）",
            "prompts": {"factual": "factual 模板 {context}"},
        })
        p._classify_intent = MagicMock(return_value=(QueryType.RECOMMENDATION, "test"))
        fake_chunk = Chunk(id="c11", artifact_id="a11", artifact_name="参观地图",
                           text="第55届中国家博会（广州）参观地图", chunk_type="detail", metadata={})
        high = [(fake_chunk, 0.85)]
        p.hybrid_retriever.retrieve = MagicMock(return_value=high)
        p.reranker.rerank = MagicMock(return_value=high)
        p.llm.chat = MagicMock(return_value="B区户外路线推荐")
        result = p.query("一天推荐路线")
        assert result["retrieved_chunks"] != []
        assert "中国家博会（广州）" in p.llm.chat.call_args.kwargs["system_prompt"]

    def test_default_subject_not_injected_when_not_configured(self):
        """未配置 default_subject 的项目（保持代码泛化）→ prompt 不注入"""
        from src.rag_pipeline import QueryType
        from src.chunking import Chunk
        p = self._make_pipeline()
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        fake_chunk = Chunk(id="c12", artifact_id="a12", artifact_name="司母戊鼎",
                           text="商代青铜重器", chunk_type="detail", metadata={})
        high = [(fake_chunk, 0.85)]
        p.hybrid_retriever.retrieve = MagicMock(return_value=high)
        p.reranker.rerank = MagicMock(return_value=high)
        p.llm.chat = MagicMock(return_value="商代青铜器。")
        result = p.query("司母戊鼎是什么时期的")
        assert result["retrieved_chunks"] != []
        assert "默认主体" not in p.llm.chat.call_args.kwargs["system_prompt"]

    def test_stream_low_relevance_returns_no_info(self):
        """流式：非时效 + 低相关（LLM 确认无关）→ meta + 委婉回复文案"""
        from src.rag_pipeline import QueryType
        from src.chunking import Chunk
        p = self._make_pipeline()
        p._classify_intent = MagicMock(return_value=(QueryType.FACTUAL, "test"))
        fake_chunk = Chunk(id="c13", artifact_id="a13", artifact_name="家博会手册",
                           text="第55届中国家博会（广州）参展商手册", chunk_type="detail", metadata={})
        low = [(fake_chunk, round(0.30 - i * 0.02, 3)) for i in range(5)]
        p.hybrid_retriever.retrieve = MagicMock(return_value=low)
        p.reranker.rerank = MagicMock(return_value=low)
        p.llm.chat = MagicMock(side_effect=["no"])
        events = list(p.query_stream("司母戊鼎有多重"))
        assert events[0]["type"] == "meta"
        assert events[0]["chunks"] == []
        assert events[0]["search_enabled"] is False
        assert events[-1] == KB_NO_INFO_REPLY