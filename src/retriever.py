"""
混合检索模块
融合语义检索（向量）和关键词检索（BM25），实现最佳召回效果
"""

from __future__ import annotations

import time
import re
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from loguru import logger
from rank_bm25 import BM25Okapi

from src.config import settings
from src.chunking import Chunk
from src.embeddings import BailianEmbedding
from src.vector_store import VectorStore
from src.cache import retrieval_cache


class BM25Retriever:
    """
    BM25 关键词检索器
    用于稀疏检索，与向量检索互补
    """

    def __init__(self):
        self.corpus: List[str] = []
        self.chunks: List[Chunk] = []
        self.bm25: Optional[BM25Okapi] = None
        self._is_built = False

    def build(self, chunks: List[Chunk]) -> None:
        """构建 BM25 索引"""
        # bug-043 修复：空文档列表直接返回，避免 rank_bm25 内部除零崩溃（ZeroDivisionError）
        if not chunks:
            self.chunks = []
            self.corpus = []
            self.bm25 = None
            self._is_built = False
            logger.warning("BM25 索引构建跳过：文档列表为空")
            return
        self.chunks = chunks
        self.corpus = [chunk.text for chunk in chunks]
        # 中文分词（简单按字/词切分）
        tokenized_corpus = [self._tokenize(text) for text in self.corpus]
        self.bm25 = BM25Okapi(tokenized_corpus)
        self._is_built = True
        logger.info(f"BM25 索引构建完成: {len(chunks)} 条文档")

    def _tokenize(self, text: str) -> List[str]:
        """
        分词
        对于中文，按字处理（unigram）；
        对于非中文，先按空白字符 split，再逐个添加（避免 "Hello World" 成为单一 token）。

        修复说明（bug-008）：
          v1 的非中文处理分支将整段非中文文本作为单一 token，
          导致 "Hello World Test" 变成 ["hello world test"]，
          英文关键词检索完全失效。
          v2 修复：对非中文文本先按空白分割，再逐个添加。
        """
        tokens = []
        i = 0
        while i < len(text):
            ch = text[i]
            if '\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf':
                # 中文字符：按字处理（unigram）
                tokens.append(ch)
                i += 1
            else:
                # 非中文：按空白字符分割，避免 "Hello World" 成为单一 token
                j = i
                while j < len(text) and not (
                    '\u4e00' <= text[j] <= '\u9fff'
                    or '\u3400' <= text[j] <= '\u4dbf'
                ):
                    j += 1
                raw = text[i:j].strip().lower()
                if raw:
                    # bug-018 修复：过滤 CJK 标点（全角逗号、句号、括号等），
                    # 避免 "Hello，World" 因标点不是空白字符而成为单个 token
                    raw = re.sub(r'[　-〿＀-￯]', ' ', raw)
                    # 按空白字符分割，逐词添加
                    for word in raw.split():
                        if word:
                            tokens.append(word)
                i = j
        return tokens

    def retrieve(
        self, query: str, top_k: int = 10
    ) -> List[Tuple[Chunk, float]]:
        """BM25 检索"""
        if not self._is_built:
            raise RuntimeError("BM25 索引未构建，请先调用 build()")

        tokenized_query = self._tokenize(query)
        scores = self.bm25.get_scores(tokenized_query)

        # 获取 Top-K
        top_indices = np.argsort(scores)[::-1][:top_k]
        results = []
        for idx in top_indices:
            if scores[idx] > 0:
                results.append((self.chunks[idx], float(scores[idx])))

        return results


