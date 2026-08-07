"""
Embedding 模块
封装阿里云百炼 Embedding API，同时提供本地备用方案
"""

from __future__ import annotations

import time
from typing import List, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from loguru import logger
from dashscope import TextEmbedding

from src.config import settings
from src.cache import embedding_cache
from src.utils import FatalAPIError


class BailianEmbedding:
    """
    阿里云百炼 Embedding API 封装
    文档：https://help.aliyun.com/zh/dashscope/developer-reference/text-embedding
    """

    # 单请求最大批次数（text-embedding-v3/v4 API 限制：input.contents 不超过 10 条）
    # 实测超限报错：<400> InternalError.Algo.InvalidParameter: Value error,
    #   batch size is invalid, it should not be larger than 10.: input.contents
    # bug-096 修复：默认 batch_size=16 超出该上限，导致构建知识库时全部批次 400 失败
    MAX_BATCH_SIZE = 10

    def __init__(
        self,
        model: str = "text-embedding-v4",
        dimension: int = 1024,
        api_key: Optional[str] = None,
        batch_size: int = 16,
        max_retries: int = 3,
    ):
        self.model = model
        self.dimension = dimension
        self.api_key = api_key or settings.dashscope_api_key
        # bug-096 修复：批大小超过 API 上限（10）时钳制；非整数配置回退到上限值，
        # 避免 .env 中配置的 batch_size 过大/异常导致构建时全部批次 400 失败
        if not isinstance(batch_size, int) or isinstance(batch_size, bool):
            logger.warning(
                f"embedding_batch_size 配置异常（{batch_size!r}），使用默认 {self.MAX_BATCH_SIZE}"
            )
            batch_size = self.MAX_BATCH_SIZE
        elif batch_size > self.MAX_BATCH_SIZE:
            logger.warning(
                f"embedding_batch_size={batch_size} 超过 API 上限 "
                f"{self.MAX_BATCH_SIZE}，已钳制为 {self.MAX_BATCH_SIZE}"
            )
            batch_size = self.MAX_BATCH_SIZE
        self.batch_size = batch_size
        self.max_retries = max_retries

    def embed_one(self, text: str) -> List[float]:
        """对单条文本生成 Embedding"""
        if not text or not text.strip():
            logger.warning("收到空文本，返回零向量")
            return [0.0] * self.dimension
        for attempt in range(self.max_retries):
            try:
                resp = TextEmbedding.call(
                    model=self.model,
                    input=text,
                    api_key=self.api_key,
                    dimension=self.dimension,
                )
                if resp.status_code == 200:
                    embedding = resp.output["embeddings"][0]["embedding"]
                    # P1-2 修复：校验向量维度，避免模型/维度配置不一致时
                    # 向量维度与集合不符，导致后续 upsert/search 报出晦涩错误
                    if len(embedding) != self.dimension:
                        raise ValueError(
                            f"Embedding 维度不匹配: 期望 {self.dimension}，实际 {len(embedding)}"
                        )
                    return embedding
                else:
                    logger.warning(
                        f"Embedding API 返回异常 (attempt {attempt + 1}): "
                        f"{resp.status_code} - {resp.message}"
                    )
                    # P1-1 修复：非 200 响应（如 429 限流）同样退避后重试
                    # bug-095 修复：4xx（除 429 外）为确定性客户端错误，直接失败并带出服务端详情
                    if 400 <= resp.status_code < 500 and resp.status_code != 429:
                        raise FatalAPIError(
                            f"Embedding API 返回 {resp.status_code}: {resp.message}"
                        )
                    if attempt < self.max_retries - 1:
                        time.sleep(1 * (attempt + 1))
            except Exception as e:
                if isinstance(e, FatalAPIError):
                    raise
                logger.warning(
                    f"Embedding 请求失败 (attempt {attempt + 1}): {e}"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(1 * (attempt + 1))  # 指数退避
        raise RuntimeError(f"Embedding 失败（已达最大重试次数）: {text[:50]}...")

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量生成 Embedding，保证返回数量与输入一致"""
        # bug-024 修复：空列表提前返回
        if not texts:
            logger.debug("embed_batch 收到空列表，返回空结果")
            return []

        results: List[List[float]] = [None] * len(texts)
        batches = [
            texts[i : i + self.batch_size]
            for i in range(0, len(texts), self.batch_size)
        ]

        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_batch = {
                executor.submit(self._embed_batch, batch): (i, batch)
                for i, batch in enumerate(batches)
            }
            first_error = None
            for future in as_completed(future_to_batch):
                batch_idx, batch = future_to_batch[future]
                try:
                    batch_embeddings = future.result()
                    start = batch_idx * self.batch_size
                    for j, emb in enumerate(batch_embeddings):
                        results[start + j] = emb
                except Exception as e:
                    logger.error(f"批处理 {batch_idx} 失败: {e}")
                    # 记录第一个错误，但继续等待其他线程完成（取消非强制，已无实际效果）
                    if first_error is None:
                        first_error = e

            if first_error is not None:
                raise RuntimeError(f"Embedding 批处理失败: {first_error}")

        # 检查是否有 None（失败的 embedding）
        none_count = sum(1 for r in results if r is None)
        if none_count > 0:
            raise RuntimeError(
                f"Embedding 失败: {none_count}/{len(texts)} 条向量为空"
            )

        logger.info(
            f"Embedding 完成: {len(texts)} 条 → {len(results)} 条向量 "
            f"(维度: {len(results[0]) if results else 0})"
        )
        return results

    def _embed_batch(self, texts: List[str]) -> List[List[float]]:
        """嵌入一个批次"""
        for attempt in range(self.max_retries):
            try:
                resp = TextEmbedding.call(
                    model=self.model,
                    input=texts,
                    api_key=self.api_key,
                    dimension=self.dimension,
                )
                if resp.status_code == 200:
                    embeddings = resp.output["embeddings"]
                    # 按原始顺序排列
                    ordered = [None] * len(texts)
                    for emb in embeddings:
                        # P1-2 修复：校验每个返回向量的维度，避免维度不一致静默入库
                        if len(emb["embedding"]) != self.dimension:
                            raise ValueError(
                                f"Embedding 维度不匹配: 期望 {self.dimension}，实际 {len(emb['embedding'])}"
                            )
                        ordered[emb["text_index"]] = emb["embedding"]
                    # bug-020 修复：检查 ordered 中是否有 None（API 返回不完整）
                    none_idx = [i for i, v in enumerate(ordered) if v is None]
                    if none_idx:
                        raise RuntimeError(
                            f"Embedding API 返回不完整: 缺失索引 {none_idx}"
                        )
                    return ordered
                else:
                    logger.warning(
                        f"Batch Embedding 返回异常 (attempt {attempt + 1}): "
                        # bug-095 修复：补全服务端错误详情（resp.message），
                        # 此前只记录状态码（如 400），真实原因不可见，难以定位根因
                        f"{resp.status_code} - {resp.message}"
                    )
                    # P1-1 修复：非 200 响应（如 429 限流）同样退避后重试
                    # bug-095 修复：4xx（除 429 外）为确定性客户端错误，直接失败并带出服务端详情
                    if 400 <= resp.status_code < 500 and resp.status_code != 429:
                        raise FatalAPIError(
                            f"Batch Embedding API 返回 {resp.status_code}: {resp.message}"
                        )
                    if attempt < self.max_retries - 1:
                        time.sleep(1 * (attempt + 1))
            except Exception as e:
                # bug-095 修复：确定性客户端错误直接抛出，不进入重试循环
                if isinstance(e, FatalAPIError):
                    raise
                logger.warning(
                    f"Batch Embedding 请求失败 (attempt {attempt + 1}): {e}"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(1 * (attempt + 1))
        raise RuntimeError(f"Batch Embedding 失败")

    def embed_query(self, query: str, use_cache: bool = True) -> List[float]:
        """
        对查询文本生成 Embedding（优先使用缓存）

        优化：
          - 高频问题模式预计算，直接命中缓存
          - 精确问题缓存，相同问题不重复调用 API
        """
        # 尝试从缓存获取
        if use_cache:
            cached = embedding_cache.get(query)
            if cached is not None:
                return cached

        # 缓存未命中，调用 API
        embedding = self.embed_one(query)

        # 写入缓存
        if use_cache:
            embedding_cache.set(query, embedding)

        return embedding

    def precompute_patterns(self):
        """预计算高频问题模式的 Embedding（在构建知识库时调用）"""
        logger.info("开始预计算高频问题 Embedding...")
        embedding_cache.precompute_patterns(self.embed_one)
        logger.info(f"预计算完成: {embedding_cache.stats}")