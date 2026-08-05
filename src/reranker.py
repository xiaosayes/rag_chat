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
from dashscope import TextEmbedding

from src.config import settings
from src.chunking import Chunk


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

        if len(candidates) <= 1:
            return candidates

        # 尝试使用百炼 Qwen3-Reranker API
        try:
            return self._rerank_with_api(query, candidates)
        except Exception as e:
            logger.warning(f"Qwen3-Reranker API 调用失败，使用本地重排序: {e}")
            return self._rerank_local(query, candidates)

    def _rerank_with_api(
        self,
        query: str,
        candidates: List[Tuple[Chunk, float]],
    ) -> List[Tuple[Chunk, float]]:
        """
        使用百炼 Qwen3-Reranker API

        Qwen3-Reranker 通过 TextEmbedding.call 调用，传入 model 参数指定模型名。
        支持模型：
          - qwen3-reranker-4b  (推荐)
          - qwen3-reranker-8b  (更准)
        """
        texts = [chunk.text for chunk, _ in candidates]

        for attempt in range(self.max_retries):
            try:
                resp = TextEmbedding.call(
                    model=self.model,
                    input=texts,
                    api_key=self.api_key,
                    query=query,
                )
                if resp.status_code == 200:
                    embeddings = resp.output.get("embeddings", [])
                    if not embeddings:
                        raise ValueError("Qwen3-Reranker API 返回空结果")

                    scores = []
                    for item in embeddings:
                        # Qwen3-Reranker 返回格式: {"text_index": int, "score": float}
                        # 使用 is not None 而非 or，避免 0.0 被错误跳过（or 中 0.0 为 falsy）
                        score = item.get("score")
                        if score is None:
                            score = item.get("relevance_score", 0.0)
                        scores.append(float(score))

                    # 按得分排序（降序）
                    indexed = list(enumerate(scores))
                    indexed.sort(key=lambda x: x[1], reverse=True)

                    reranked = []
                    for idx, score in indexed:
                        chunk, _ = candidates[idx]
                        reranked.append((chunk, float(score)))

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
            except Exception as e:
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