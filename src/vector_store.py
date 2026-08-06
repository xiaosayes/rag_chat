"""
向量数据库模块
使用 Qdrant 作为向量数据库，支持本地开发模式
"""

from __future__ import annotations

import json
import hashlib
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models

from src.config import settings
from src.chunking import Chunk


class VectorStore:
    """
    向量数据库封装
    支持 Qdrant 本地/远程模式
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        collection_name: Optional[str] = None,
        vector_size: int = 1024,
        local_mode: bool = False,
        local_path: Optional[Path] = None,
        memory_mode: bool = False,
        project_id: Optional[str] = None,
    ):
        self.host = host or settings.qdrant_host
        self.port = port or settings.qdrant_port
        # 如果指定了 project_id，使用项目专属集合名和路径
        if project_id:
            self.collection_name = collection_name or f"project_{project_id}"
            self.local_path = local_path or (settings.processed_data_path / project_id / "qdrant_db")
        else:
            self.collection_name = collection_name or settings.qdrant_collection_name
            self.local_path = local_path or (settings.processed_data_path / "qdrant_db")
        self.vector_size = vector_size
        self.local_mode = local_mode
        self.memory_mode = memory_mode
        self._snapshot_path = self.local_path / "memory_snapshot"

        self._client: Optional[QdrantClient] = None
        # bug-053 修复：懒连接加锁，避免多线程并发首次访问 client 时重复创建实例
        self._connect_lock = threading.Lock()
        # P1-6 修复：关闭标记。close() 后不再惰性重连，
        # 避免被替换的旧 pipeline 访问 client 时与新 pipeline 在同一 Qdrant 本地路径上双客户端冲突
        self._closed = False

    @property
    def client(self) -> QdrantClient:
        if self._client is None and not self._closed:
            with self._connect_lock:
                if self._client is None and not self._closed:
                    self._connect()
        return self._client

    def _connect(self) -> None:
        """连接数据库"""
        if self.memory_mode:
            # 本地持久化模式（使用专用快照路径，查询快，重启后数据保留）
            # 注意：这不是纯 RAM 模式，而是使用 Qdrant 本地存储引擎的快速模式
            self._snapshot_path.mkdir(parents=True, exist_ok=True)
            self._client = QdrantClient(path=str(self._snapshot_path))
            logger.info(f"Qdrant 本地持久模式（快照路径）: {self._snapshot_path}")
        elif self.local_mode:
            # 本地持久化模式（无需启动 Qdrant 服务）
            self.local_path.mkdir(parents=True, exist_ok=True)
            self._client = QdrantClient(path=str(self.local_path))
            logger.info(f"Qdrant 本地持久模式: {self.local_path}")
        else:
            try:
                self._client = QdrantClient(
                    host=self.host,
                    port=self.port,
                    timeout=30,
                )
                self._client.get_collections()
                logger.info(f"Qdrant 远程模式: {self.host}:{self.port}")
            except Exception as e:
                logger.warning(f"Qdrant 远程连接失败，回退到本地模式: {e}")
                self.local_mode = True
                self.local_path.mkdir(parents=True, exist_ok=True)
                self._client = QdrantClient(path=str(self.local_path))
                logger.info(f"Qdrant 本地持久模式（回退）: {self.local_path}")

    def create_collection(self, overwrite: bool = False) -> None:
        """创建集合（带覆盖保护）"""
        # 检查集合是否已存在
        try:
            existing = self.client.get_collection(self.collection_name)
            if not overwrite:
                logger.info(f"集合已存在: {self.collection_name}")
                return
            # 覆盖模式：删除旧集合
            logger.info(f"正在删除旧集合: {self.collection_name}")
            self.client.delete_collection(self.collection_name)
        except Exception as e:
            err_str = str(e).lower()
            if "not found" in err_str or "404" in err_str or "doesn't exist" in err_str:
                # 集合不存在，直接创建
                logger.info(f"集合不存在，准备创建: {self.collection_name}")
            else:
                # 其他异常（如网络错误、认证失败）直接抛出，避免后续错误更难诊断（bug-019）
                logger.error(f"检查集合时出现异常: {e}")
                raise

        # 创建新集合
        try:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=qdrant_models.VectorParams(
                    size=self.vector_size,
                    distance=qdrant_models.Distance.COSINE,
                ),
                optimizers_config=qdrant_models.OptimizersConfigDiff(
                    indexing_threshold=0,  # 立即创建索引
                ),
            )
            logger.info(f"已创建集合: {self.collection_name} (向量维度: {self.vector_size})")
        except Exception as e:
            logger.error(f"创建集合失败: {e}")
            raise

    def upsert(
        self,
        chunks: List[Chunk],
        embeddings: List[List[float]],
        batch_size: int = 64,
    ) -> int:
        """批量插入向量数据"""
        if len(chunks) != len(embeddings):
            raise ValueError(f"chunks ({len(chunks)}) 与 embeddings ({len(embeddings)}) 数量不匹配")

        points = []
        for chunk, embedding in zip(chunks, embeddings):
            # 使用稳定哈希（避免 hash() 的随机化）
            # bug-010 修复：使用完整 MD5（128 位）而非截断的前 16 位十六进制字符（64 位），
            # 避免截断导致的哈希冲突风险（生日悖论，50000 条目时约 50% 冲突概率）
            point_id = int(hashlib.md5(chunk.id.encode()).hexdigest(), 16) % (2**63)

            # 构建 payload：metadata 展开为顶级字段（支持过滤）
            payload = {
                "chunk_id": chunk.id,
                "artifact_id": chunk.artifact_id,
                "artifact_name": chunk.artifact_name,
                "text": chunk.text,
                "chunk_type": chunk.chunk_type,
            }
            # 将 metadata 展开为顶级字段（支持 Qdrant 过滤）
            # 注意：列表类型直接存储为数组，Qdrant MatchAny 原生支持数组字段
            for k, v in chunk.metadata.items():
                if isinstance(v, (str, int, float, bool, list)):
                    payload[f"meta_{k}"] = v
            # 同时保留原始 metadata JSON 字符串
            # bug-051 修复：metadata 含不可序列化对象（如 set）时降级为空，
            # 避免整个 upsert 因 json.dumps 崩溃
            try:
                payload["metadata_json"] = json.dumps(chunk.metadata, ensure_ascii=False)
            except (TypeError, ValueError) as e:
                logger.warning(f"metadata 不可序列化，跳过 metadata_json: {chunk.id} - {e}")
                payload["metadata_json"] = "{}"

            point = qdrant_models.PointStruct(
                id=point_id,
                vector=embedding,
                payload=payload,
            )
            points.append(point)

        # 分批上传
        total_uploaded = 0
        for i in range(0, len(points), batch_size):
            batch = points[i : i + batch_size]
            try:
                self.client.upsert(
                    collection_name=self.collection_name,
                    points=batch,
                )
                total_uploaded += len(batch)
            except Exception as e:
                logger.error(f"上传批次 {i // batch_size} 失败: {e}")
                raise

        logger.info(f"向量数据入库完成: {total_uploaded} 条")
        return total_uploaded

    def search(
        self,
        query_vector: List[float],
        top_k: int = 10,
        score_threshold: Optional[float] = None,
        filter_conditions: Optional[Dict[str, Any]] = None,
    ) -> List[Tuple[Chunk, float]]:
        """向量检索"""
        # 构建过滤条件
        query_filter = None
        if filter_conditions:
            must_conditions = []
            for key, value in filter_conditions.items():
                field_key = f"meta_{key}"
                if isinstance(value, list):
                    must_conditions.append(
                        qdrant_models.FieldCondition(
                            key=field_key,
                            match=qdrant_models.MatchAny(any=value),
                        )
                    )
                else:
                    must_conditions.append(
                        qdrant_models.FieldCondition(
                            key=field_key,
                            match=qdrant_models.MatchValue(value=value),
                        )
                    )
            if must_conditions:
                query_filter = qdrant_models.Filter(must=must_conditions)

        try:
            # P0 连带修复：qdrant-client >=1.12 已移除弃用的 search 方法（改用 query_points），
            # 直接调用 self.client.search 会 AttributeError，导致语义检索永远失败；
            # 此处按客户端能力选择 API，兼容旧版（<=1.11 仍用 search）
            if hasattr(self.client, "query_points"):
                resp = self.client.query_points(
                    collection_name=self.collection_name,
                    query=query_vector,
                    limit=top_k,
                    score_threshold=score_threshold,
                    query_filter=query_filter,
                )
                hits = resp.points
            else:
                hits = self.client.search(
                    collection_name=self.collection_name,
                    query_vector=query_vector,
                    limit=top_k,
                    score_threshold=score_threshold,
                    query_filter=query_filter,
                )
        except Exception as e:
            err_str = str(e).lower()
            # 集合不存在（首次使用前未创建）返回空列表，其他异常传播
            if "not found" in err_str or "404" in err_str or "doesn't exist" in err_str:
                logger.warning(f"向量集合不存在，返回空结果: {e}")
                return []
            logger.error(f"向量检索异常: {e}")
            raise

        results = []
        for hit in hits:
            # bug-042 修复：Qdrant 可能返回无 payload 的 hit，降级为空 dict 避免崩溃
            payload = hit.payload or {}
            metadata = json.loads(payload.get("metadata_json", "{}"))
            chunk = Chunk(
                id=payload.get("chunk_id", ""),
                artifact_id=payload.get("artifact_id", ""),
                artifact_name=payload.get("artifact_name", ""),
                text=payload.get("text", ""),
                metadata=metadata,
                chunk_type=payload.get("chunk_type", "full"),
            )
            results.append((chunk, hit.score))

        return results

    def delete_stale_chunks(self, keep_chunk_ids: set) -> int:
        """删除集合中 chunk_id 不在 keep_chunk_ids 中的陈旧切片点

        P0-1 修复：build_knowledge_base(overwrite=False) 重建时，Qdrant 只 upsert 不删除，
        被移除/变更的文物旧向量会一直残留，导致语义检索返回知识库中已不存在的切片，
        与 BM25/缓存文件不一致。此方法在重建后清理这些陈旧点。

        Args:
            keep_chunk_ids: 本次重建后应保留的 chunk_id 集合

        Returns:
            删除的陈旧点数量（失败时返回 0，不影响主流程）
        """
        try:
            stale_ids = []
            offset = None
            while True:
                records, offset = self.client.scroll(
                    collection_name=self.collection_name,
                    limit=1000,
                    offset=offset,
                    with_payload=["chunk_id"],
                    with_vectors=False,
                )
                for record in records:
                    if record.payload.get("chunk_id") not in keep_chunk_ids:
                        stale_ids.append(record.id)
                if offset is None:
                    break
            if stale_ids:
                self.client.delete(
                    collection_name=self.collection_name,
                    points_selector=stale_ids,
                )
                logger.info(f"清理陈旧向量: {len(stale_ids)} 条")
            return len(stale_ids)
        except Exception as e:
            logger.warning(f"清理陈旧向量失败（不影响主流程）: {e}")
            return 0

    def delete_collection(self) -> None:
        """删除集合"""
        try:
            self.client.delete_collection(self.collection_name)
            logger.info(f"已删除集合: {self.collection_name}")
        except Exception as e:
            logger.warning(f"删除集合失败: {e}")

    def close(self) -> None:
        """关闭连接"""
        # P1-6 修复：与 client 懒连接共用锁，避免 close() 与 _connect() 交错执行
        # （项目切换时旧 pipeline 被关闭，若同时有在途请求正在建立连接，可能拿到半初始化状态）
        with self._connect_lock:
            if self._client:
                self._client.close()
                self._client = None
            # P1-6 修复：标记关闭，禁止后续惰性重连
            self._closed = True

    def reset_connection(self) -> None:
        """关闭当前连接并重置关闭标记，使下次访问 client 时按当前配置重新连接

        用于 pipeline 切换项目后，确保 Qdrant 客户端指向新项目的存储路径
        （否则 create_collection/upsert 会继续写入旧项目的 Qdrant 目录）。
        """
        self.close()
        self._closed = False