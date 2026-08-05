"""
RAG 流水线模块
整合所有模块，提供端到端的检索增强生成能力
"""

from __future__ import annotations

import json
import time
import threading
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple, Union
from enum import Enum

from loguru import logger

from src.config import settings
from src.data_loader import Artifact, DataLoader
from src.chunking import Chunk, ChunkingPipeline, SmartChunking
from src.embeddings import BailianEmbedding
from src.vector_store import VectorStore
from src.retriever import BM25Retriever, HybridRetriever
from src.reranker import BailianReranker
from src.llm import BailianLLM
from src.utils import save_json, load_json
from src.document_loader import DocumentLoader
from src.project import project_manager, ProjectConfig


# 上下文 chunk 分隔符（使用不易出现在正文中的唯一字符串，避免与 chunk 正文内容冲突，bug-031）
CHUNK_SEPARATOR = "\n\n=====CHUNK_SEPARATOR=====\n\n"


class QueryType(str, Enum):
    """查询类型"""
    RECOMMENDATION = "recommendation"  # 推荐类
    FACTUAL = "factual"                # 事实类
    COMPARISON = "comparison"          # 比较类
    OPEN_ENDED = "open_ended"          # 开放讨论
    UNKNOWN = "unknown"                # 未知


# ========== Prompt 模板 ==========

SYSTEM_PROMPT_RECOMMEND = """你是一位专业的知识助手。你的任务是根据用户问题和提供的参考信息，给出优质的推荐。

## 推荐原则
1. 从参考信息中挑选 **3~5 个** 最具代表性的结果进行推荐
2. 每个推荐项需包含：**名称、简要介绍、推荐理由**
3. 尽量覆盖 **不同类型**，确保推荐结果多样化
4. 推荐理由要具体，说明该结果为何具有代表性
5. 如果参考信息不足，请如实说明，不要编造信息
6. 回答格式要清晰易读，有层次感

## 参考信息
{context}

## 输出格式要求
请使用结构化格式输出推荐结果。
"""

SYSTEM_PROMPT_FACTUAL = """你是一位专业的知识助手。请根据用户问题和提供的参考信息，给出准确、详实的回答。

## 回答原则
1. 基于参考信息回答，不要编造事实
2. 如果参考信息不足，请如实说明
3. 引用参考信息时，可以提及来源名称
4. 回答要简洁明了

## 参考信息
{context}
"""

SYSTEM_PROMPT_DEFAULT = """你是一位专业的知识助手。请根据用户问题和提供的参考信息，给出专业、准确的回答。

## 回答原则
1. 基于参考信息回答
2. 如果参考信息不足，请如实说明
3. 回答要结构清晰、内容详实

## 参考信息
{context}
"""

SYSTEM_PROMPT_CHITCHAT = """你是一位友好的知识助手。

## 回答原则
1. 如果用户问的是知识库相关的问题，请基于检索结果回答
2. 如果用户问的是闲聊（问候、天气、心情等），请友好回应
3. 如果用户问的是其他问题，请用你的通用知识回答
4. 回答要简洁、友好、有帮助
5. 如果用户问题与知识库无关，不需要检索
"""


