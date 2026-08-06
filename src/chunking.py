"""
文档切片模块 v2
将文物数据按策略切分为适合检索的文本块

优化说明（v2）：
  v1 每件文物生成 4 个切片（short/desc/sig/full），内容重叠严重。
  v2 每件文物生成 3 个切片（summary/detail/significance），
  每个切片信息密度更高、重叠更少，检索精度提升同时减少向量库存储。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

from loguru import logger

from src.data_loader import Artifact
from src.utils import generate_id


@dataclass
class Chunk:
    """文本切片"""
    id: str
    artifact_id: str
    artifact_name: str
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    chunk_type: str = "summary"  # summary / detail / significance

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "artifact_id": self.artifact_id,
            "artifact_name": self.artifact_name,
            "text": self.text,
            "metadata": self.metadata,
            "chunk_type": self.chunk_type,
        }


class ChunkingStrategy:
    """切片策略基类"""

    def chunk(self, artifact: Artifact) -> List[Chunk]:
        raise NotImplementedError


class SmartChunking(ChunkingStrategy):
    """
    智能切片策略（v2）

    为每件文物生成 3 个信息密度高、重叠少的切片：

    1. summary：概要切片，包含名称、朝代、类别、标签、一句话亮点
       → 用于快速匹配和推荐类问题
    2. detail：详情切片，包含完整的描述、材质、出土地、现藏地
       → 用于事实类问题
    3. significance：意义切片，包含历史意义和文化价值
       → 用于推荐类问题（回答"为什么有代表性"）
    """

    def __init__(
        self,
        enable_summary: bool = True,
        enable_detail: bool = True,
        enable_significance: bool = True,
    ):
        self.enable_summary = enable_summary
        self.enable_detail = enable_detail
        self.enable_significance = enable_significance

    def chunk(self, artifact: Artifact) -> List[Chunk]:
        """为一件文物生成 3 个切片"""
        chunks: List[Chunk] = []
        meta = {
            "name": artifact.name,
            "dynasty": artifact.dynasty,
            "category": artifact.category,
            "location": artifact.location,
            "tags": artifact.tags,
            "importance": artifact.importance,
        }

        # 1. 概要切片：名称 + 朝代 + 类别 + 标签 + 一句话亮点
        if self.enable_summary:
            # 从描述中提取第一句作为亮点
            first_sentence = ""
            if artifact.description:
                # 取第一个句号/感叹号前的文字
                for sep in ["。", "！", "；"]:
                    if sep in artifact.description:
                        first_sentence = artifact.description.split(sep)[0] + sep
                        break
                if not first_sentence:
                    first_sentence = artifact.description[:100] + ("..." if len(artifact.description) > 100 else "")

            tags = artifact.tags if isinstance(artifact.tags, list) else []  # bug-060：tags 为标量时按空处理
            # bug-090 修复：tags 元素可能为非字符串（如 JSON 数字列表 [1,2,3]），
            # join 前统一转 str，避免 "、".join 抛 TypeError 导致整件文物切片被静默丢弃
            tags_str = "、".join(str(t) for t in tags[:5]) if tags else ""
            summary_parts = [
                f"名称：{artifact.name}",
                f"朝代：{artifact.dynasty}",
                f"类别：{artifact.category}",
            ]
            if tags_str:
                summary_parts.append(f"标签：{tags_str}")
            if first_sentence:
                summary_parts.append(f"亮点：{first_sentence}")

            summary_text = "\n".join(summary_parts)
            chunks.append(Chunk(
                id=generate_id(f"{artifact.id}:summary:{summary_text}"),
                artifact_id=artifact.id,
                artifact_name=artifact.name,
                text=summary_text,
                metadata={**meta, "chunk_type": "summary"},
                chunk_type="summary",
            ))

        # 2. 详情切片：完整的描述信息
        if self.enable_detail:
            detail_parts = [
                f"文物名称：{artifact.name}",
                f"朝代：{artifact.dynasty}",
                f"类别：{artifact.category}",
                f"材质：{artifact.material}",
                f"现藏于：{artifact.location}",
            ]
            if artifact.provenance:
                detail_parts.append(f"出土地：{artifact.provenance}")
            if artifact.description:
                detail_parts.append(f"描述：{artifact.description}")

            detail_text = "\n".join(detail_parts)
            chunks.append(Chunk(
                id=generate_id(f"{artifact.id}:detail:{detail_text}"),
                artifact_id=artifact.id,
                artifact_name=artifact.name,
                text=detail_text,
                metadata={**meta, "chunk_type": "detail"},
                chunk_type="detail",
            ))

        # 3. 意义切片：历史意义和文化价值
        if self.enable_significance:
            sig_parts = [f"文物名称：{artifact.name}", f"朝代：{artifact.dynasty}"]
            if artifact.historical_significance:
                sig_parts.append(f"历史意义：{artifact.historical_significance}")
            if artifact.cultural_value:
                sig_parts.append(f"文化价值：{artifact.cultural_value}")

            if len(sig_parts) > 2:  # 至少有一条意义信息
                sig_text = "\n".join(sig_parts)
                chunks.append(Chunk(
                    id=generate_id(f"{artifact.id}:significance:{sig_text}"),
                    artifact_id=artifact.id,
                    artifact_name=artifact.name,
                    text=sig_text,
                    metadata={**meta, "chunk_type": "significance"},
                    chunk_type="significance",
                ))

        return chunks


class ChunkingPipeline:
    """切片流水线"""

    def __init__(self, strategy: Optional[ChunkingStrategy] = None):
        self.strategy = strategy or SmartChunking()

    def process(self, artifacts: List[Artifact]) -> List[Chunk]:
        """处理所有文物数据"""
        all_chunks: List[Chunk] = []
        for artifact in artifacts:
            try:
                chunks = self.strategy.chunk(artifact)
                all_chunks.extend(chunks)
            except Exception as e:
                logger.error(f"切片失败: {artifact.name} - {e}")
                continue

        avg = len(all_chunks) / len(artifacts) if artifacts else 0
        logger.info(
            f"切片完成: {len(artifacts)} 件文物 → {len(all_chunks)} 个切片"
            f"（平均每件 {avg:.1f} 个切片，v2 智能切片）"
        )
        return all_chunks