class HybridRetriever:
    """
    混合检索器
    融合语义检索（向量）和关键词检索（BM25），使用 RRF 融合
    """

    def __init__(
        self,
        vector_store: VectorStore,
        embedding: BailianEmbedding,
        bm25_retriever: Optional[BM25Retriever] = None,
        semantic_weight: float = 0.7,
        bm25_weight: float = 0.3,
        rrf_k: int = 60,
    ):
        self.vector_store = vector_store
        self.embedding = embedding
        self.bm25_retriever = bm25_retriever
        self.semantic_weight = semantic_weight
        self.bm25_weight = bm25_weight
        self.rrf_k = rrf_k

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        semantic_top_k: int = 20,
        bm25_top_k: int = 20,
        filter_conditions: Optional[Dict[str, Any]] = None,
        use_cache: bool = True,
    ) -> List[Tuple[Chunk, float]]:
        """
        混合检索（并行执行语义检索 + BM25 关键词检索）

        优化：
          - 语义检索和 BM25 检索并行执行
          - 使用 LRU 缓存避免重复检索
          - 减少 RRF 融合计算量
        """
        logger.debug(f"混合检索: query='{query[:50]}...'")

        # 检查缓存（使用排序后的 filter_conditions 确保键确定性）
        filter_str = str(sorted(filter_conditions.items())) if filter_conditions else "None"
        # bug-047 修复：缓存 key 包含 semantic_top_k / bm25_top_k，
        # 避免不同召回量参数的检索共享同一缓存条目导致结果错误
        # P0-1 修复：缓存 key 加入 collection_name，避免多项目共享 retrieval_cache 时
        # 不同项目（museum/enterprise）的相同问题命中彼此缓存导致结果串数据
        cache_key = f"retrieve:{self.vector_store.collection_name}:{query}:{top_k}:{semantic_top_k}:{bm25_top_k}:{filter_str}"
        if use_cache:
            cached = retrieval_cache.get(cache_key)
            if cached is not None:
                logger.debug(f"检索命中缓存: {query[:30]}...")
                return cached

        t_start = time.time()

        # 并行执行：语义检索 + BM25 关键词检索
        semantic_results = []
        bm25_results = []
        # audit-F4：任一侧检索失败时不写缓存，避免瞬时故障（限流/网络抖动）的
        # 不完整结果被缓存 TTL 固化（故障后 5 分钟内持续返回降级/空结果）
        retrieval_failed = False

        with ThreadPoolExecutor(max_workers=2) as executor:
            # 提交语义检索任务
            future_semantic = executor.submit(
                self._semantic_search, query, semantic_top_k, filter_conditions
            )
            # 提交 BM25 检索任务（传递 filter_conditions 以便后续过滤）
            future_bm25 = executor.submit(
                self._bm25_search, query, bm25_top_k, filter_conditions
            )

            # 等待两个任务完成
            for future in as_completed([future_semantic, future_bm25]):
                if future == future_semantic:
                    try:
                        semantic_results = future.result()
                    except Exception as e:
                        logger.error(f"语义检索失败: {e}")
                        retrieval_failed = True
                elif future == future_bm25:
                    try:
                        bm25_results = future.result()
                    except Exception as e:
                        logger.error(f"BM25 检索失败: {e}")
                        retrieval_failed = True

        # 3. RRF 融合排序
        rrf_scores: Dict[str, float] = {}

        for rank, (chunk, _) in enumerate(semantic_results):
            rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0) + (
                self.semantic_weight / (self.rrf_k + rank + 1)
            )

        for rank, (chunk, _) in enumerate(bm25_results):
            rrf_scores[chunk.id] = rrf_scores.get(chunk.id, 0) + (
                self.bm25_weight / (self.rrf_k + rank + 1)
            )

        # 按 RRF 得分排序
        ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)

        # 构建 chunk 映射
        chunk_map: Dict[str, Chunk] = {}
        for chunk, _ in semantic_results:
            chunk_map[chunk.id] = chunk
        for chunk, _ in bm25_results:
            chunk_map[chunk.id] = chunk

        # 截取 top_k（按 chunk.id 去重，允许同一文物的多个切片进入上下文，
        # 由 _build_context 和 _trim_context 控制总长度，避免信息丢失）
        results = []
        seen_chunk_ids = set()
        for chunk_id, score in ranked:
            if chunk_id in chunk_map and chunk_id not in seen_chunk_ids:
                chunk = chunk_map[chunk_id]
                results.append((chunk, score))
                seen_chunk_ids.add(chunk_id)
                if len(results) >= top_k:
                    break

        elapsed = time.time() - t_start

        logger.info(
            f"混合检索完成: {len(semantic_results)} 语义 + "
            f"{len(bm25_results)} BM25 → {len(results)} 结果 "
            f"({elapsed*1000:.0f}ms)"
        )

        # 写入缓存（audit-F4：检索故障的结果不缓存，下次查询重试）
        if use_cache and not retrieval_failed:
            retrieval_cache.set(cache_key, results)
        elif retrieval_failed:
            logger.debug("检索存在故障，结果不写入缓存")

        return results

    def _semantic_search(
        self, query: str, top_k: int, filter_conditions: Optional[Dict] = None
    ) -> List[Tuple[Chunk, float]]:
        """语义检索子任务（使用缓存加速）"""
        query_vector = self.embedding.embed_query(query, use_cache=True)
        return self.vector_store.search(
            query_vector=query_vector,
            top_k=top_k,
            filter_conditions=filter_conditions,
        )

    def _bm25_search(
        self, query: str, top_k: int, filter_conditions: Optional[Dict] = None
    ) -> List[Tuple[Chunk, float]]:
        """BM25 检索子任务（支持过滤条件，在结果中剔除不匹配的 chunk）"""
        if not self.bm25_retriever:
            return []
        results = self.bm25_retriever.retrieve(query=query, top_k=top_k * 2)
        if filter_conditions:
            filtered = []
            for chunk, score in results:
                match = True
                for key, value in filter_conditions.items():
                    meta_value = chunk.metadata.get(key)
                    if isinstance(value, list):
                        if meta_value not in value and not (isinstance(meta_value, list) and any(v in meta_value for v in value)):
                            match = False
                            break
                    else:
                        # 标量值比较：如果 meta_value 是列表，检查 value 是否在列表中
                        if isinstance(meta_value, list):
                            if value not in meta_value:
                                match = False
                                break
                        elif meta_value != value:
                            match = False
                            break
                if match:
                    filtered.append((chunk, score))
            results = filtered[:top_k]
        return results[:top_k]

    def retrieve_by_dynasty(
        self,
        query: str,
        dynasty: str,
        top_k: int = 5,
    ) -> List[Tuple[Chunk, float]]:
        """按朝代过滤检索"""
        return self.retrieve(
            query=query,
            top_k=top_k,
            filter_conditions={"dynasty": dynasty},
        )

    def retrieve_by_category(
        self,
        query: str,
        category: str,
        top_k: int = 5,
    ) -> List[Tuple[Chunk, float]]:
        """按类别过滤检索"""
        return self.retrieve(
            query=query,
            top_k=top_k,
            filter_conditions={"category": category},
        )