class RAGPipeline:
    """
    完整的 RAG 流水线
    提供端到端的检索增强生成能力
    """

    # 上下文最大字符数（根据不同模型动态调整）
    # 注意：修改此值不会影响准确率，只影响是否能塞进 LLM 上下文窗口
    # 根据模型上下文窗口估算：Qwen-plus 128K tokens ≈ 80K 字符，Qwen-max 32K tokens ≈ 25K 字符
    MAX_CONTEXT_CHARS = 30000

    # 闲聊模式关键词（用于 is_kb_related 判断）
    CHITCHAT_KEYWORDS = [
        "你好", "您好", "嗨", "hello", "hi", "hey",
        "再见", "拜拜", "bye", "goodbye",
        "谢谢", "感谢", "多谢", "thanks", "thank",
        "你是谁", "你叫什么", "who are you",
        "你能做什么", "你会什么", "可以做什么",
        "天气", "今天天气", "明天天气",
        "早上好", "下午好", "晚上好", "晚安",
        "开心", "难过", "心情",
        "不错", "很好", "厉害",
    ]

    # 查询分类模式（定义为类常量，避免每次调用重建）
    _RECOMMEND_PATTERNS = [
        (["推荐", "给我推荐", "帮我推荐", "推荐几个", "推荐一些"], 10),
        (["代表性", "著名", "经典", "必看", "热门", "精选"], 8),
        (["有哪些", "有什么", "什么值得", "哪些值得"], 6),
        (["介绍一下", "给我介绍", "说说", "谈谈", "讲讲"], 4),
        (["值得看", "值得去", "值得推荐", "值得关注"], 8),
    ]
    _FACTUAL_PATTERNS = [
        (["多少", "多重", "多高", "多大", "多长", "多宽", "多深"], 10),
        (["何时", "哪里", "什么时候", "在哪儿", "在什么地方"], 10),
        (["是什么", "什么是", "哪些是"], 8),
        (["是谁", "是谁做的", "谁创作的", "谁制造的", "谁发明的"], 8),
        (["位于", "在哪里", "在哪儿"], 8),
    ]
    _COMPARE_PATTERNS = [
        (["比较", "对比", "vs", "versus"], 10),
        (["区别", "不同", "差异", "异同"], 10),
        (["哪个好", "哪个更", "哪个更适合"], 8),
        (["与...相比", "相对于", "比不上"], 8),
    ]
    _OPEN_PATTERNS = [
        (["谈谈", "讨论", "分析", "评价", "如何看待"], 6),
        (["发展", "历史", "演变", "影响", "意义"], 4),
        (["为什么", "为何", "怎么"], 3),
    ]

    def __init__(
        self,
        embedding_model: Optional[str] = None,
        llm_model: Optional[str] = None,
        vector_size: Optional[int] = None,
        local_mode: bool = True,
        enable_cache: bool = True,
        memory_mode: bool = False,
        project_id: Optional[str] = None,
    ):
        # 从配置读取向量维度
        if vector_size is None:
            vector_size = settings.embedding_dimension

        self.enable_cache = enable_cache
        self.project_id = project_id
        self.project_cfg: Optional[ProjectConfig] = None

        # 加载项目配置（如果指定了 project_id）
        if project_id:
            self.project_cfg = project_manager.switch_to(project_id)
            logger.info(f"使用项目: {project_id} - {self.project_cfg.name}")

        # 早期校验 API Key，避免 dashscope SDK 返回模糊错误
        if not settings.dashscope_api_key:
            logger.warning(
                "DASHSCOPE_API_KEY 未设置！API 调用将失败。"
                "请通过环境变量或 .env 文件设置。"
            )

        # 初始化各模块（按项目隔离）
        self.embedding = BailianEmbedding(
            model=embedding_model or settings.embedding_model_name,
            dimension=vector_size,
        )
        self.vector_store = VectorStore(
            vector_size=vector_size,
            local_mode=local_mode,
            memory_mode=memory_mode,
            project_id=project_id,
            collection_name=self.project_cfg.collection_name if self.project_cfg else None,
            local_path=self.project_cfg.qdrant_path if self.project_cfg else None,
        )
        self.bm25_retriever = BM25Retriever()
        self.hybrid_retriever = HybridRetriever(
            vector_store=self.vector_store,
            embedding=self.embedding,
            bm25_retriever=self.bm25_retriever,
        )
        self.reranker = BailianReranker(
            top_k=settings.reranker_top_k,
        )
        self.llm = BailianLLM(
            model=llm_model or settings.llm_model_name,
            use_cache=enable_cache,
        )
        self.chunking_pipeline = ChunkingPipeline(
            strategy=SmartChunking()
        )

        self._is_built = False
        self._warmup_done = False
        self._kb_lock = threading.Lock()

    def warmup(self) -> None:
        """预热：预加载知识库，减少首次查询延迟"""
        if self._warmup_done:
            return
        logger.info("预热知识库...")
        try:
            self._ensure_knowledge_base()
            self._warmup_done = True
            logger.info("知识库预热完成（BM25 索引已加载到内存）")
        except Exception as e:
            logger.warning(f"预热失败（不影响正常使用）: {e}")

    @staticmethod
    def _trim_context(context, max_chars: int = MAX_CONTEXT_CHARS) -> str:
        """
        裁剪上下文到最大字符数。

        策略：按段落裁剪，保留靠前的段落（因为检索结果已按相关性排序）。
        如果某一篇文物信息太长，优先保留完整的前 N 篇，而不是截断最后一篇。
        这样确保每件被引用的文物信息都是完整的。

        注意：此方法仅当上下文远超 Token 限制时才触发，
        正常情况（50件文物以内）不会触发裁剪。

        支持传入字符串（兼容旧调用）或列表（避免 CHUNK_SEPARATOR 在 chunk 正文中导致分割错误）。
        """
        if isinstance(context, str):
            if len(context) <= max_chars:
                return context
            paragraphs = context.split(CHUNK_SEPARATOR)
        else:
            # 已经是段落列表，无需分割
            paragraphs = context

        trimmed = []
        char_count = 0

        for p in paragraphs:
            # 每段前加分隔符的额外开销
            overhead = len(CHUNK_SEPARATOR) if trimmed else 0
            if char_count + len(p) + overhead <= max_chars:
                trimmed.append(p)
                char_count += len(p) + overhead
            else:
                # 如果当前段落放不下，舍弃剩余所有段落
                logger.debug(
                    f"上下文裁剪: {char_count} 字符 "
                    f"（舍弃 {len(paragraphs) - len(trimmed)} 个段落）"
                )
                break

        return CHUNK_SEPARATOR.join(trimmed)

    def build_knowledge_base(
        self,
        data_path: Optional[Path] = None,
        artifacts: Optional[List[Artifact]] = None,
        overwrite: bool = False,
        project_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """
        构建知识库
        1. 加载数据
        2. 切片处理
        3. 生成 Embedding
        4. 存入向量数据库
        5. 构建 BM25 索引

        Args:
            data_path: 数据文件路径（默认根据 project_id 自动查找）
            artifacts: 直接传入 Artifact 列表
            overwrite: 是否覆盖已有知识库
            project_id: 项目 ID，用于确定数据路径和存储位置
        """
        # 确定项目
        pid = project_id or self.project_id
        if pid and self.project_cfg is None:
            self.project_cfg = project_manager.switch_to(pid)
            self.project_id = pid

        project_name = self.project_cfg.name if self.project_cfg else "默认"

        logger.info("=" * 50)
        logger.info(f"开始构建知识库 - 项目: {project_name}")
        logger.info("=" * 50)

        # 1. 加载数据
        if artifacts is None:
            if data_path is None:
                if self.project_cfg:
                    # 使用项目专属数据目录
                    data_path = self.project_cfg.data_dir / "data.json"
                    if not data_path.exists():
                        # 也尝试 artifacts.json
                        data_path = self.project_cfg.data_dir / "artifacts.json"
                else:
                    data_path = settings.raw_data_path / "artifacts.json"
            artifacts = DataLoader.load(data_path)

        # 检查是否有数据
        if not artifacts:
            logger.error("没有加载到任何文物数据！")
            return {"artifacts": 0, "chunks": 0, "vectors": 0}

        # 2. 切片处理
        chunks = self.chunking_pipeline.process(artifacts)
        logger.info(f"切片完成: {len(chunks)} 个切片")

        # 3. 生成 Embedding
        texts = [chunk.text for chunk in chunks]
        embeddings = self.embedding.embed_batch(texts)

        # 4. 存入向量数据库
        self.vector_store.create_collection(overwrite=overwrite)
        vector_count = self.vector_store.upsert(chunks, embeddings)

        # 5. 构建 BM25 索引
        self.bm25_retriever.build(chunks)

        # 6. 预计算高频问题 Embedding（加速后续查询）
        try:
            self.embedding.precompute_patterns()
        except Exception as e:
            logger.warning(f"预计算高频问题 Embedding 失败（不影响正常使用）: {e}")

        # 7. 保存切片数据到本地（项目专属路径）
        chunk_data = [c.to_dict() for c in chunks]
        if self.project_cfg:
            save_path = self.project_cfg.chunk_cache_path
        else:
            save_path = settings.processed_data_path / "chunks.json"
        save_json(chunk_data, save_path)

        self._is_built = True

        stats = {
            "artifacts": len(artifacts),
            "chunks": len(chunks),
            "vectors": vector_count,
        }
        logger.info(f"知识库构建完成: {stats}")
        return stats

    def add_artifacts(
        self,
        artifacts: List[Artifact],
    ) -> Dict[str, int]:
        """
        增量添加文物到已有知识库（无需重建）

        流程：
          1. 对新文物切片
          2. 生成 Embedding
          3. 追加到向量数据库
          4. 重建 BM25 索引（全量）
          5. 更新缓存文件
        """
        if not artifacts:
            logger.warning("没有新文物需要添加")
            return {"artifacts": 0, "chunks": 0, "vectors": 0}

        # 确保知识库已初始化
        if not self._is_built:
            # 首次添加 = 全量构建
            return self.build_knowledge_base(
                artifacts=artifacts,
                overwrite=False,
            )

        logger.info(f"增量添加 {len(artifacts)} 件文物...")

        # 1. 切片
        new_chunks = self.chunking_pipeline.process(artifacts)
        logger.info(f"新切片: {len(new_chunks)} 个")

        # 2. 生成 Embedding
        texts = [chunk.text for chunk in new_chunks]
        embeddings = self.embedding.embed_batch(texts)

        # 3. 追加到向量数据库
        vector_count = self.vector_store.upsert(new_chunks, embeddings)

        # 4. 重建 BM25 索引（合并新旧数据）
        # 从项目专属缓存加载旧切片
        old_chunks = []
        if self.project_cfg:
            cache_path = self.project_cfg.chunk_cache_path
        else:
            cache_path = settings.processed_data_path / "chunks.json"

        if cache_path.exists():
            try:
                old_data = load_json(cache_path)
                # 过滤 Chunk 不接受的字段，避免缓存格式变更时崩溃（bug-028）
                valid_fields = set(Chunk.__dataclass_fields__.keys())
                old_chunks = [
                    Chunk(**{k: v for k, v in c.items() if k in valid_fields})
                    for c in old_data
                ]
            except Exception as e:
                logger.warning(f"加载缓存失败: {e}")

        all_chunks = old_chunks + new_chunks
        self.bm25_retriever.build(all_chunks)

        # 5. 更新缓存文件
        all_chunk_data = [c.to_dict() for c in all_chunks]
        save_json(all_chunk_data, cache_path)

        stats = {
            "artifacts": len(artifacts),
            "new_chunks": len(new_chunks),
            "total_chunks": len(all_chunks),
            "vectors_added": vector_count,
        }
        logger.info(f"增量添加完成: {stats}")
        return stats

    def build_knowledge_base_from_documents(
        self,
        source_path: Path,
        category: str = "文档资料",
        overwrite: bool = False,
        recursive: bool = True,
        enable_ocr: bool = True,
        ocr_engine: str = "paddle",
        project_id: Optional[str] = None,
    ) -> Dict[str, int]:
        """
        从多格式文档构建知识库
        支持 PDF、Word、TXT、Markdown、图片(OCR) 等格式

        Args:
            source_path: 文档文件或目录路径
            category: 文档分类标签
            overwrite: 是否覆盖已有知识库
            recursive: 是否递归扫描子目录
            enable_ocr: 是否启用图片 OCR
            ocr_engine: OCR 引擎 (paddle / tesseract)

        Returns:
            构建统计信息
        """
        # 确定项目（与 build_knowledge_base 一致）
        pid = project_id or self.project_id
        if pid and self.project_cfg is None:
            self.project_cfg = project_manager.switch_to(pid)
            self.project_id = pid

        project_name = self.project_cfg.name if self.project_cfg else "默认"
        logger.info("=" * 50)
        logger.info(f"从多格式文档构建知识库 - 项目: {project_name}")
        logger.info(f"数据源: {source_path}")
        logger.info("=" * 50)

        # 1. 加载文档并转换为 Artifact
        doc_loader = DocumentLoader(
            enable_ocr=enable_ocr,
            ocr_engine=ocr_engine,
        )
        artifacts = doc_loader.load_all_as_artifacts(
            source=source_path,
            category=category,
            recursive=recursive,
        )

        if not artifacts:
            logger.warning("未找到任何可解析的文档！")
            return {"artifacts": 0, "chunks": 0, "vectors": 0}

        # 2. 切片处理
        chunks = self.chunking_pipeline.process(artifacts)
        logger.info(f"切片完成: {len(chunks)} 个切片")

        # 3. 生成 Embedding
        texts = [chunk.text for chunk in chunks]
        embeddings = self.embedding.embed_batch(texts)

        # 4. 存入向量数据库
        self.vector_store.create_collection(overwrite=overwrite)
        vector_count = self.vector_store.upsert(chunks, embeddings)

        # 5. 构建 BM25 索引
        self.bm25_retriever.build(chunks)

        # 6. 保存切片缓存（项目专属路径）
        chunk_data = [c.to_dict() for c in chunks]
        if self.project_cfg:
            save_path = self.project_cfg.chunk_cache_path
        else:
            save_path = settings.processed_data_path / "chunks_documents.json"
        save_json(chunk_data, save_path)

        self._is_built = True

        stats = {
            "artifacts": len(artifacts),
            "chunks": len(chunks),
            "vectors": vector_count,
            "source": str(source_path),
        }
        logger.info(f"文档知识库构建完成: {stats}")
        return stats

    def classify_query(self, query: str) -> QueryType:
        """
        判断查询类型（v2，基于评分机制）

        使用打分机制替代原来的顺序匹配，处理更精准。
        例如 "什么文物最值得看" 虽然含 "什么"，但 "值得" 将正确归类为推荐。
        """
        q = query.strip()

        # ========== 评分规则 ==========
        scores = {
            QueryType.RECOMMENDATION: 0,
            QueryType.FACTUAL: 0,
            QueryType.COMPARISON: 0,
            QueryType.OPEN_ENDED: 0,
        }

        # --- 推荐类（使用类常量，避免每次调用重建）
        for keywords, score in self._RECOMMEND_PATTERNS:
            for kw in keywords:
                if kw in q:
                    scores[QueryType.RECOMMENDATION] += score
                    break

        # --- 事实类 ---
        for keywords, score in self._FACTUAL_PATTERNS:
            for kw in keywords:
                if kw in q:
                    scores[QueryType.FACTUAL] += score
                    break

        # --- 比较类 ---
        for keywords, score in self._COMPARE_PATTERNS:
            for kw in keywords:
                if kw in q:
                    scores[QueryType.COMPARISON] += score
                    break

        # bug-016 修复：如果 "比较" 后跟推荐类词汇，降低比较类得分，避免误分类
        if "比较" in q:
            for recommend_word in ["有名", "著名", "经典", "推荐", "值得", "好看", "热门"]:
                if recommend_word in q:
                    scores[QueryType.COMPARISON] -= 5
                    break

        # --- 开放讨论 ---
        for keywords, score in self._OPEN_PATTERNS:
            for kw in keywords:
                if kw in q:
                    scores[QueryType.OPEN_ENDED] += score
                    break

        # --- 长度惩罚/奖励 ---
        if len(q) >= 15:
            scores[QueryType.OPEN_ENDED] += 2
        if len(q) <= 8:
            # 短问题通常是事实类
            scores[QueryType.FACTUAL] += 2

        # ========== 选择最高分 ==========
        max_score = max(scores.values())
        if max_score == 0:
            # 没有匹配任何规则，按长度判断
            if len(q) >= 20:
                return QueryType.OPEN_ENDED
            return QueryType.RECOMMENDATION  # 默认推荐类

        # 如果有并列，按优先级：RECOMMENDATION > FACTUAL > COMPARISON > OPEN_ENDED
        priority = [QueryType.RECOMMENDATION, QueryType.FACTUAL, QueryType.COMPARISON, QueryType.OPEN_ENDED]
        best = QueryType.RECOMMENDATION
        best_score = 0
        for qt in priority:
            if scores[qt] > best_score:
                best_score = scores[qt]
                best = qt

        return best

    @staticmethod
    def is_kb_related(question: str) -> bool:
        """
        判断问题是否与知识库相关

        如果问题明显是闲聊（问候、天气、个人问题等），返回 False，
        直接使用 LLM 自身知识回答，不走 RAG 检索，节省时间和 API 费用。

        bug-009 修复说明：
          原实现使用简单子串匹配，导致"你好文物"、"谢谢你的帮助是什么文物"等包含闲聊关键词
          的知识库问题被误判为闲聊。修复后使用精确匹配 + 前缀匹配策略，
          仅当问题完全匹配闲聊关键词或以闲聊关键词开头且剩余部分仅为标点/语气词时，才判为闲聊。
        """
        q = question.strip()

        # 纯空字符串（非知识库相关）
        if len(q) == 0:
            return False

        # bug-022 修复：纯标点/纯符号查询，没有检索意义，视为非知识库相关
        if all(c in ' ，。！？,。!?～~、；：\'""（）()【】《》『』「」·…—-' for c in q):
            return False

        q_lower = q.lower()

        # 使用精确匹配 + 前缀匹配，避免子串误判
        for pattern in RAGPipeline.CHITCHAT_KEYWORDS:
            # 精确匹配：问题完全等于闲聊关键词
            if q_lower == pattern.lower():
                return False
            # 前缀匹配：问题以闲聊关键词开头且剩余部分仅为标点/语气词
            if q_lower.startswith(pattern.lower()):
                extra = q_lower[len(pattern):].strip()
                if not extra or all(c in '，。！？,。!? ～~啊呀哦嗯吧呗吗' for c in extra):
                    return False

        return True

    @staticmethod
    def _validate_message_roles(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """
        验证并修复消息角色序列，确保符合 LLM API 要求：
          - 消息以 user 角色结尾
          - 无连续 user 角色消息
          - user 和 assistant 角色交替出现
        """
        if not messages:
            return messages

        validated = []
        for msg in messages:
            role = msg.get("role", "")
            if role == "user":
                # 如果前一条也是 user，跳过
                if validated and validated[-1]["role"] == "user":
                    continue
                validated.append(msg)
            elif role == "assistant":
                # assistant 前必须有 user
                if not validated or validated[-1]["role"] != "user":
                    continue
                validated.append(msg)
            else:
                # 未知角色，丢弃
                continue

        # 确保最后一条是 user（如果以 assistant 结尾，丢弃它）
        if validated and validated[-1]["role"] == "assistant":
            validated.pop()

        # 如果全部被清空，至少保留空列表
        return validated

    def _ensure_knowledge_base(self) -> None:
        """确保知识库已加载（BM25 + Qdrant 双重检查，线程安全）"""
        if self._is_built:
            return

        with self._kb_lock:
            if self._is_built:
                return

            # 确定 Qdrant 路径和缓存路径（优先使用项目目录）
            if self.project_cfg:
                qdrant_base = self.project_cfg.qdrant_path
                cache_path = self.project_cfg.chunk_cache_path
            else:
                qdrant_base = settings.processed_data_path / "qdrant_db"
                cache_path = settings.processed_data_path / "chunks.json"

            # 修复 bug-033：memory_mode 下 Qdrant 数据存储在 _snapshot_path 子目录中
            # 使用正确的路径检查，避免因 memory_snapshot 目录本身导致误判
            if self.vector_store.memory_mode:
                qdrant_path = self.vector_store._snapshot_path
            else:
                qdrant_path = qdrant_base

            qdrant_ready = qdrant_path.exists() and any(qdrant_path.iterdir())

            # 尝试从项目专属缓存加载 BM25 索引
            loaded = False
            if cache_path.exists():
                try:
                    chunk_data = load_json(cache_path)
                    # 过滤 Chunk 不接受的字段，避免缓存格式变更时崩溃（bug-028）
                    valid_fields = set(Chunk.__dataclass_fields__.keys())
                    chunks = [
                        Chunk(**{k: v for k, v in c.items() if k in valid_fields})
                        for c in chunk_data
                    ]
                    self.bm25_retriever.build(chunks)
                    loaded = True
                    logger.info(f"从缓存加载知识库: {cache_path.name} ({len(chunks)} 个切片)")
                except Exception as e:
                    logger.warning(f"加载缓存失败: {e}")

            if loaded and qdrant_ready:
                self._is_built = True
                logger.info("知识库完全就绪（BM25 + Qdrant）")
            elif loaded and not qdrant_ready:
                logger.warning("BM25 索引已加载，但 Qdrant 向量数据库不存在，语义检索将不可用")
                self._is_built = True
            else:
                raise RuntimeError(
                    "知识库未构建！请先运行 build_knowledge_base()"
                )

    def _build_context(self, retrieve_results, max_chars=None) -> str:
        """构建并裁剪上下文（通用格式，不包含领域特定术语）

        修复说明（bug-010）：
          v1 按 artifact_id 去重，同一文物的多个切片只保留一个。
          例如 summary 和 detail 切片中，后出现的被丢弃，
          导致 detail 中包含的出土地、现藏地等信息丢失。
          v2 修复：按 chunk.id 去重，允许同一文物的多个切片进入上下文，
          由 _trim_context 控制总长度。
        """
        if max_chars is None:
            max_chars = self.MAX_CONTEXT_CHARS

        context_parts = []
        seen_chunk_ids = set()
        for chunk, _ in retrieve_results:
            if chunk.id not in seen_chunk_ids:
                context_parts.append(
                    f"【{chunk.artifact_name}】\n{chunk.text}"
                )
                seen_chunk_ids.add(chunk.id)

        # 直接传入列表，避免 CHUNK_SEPARATOR 在 chunk 正文中导致分割错误
        return self._trim_context(context_parts, max_chars)

    def _select_prompt(self, query_type: QueryType, context: str) -> str:
        """选择 Prompt 模板（优先使用项目自定义 Prompt，无则用通用模板）

        修复说明（bug-015）：
          v1 中 COMPARISON 类型使用 factual prompt，缺少比较引导。
          v2 修复：COMPARISON 改用 default prompt（更通用，不含领域特定术语）。
        """
        if self.project_cfg:
            # 使用项目自定义 Prompt
            prompt_type_map = {
                QueryType.RECOMMENDATION: "recommend",
                QueryType.FACTUAL: "factual",
                QueryType.COMPARISON: "default",
                QueryType.OPEN_ENDED: "default",
                QueryType.UNKNOWN: "default",
            }
            ptype = prompt_type_map[query_type]
            return self.project_cfg.get_prompt(ptype, context=context)

        # 默认通用模板
        system_prompt_map = {
            QueryType.RECOMMENDATION: SYSTEM_PROMPT_RECOMMEND,
            QueryType.FACTUAL: SYSTEM_PROMPT_FACTUAL,
            QueryType.COMPARISON: SYSTEM_PROMPT_DEFAULT,
            QueryType.OPEN_ENDED: SYSTEM_PROMPT_DEFAULT,
            QueryType.UNKNOWN: SYSTEM_PROMPT_DEFAULT,
        }
        return system_prompt_map[query_type].format(context=context)

    def query(
        self,
        question: str,
        top_k: int = 10,
        rerank: bool = True,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        执行 RAG 查询（非流式，支持多轮对话和闲聊路由）

        Args:
            question: 用户问题
            top_k: 检索 Top-K
            rerank: 是否启用重排序
            conversation_history: 对话历史

        Returns:
            {
                "answer": str,
                "query_type": str,
                "retrieved_chunks": [...],
                "context": str,
                "timing": {...},
            }
        """
        timings = {}
        t_start = time.time()

        # 构建消息列表（含对话历史）
        messages = []
        if conversation_history:
            recent_history = conversation_history[-8:]
            messages.extend(recent_history)
        messages.append({"role": "user", "content": question})

        # 验证消息角色序列：确保以 user 结尾且无连续 user
        messages = self._validate_message_roles(messages)

        # 判断是否与知识库相关
        kb_related = self.is_kb_related(question)
        query_type = self.classify_query(question) if kb_related else QueryType.UNKNOWN
        timings["classify"] = round((time.time() - t_start) * 1000)

        if not kb_related:
            # 闲聊/非知识库问题：直接 LLM 回答，不走 RAG
            logger.info(f"闲聊模式 | 问题: {question[:60]}...")
            answer = self.llm.chat(
                messages=messages,
                system_prompt=SYSTEM_PROMPT_CHITCHAT,
            )
            timings["total"] = round((time.time() - t_start) * 1000)
            return {
                "answer": answer,
                "query_type": "chitchat",
                "retrieved_chunks": [],
                "context": "",
                "timing": timings,
                "from_kb": False,
            }

        # ===== 知识库相关问题的 RAG 流程 =====
        self._ensure_knowledge_base()

        # 2. 检索（并行语义 + BM25）
        t_retrieve = time.time()
        retrieve_results = self.hybrid_retriever.retrieve(
            query=question,
            top_k=top_k,
            use_cache=self.enable_cache,
        )
        timings["retrieve"] = round((time.time() - t_retrieve) * 1000)

        if not retrieve_results:
            # 检索为空时仍调用 LLM，让模型基于对话历史或通用知识尝试回答
            logger.info(f"检索无结果，尝试 LLM 基于对话历史回答: {question[:60]}...")
            t_llm = time.time()
            answer = self.llm.chat(
                messages=messages,
                system_prompt=SYSTEM_PROMPT_CHITCHAT,
            )
            timings["llm"] = round((time.time() - t_llm) * 1000)
            timings["total"] = round((time.time() - t_start) * 1000)
            return {
                "answer": answer,
                "query_type": query_type.value,
                "retrieved_chunks": [],
                "context": "",
                "timing": timings,
                "from_kb": True,
            }

        # 3. 重排序（如果结果数 > 3 才做，否则跳过节省时间）
        t_rerank = time.time()
        if rerank and len(retrieve_results) > 3:
            retrieve_results = self.reranker.rerank(
                query=question,
                candidates=retrieve_results,
            )
        timings["rerank"] = round((time.time() - t_rerank) * 1000)

        # 4. 构建上下文（自动裁剪）
        context = self._build_context(retrieve_results)

        # 5. 选择 Prompt 模板
        system_prompt = self._select_prompt(query_type, context)

        # 6. 调用 LLM
        t_llm = time.time()
        answer = self.llm.chat(
            messages=messages,
            system_prompt=system_prompt,
        )
        timings["llm"] = round((time.time() - t_llm) * 1000)

        timings["total"] = round((time.time() - t_start) * 1000)

        result = {
            "answer": answer,
            "query_type": query_type.value,
            "retrieved_chunks": [
                {
                    "artifact_name": c.artifact_name,
                    "chunk_type": c.chunk_type,
                    "text": c.text[:200] + "...",
                    "score": round(s, 4),
                }
                for c, s in retrieve_results
            ],
            "context": context,
            "timing": timings,
            "from_kb": True,
        }
        return result

    def query_stream(
        self,
        question: str,
        top_k: int = 10,
        rerank: bool = True,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> Generator[Union[Dict[str, Any], str], None, None]:
        """
        流式 RAG 查询（逐 token 产出，支持多轮对话和闲聊路由）

        Yields:
            检索完成后先 yield 一个 metadata dict（包含检索结果信息和计时），
            然后逐 token yield 回答内容（str）。
            所以实际产出类型为 Dict[str, Any] | str。
        """
        timings = {}
        t_start = time.time()

        messages = []
        if conversation_history:
            recent_history = conversation_history[-8:]
            messages.extend(recent_history)
        messages.append({"role": "user", "content": question})

        # 验证消息角色序列：确保以 user 结尾且无连续 user
        messages = self._validate_message_roles(messages)

        # 判断是否与知识库相关
        kb_related = self.is_kb_related(question)
        query_type = self.classify_query(question) if kb_related else QueryType.UNKNOWN
        timings["classify"] = round((time.time() - t_start) * 1000)

        if not kb_related:
            # 闲聊模式：直接 LLM 流式回答
            logger.info(f"闲聊模式 | 问题: {question[:60]}...")
            timings["total"] = round((time.time() - t_start) * 1000)
            yield {"type": "meta", "from_kb": False, "query_type": "chitchat", "chunks": [], "timing": timings}
            yield from self.llm.chat_stream(
                messages=messages,
                system_prompt=SYSTEM_PROMPT_CHITCHAT,
            )
            return

        # ===== 知识库相关问题的 RAG 流程 =====
        self._ensure_knowledge_base()

        t_retrieve = time.time()
        retrieve_results = self.hybrid_retriever.retrieve(
            query=question, top_k=top_k,
            use_cache=self.enable_cache,
        )
        timings["retrieve"] = round((time.time() - t_retrieve) * 1000)

        if not retrieve_results:
            # 检索为空时仍调用 LLM，让模型基于对话历史或通用知识尝试回答
            logger.info(f"检索无结果，尝试 LLM 基于对话历史回答: {question[:60]}...")
            timings["total"] = round((time.time() - t_start) * 1000)
            yield {"type": "meta", "from_kb": True, "query_type": query_type.value, "chunks": [], "timing": timings}
            yield from self.llm.chat_stream(
                messages=messages,
                system_prompt=SYSTEM_PROMPT_CHITCHAT,
            )
            return

        t_rerank = time.time()
        if rerank and len(retrieve_results) > 3:
            retrieve_results = self.reranker.rerank(
                query=question, candidates=retrieve_results,
            )
        timings["rerank"] = round((time.time() - t_rerank) * 1000)

        context = self._build_context(retrieve_results)
        system_prompt = self._select_prompt(query_type, context)

        # 先 yield 检索结果的元数据（含计时信息）
        chunks_info = [
            {
                "artifact_name": c.artifact_name,
                "chunk_type": c.chunk_type,
                "score": round(s, 4),
            }
            for c, s in retrieve_results
        ]
        timings["total"] = round((time.time() - t_start) * 1000)
        yield {"type": "meta", "from_kb": True, "query_type": query_type.value, "chunks": chunks_info, "timing": timings}

        # 再 yield 流式回答
        yield from self.llm.chat_stream(
            messages=messages,
            system_prompt=system_prompt,
        )

    def verify_answer_grounding(self, answer: str, context: str) -> Dict[str, Any]:
        """
        验证 LLM 回答是否基于检索到的上下文（防幻觉检查）

        检查逻辑：
          1. 从上下文中提取所有被引用的来源名称
          2. 检查回答中提到的名称是否都在上下文中
          3. 如果回答中提到不在上下文中的名称，标记为可疑

        Returns:
            {"passed": bool, "reason": str, "mentioned": [...], "missing": [...]}
        """
        import re

        # 从上下文中提取来源名称（支持多种格式： 【】、**加粗**、【】、「」、《》）
        # bug-019 修复：增加 **加粗**、「」、《》 等格式支持
        context_names = set()
        for match in re.finditer(r"【(.+?)】|\*\*(.+?)\*\*|[「『](.+?)[」』]|[《](.+?)[》]", context):
            name = next(g for g in match.groups() if g is not None).strip()
            if name and len(name) >= 2:
                context_names.add(name)

        # 从回答中提取可能的名称（匹配 **加粗**、「」、《》 等内容）
        # bug-019 修复：增加 《》 格式支持
        # bug-027 修复：添加 re.DOTALL 标志，支持跨行名称匹配
        answer_names = set()
        for match in re.finditer(r'\*\*(.+?)\*\*|[「『](.+?)[」』]|[《](.+?)[》]', answer, re.DOTALL):
            name = next(g for g in match.groups() if g is not None).strip()
            if name and len(name) >= 2:
                answer_names.add(name)

        if not answer_names:
            return {"passed": True, "reason": "回答中未提及具体名称", "mentioned": [], "missing": []}

        missing = [n for n in answer_names if n not in context_names]
        if missing:
            return {
                "passed": False,
                "reason": f"回答中提到了以下不在上下文中的内容: {missing}",
                "mentioned": list(answer_names),
                "missing": missing,
            }

        return {
            "passed": True,
            "reason": "所有提及的内容均在上下文中",
            "mentioned": list(answer_names),
            "missing": [],
        }

    def get_stats(self) -> Dict[str, Any]:
        """获取知识库统计信息"""
        try:
            collection_info = self.vector_store.client.get_collection(
                self.vector_store.collection_name
            )
            return {
                "collection": self.vector_store.collection_name,
                "vector_count": collection_info.points_count,
                "vector_size": collection_info.config.params.vectors.size,
                "distance": str(collection_info.config.params.distance),
            }
        except Exception as e:
            return {"error": str(e)}