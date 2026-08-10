"""
重排序模块 v2
使用百炼 Qwen3-Reranker API 对检索结果进行精排
（已从 gte-rerank 迁移，该 API 已于 2026-05-30 下线）

API 文档：https://help.aliyun.com/zh/dashscope/developer-reference/task-retry-2
"""

from __future__ import annotations

import time
from typing import List, Optional, Tuple

from loguru import logger
from dashscope import TextReRank

from src.config import settings
from src.chunking import Chunk
from src.utils import FatalAPIError


class BailianReranker:
    """
    阿里云百炼 Rerank 重排序 API 封装 v2

    使用 Qwen3-Reranker 系列模型：
      - qwen3-reranker-4b：推荐，速度与精度平衡
      - qwen3-reranker-8b：更准，稍慢

    如果 API 调用失败，自动降级到本地 TF-IDF 重排序。
    """

    def __init__(
        self,
        model: str = "qwen3-reranker-4b",
        api_key: Optional[str] = None,
        top_k: int = 5,
        max_retries: int = 3,
    ):
        self.model = model
        self.api_key = api_key or settings.dashscope_api_key
        self.top_k = top_k
        self.max_retries = max_retries

    def rerank(
        self,
        query: str,
        candidates: List[Tuple[Chunk, float]],
    ) -> List[Tuple[Chunk, float]]:
        """
        对候选结果进行重排序
        返回重排序后的 Top-K 结果
        """
        if not candidates:
            return []

        # audit-F5 修复：单候选不再原样提前返回。pipeline 对时效性问题强制重排，
        # 依赖 0~1 相关性分（_has_relevant_results 的 RELEVANCE_THRESHOLD=0.45）；
        # 提前返回把 RRF 融合分（~0.008）当相关性分 → 相关性闸门必误判为不相关
        # （LLM 确认关闭时直接拒答）。单候选同样走 API 拿真实相关性分。

        # 尝试使用百炼 Qwen3-Reranker API
        try:
            return self._rerank_with_api(query, candidates)
        except Exception as e:
            logger.warning(f"Qwen3-Reranker API 调用失败，使用本地重排序: {e}")
            try:
                return self._rerank_local(query, candidates)
            except Exception as e2:
                # audit-F17 修复：本地降级自身也可能失败（如全空文本 TF-IDF 空词表），
                # 不能再穿透 rerank() 向 pipeline 抛异常，按原顺序降级返回
                logger.warning(f"本地重排序失败，按原顺序返回: {e2}")
                return candidates[:self.top_k]

    def _rerank_with_api(
        self,
        query: str,
        candidates: List[Tuple[Chunk, float]],
    ) -> List[Tuple[Chunk, float]]:
        """
        使用百炼 Qwen3-Reranker API

        bug-055 修复：改用重排专用接口 dashscope.TextReRank（
        原实现调用 TextEmbedding 并按 embeddings 格式解析，与重排响应契约不符，
        导致线上重排静默降级为本地 TF-IDF）。
        响应格式为 output.results[].index / relevance_score。
        支持模型：
          - qwen3-reranker-4b  (推荐)
          - qwen3-reranker-8b  (更准)
        """
        texts = [chunk.text for chunk, _ in candidates]

        for attempt in range(self.max_retries):
            try:
                resp = TextReRank.call(
                    model=self.model,
                    query=query,
                    documents=texts,
                    api_key=self.api_key,
                )
                if resp.status_code == 200:
                    results = resp.output.get("results", [])
                    if not results:
                        raise ValueError("Qwen3-Reranker API 返回空结果")

                    # Qwen3-Reranker 返回格式: {"index": int, "relevance_score": float}
                    # 按 index 映射回原始 candidates（index 对应传入 documents 的下标）
                    reranked = []
                    for item in results:
                        idx = item.get("index")
                        score = item.get("relevance_score")
                        if idx is None or score is None:
                            continue
                        if 0 <= idx < len(candidates):
                            chunk, _ = candidates[idx]
                            reranked.append((chunk, float(score)))

                    if not reranked:
                        raise ValueError("Qwen3-Reranker API 返回结果无法映射到候选")

                    result = reranked[:self.top_k]
                    logger.info(
                        f"Qwen3-Reranker 重排序完成: {len(candidates)} → {len(result)} 条"
                    )
                    return result
                else:
                    logger.warning(
                        f"Qwen3-Reranker API 异常 (attempt {attempt + 1}): "
                        f"{resp.status_code} - {resp.message}"
                    )
                    # P1-1 修复：非 200 响应（如 429 限流）同样退避后重试
                    # bug-095 修复：4xx（除 429 外）为确定性客户端错误，直接失败并带出服务端详情
                    if 400 <= resp.status_code < 500 and resp.status_code != 429:
                        raise FatalAPIError(
                            f"Qwen3-Reranker API 返回 {resp.status_code}: {resp.message}"
                        )
                    if attempt < self.max_retries - 1:
                        time.sleep(1 * (attempt + 1))
            except Exception as e:
                # bug-095 修复：确定性客户端错误直接抛出，不进入重试循环
                if isinstance(e, FatalAPIError):
                    raise
                logger.warning(
                    f"Qwen3-Reranker 请求失败 (attempt {attempt + 1}): {e}"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(1 * (attempt + 1))

        raise RuntimeError("Qwen3-Reranker API 调用失败")

    def _rerank_local(
        self,
        query: str,
        candidates: List[Tuple[Chunk, float]],
    ) -> List[Tuple[Chunk, float]]:
        """
        本地重排序（基于 Query 和 Chunk 的文本相似度）
        使用简单的 TF-IDF + 余弦相似度作为 fallback
        """
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
        except ImportError:
            logger.warning("scikit-learn 未安装，使用原始顺序返回（降级）")
            return candidates[:self.top_k]

        texts = [chunk.text for chunk, _ in candidates]
        all_texts = [query] + texts

        # audit-F17 修复：全空文本（含 query）时 TF-IDF 空词表抛 ValueError，
        # 提前按原顺序返回，不向调用方抛异常
        if not any(t.strip() for t in all_texts):
            logger.warning("本地重排序输入全为空文本，按原顺序返回")
            return candidates[:self.top_k]

        # TF-IDF 向量化
        vectorizer = TfidfVectorizer(
            analyzer="char",
            ngram_range=(1, 3),
            max_features=5000,
        )
        tfidf_matrix = vectorizer.fit_transform(all_texts)

        # 计算相似度
        query_vector = tfidf_matrix[0:1]
        doc_vectors = tfidf_matrix[1:]
        similarities = cosine_similarity(query_vector, doc_vectors).flatten()

        # 按相似度排序
        indexed = list(enumerate(similarities))
        indexed.sort(key=lambda x: x[1], reverse=True)

        reranked = []
        for idx, score in indexed:
            chunk, _ = candidates[idx]
            reranked.append((chunk, float(score)))

        result = reranked[:self.top_k]
        logger.info(
            f"本地 Rerank 完成: {len(candidates)} → {len(result)} 条"
        )
        return result