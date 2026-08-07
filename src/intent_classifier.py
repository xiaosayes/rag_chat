"""
意图理解模块：L1 向量语义分类 + L2 LLM 兜底

分层意图理解设计（配合 rag_pipeline 中的 L0 规则层 is_kb_related）：
  L0 规则（is_kb_related）：零成本闲聊快通道，保留现有实现（src/rag_pipeline.py）
  L1 语义（SemanticIntentClassifier）：用户问题 embedding 与各类别"原型问题"计算
      余弦相似度，最高分超过阈值即归类——语义泛化，覆盖规则层漏判/换说法问题
  L2 LLM（classify_with_llm）：L1 置信度低于阈值时调用 LLM 分类兜底，
      处理模糊意图；无 API Key / 调用失败 / 输出无法解析时返回 None，由规则层兜底

使用方式（由 RAGPipeline 集成）：
    classifier = SemanticIntentClassifier(embedding=..., min_confidence=0.55)
    intent, confidence = classifier.classify(question)   # L1
    intent = classify_with_llm(llm, question)            # L2（可选）
"""

from __future__ import annotations

import math
import threading
from typing import Dict, List, Optional, Tuple

from loguru import logger

from src.cache import embedding_cache


# L2 LLM 意图分类提示词：要求只输出一个英文单词
LLM_INTENT_PROMPT = """判断以下用户问题的意图类型，只输出一个英文单词，不要输出任何解释或其他内容。

类型说明：
- recommendation：用户要求推荐、介绍、列举若干对象（如"推荐几个""有哪些""什么值得看""介绍一些"）
- factual：用户询问具体事实信息（数量、重量、时间、地点、材质、人物、事件等）
- comparison：用户要求比较、对比两个或多个事物之间的异同
- open_ended：用户要求分析、讨论、评价、谈谈看法、探究原因
- chitchat：问候寒暄、闲聊、与知识库无关的个人问题

用户问题：{question}

输出："""


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """计算两个向量的余弦相似度（维度不一致或零向量返回 0.0）"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class SemanticIntentClassifier:
    """L1 向量语义意图分类器

    原理：为每个意图类别准备若干"原型问题"，预计算其 embedding；
    用户问题 embedding 与所有原型计算余弦相似度，取最高分作为意图与置信度。

    原型向量懒加载（首次 classify 时计算，之后复用），并通过
    embedding.embed_query 走全局 EmbeddingCache 持久化——
    首次计算后重启进程命中磁盘缓存，查询零额外 API 成本。
    """

    # 各类别原型问题（领域无关，符合项目"代码泛化"约定；
    # 类别与 rag_pipeline.QueryType 的 recommendation/factual/comparison/
    # open_ended/chitchat 一一对应）
    INTENT_PROTOTYPES: Dict[str, List[str]] = {
        "recommendation": [
            "推荐几个不错的",
            "有哪些值得推荐的",
            "给我推荐一些好的",
            "什么最值得看",
            "介绍几个有代表性的",
            "有哪些必看的内容",
            "推荐一下经典的选择",
        ],
        "factual": [
            "这个有多重",
            "在哪里展出",
            "是什么材质的",
            "什么时候建造的",
            "有多少个",
            "谁创作的",
            "位于什么地方",
            "是什么时候的事情",
        ],
        "comparison": [
            "这两个有什么区别",
            "哪个更好",
            "这两个有什么不同",
            "比较一下这两者",
            "哪个更适合我",
            "有什么差异",
            "哪一个更值得选择",
        ],
        "open_ended": [
            "谈谈你的看法",
            "分析一下这个问题",
            "讨论一下这件事的意义",
            "如何看待这个现象",
            "为什么会有这样的发展",
            "评价一下这件事",
            "探讨一下背后的原因",
        ],
        "chitchat": [
            "今天天气怎么样",
            "你好，很高兴认识你",
            "谢谢你",
            "再见，下次聊",
            "你是谁",
            "你会做什么",
            "我很开心",
            "你最近怎么样",
        ],
    }

    # 需联网搜索原型问题（bug-116 补8：联网决策从"措辞穷举"升级为"语义判断"）
    # 语义上依赖最新/动态/时效信息的问法，与具体措辞无关：
    #   - 用户问"大唐妖探啥时上"（无"上映"二字）不命中 TEMPORAL_KEYWORDS，
    #     但与"什么时候上映"语义相近 → 应命中此集合触发联网。
    # 与 INTENT_PROTOTYPES 独立：不改变 QueryType 语义，仅用于
    # _should_enable_search 的语义层补充（classify_needs_search）。
    NEEDS_SEARCH_PROTOTYPES: List[str] = [
        "什么时候上映",
        "最近有什么新动态",
        "今天天气怎么样",
        "现在几点",
        "门票现在多少钱",
        "最近有什么活动",
        "昨天发生了什么",
        "今年的新展是什么",
        "最新的消息是什么",
        "什么时候开放",
        "现在几点关门",
        "最近有什么特展",
        "现在的价格是多少",
        "今年的展览安排",
        "什么时候举办",
        # bug-116 补8 补充：口语化日期问句（语义近似"几号/哪天"）
        "电影几号上映",
        "哪天开播",
        "多久才上映",
        "什么时候开始",
        "几点开始",
    ]

    def __init__(
        self,
        embedding,
        enable_cache: bool = True,
        min_confidence: float = 0.55,
    ):
        self.embedding = embedding
        self.enable_cache = enable_cache
        self.min_confidence = min_confidence
        self._prototype_vectors: Optional[Dict[str, List[List[float]]]] = None
        self._needs_search_vectors: Optional[List[List[float]]] = None
        self._lock = threading.Lock()

    def warmup(self) -> None:
        """预计算所有原型问题的 embedding（构建/启动时调用，后续查询无额外 API 开销）"""
        self._get_prototype_vectors()
        self._get_needs_search_vectors()

    def _get_needs_search_vectors(self) -> List[List[float]]:
        """懒加载需联网搜索原型向量（线程安全；复用全局 EmbeddingCache 持久化）

        与 _get_prototype_vectors 相同的缓存策略：优先读全局缓存，
        未缓存的用 embed_batch 批量计算并写入 pattern_cache。
        """
        if self._needs_search_vectors is not None:
            return self._needs_search_vectors
        with self._lock:
            if self._needs_search_vectors is not None:
                return self._needs_search_vectors

            cached_map: Dict[str, List[float]] = {}
            uncached: List[str] = []
            for proto in self.NEEDS_SEARCH_PROTOTYPES:
                vec = embedding_cache.get(proto)
                if vec:
                    cached_map[proto] = vec
                else:
                    uncached.append(proto)
            if uncached:
                vectors = self._batch_embed(uncached)
                for proto, vec in zip(uncached, vectors):
                    if vec:
                        cached_map[proto] = vec
                        try:
                            embedding_cache.set_pattern(proto, vec)
                        except Exception as e:
                            logger.warning(f"需联网原型写入模式缓存失败: {e}")
            self._needs_search_vectors = [
                cached_map[p] for p in self.NEEDS_SEARCH_PROTOTYPES
                if p in cached_map
            ]
            logger.info(
                f"需联网搜索原型向量就绪: {len(self._needs_search_vectors)} 个"
            )
            return self._needs_search_vectors

    def classify_needs_search(self, question: str) -> Tuple[bool, float]:
        """语义判断问题是否需要联网搜索（bug-116 补8）

        与意图分类并行但独立：问题 embedding 与 NEEDS_SEARCH_PROTOTYPES
        计算余弦相似度，最高分 >= min_confidence 视为需要联网。
        措辞无关（"啥时上"与"什么时候上映"语义相近均命中），
        解决 TEMPORAL_KEYWORDS 关键词穷举的泛化缺陷。

        Returns:
            (needs_search, confidence)：embedding 失败/空问题返回 (False, 0.0)
        """
        q = (question or "").strip()
        if not q:
            return False, 0.0
        try:
            q_vec = self.embedding.embed_query(q, use_cache=self.enable_cache)
        except Exception as e:
            logger.warning(f"需联网语义判断 embedding 失败: {e}")
            return False, 0.0
        if not q_vec:
            return False, 0.0

        best_score = -1.0
        for pv in self._get_needs_search_vectors():
            score = cosine_similarity(q_vec, pv)
            if score > best_score:
                best_score = score
        logger.debug(
            f"需联网语义判断: {best_score >= self.min_confidence} (置信度 {best_score:.3f}) | 问题: {q[:30]}"
        )
        return best_score >= self.min_confidence, best_score

    def _get_prototype_vectors(self) -> Dict[str, List[List[float]]]:
        """懒加载原型向量（线程安全；embedding 失败的原型跳过并告警）

        性能优化（bug-113 首字延迟）：
          1. 优先从全局 EmbeddingCache 读取已持久化的原型（重启后零 API 调用）；
          2. 未缓存的原型用 embed_batch 批量计算（37 个原型从串行 ~9.5s 降到 ~1s），
             结果写入 pattern_cache（该缓存不淘汰、持久化，避免被 LRU 挤出后重算）；
          3. embed_batch 不可用/失败时回退逐个 embed_query。
        """
        if self._prototype_vectors is not None:
            return self._prototype_vectors
        with self._lock:
            if self._prototype_vectors is not None:
                return self._prototype_vectors

            # 1) 收集所有原型，优先读全局缓存
            all_protos: List[str] = []
            for protos in self.INTENT_PROTOTYPES.values():
                all_protos.extend(protos)
            cached_map: Dict[str, List[float]] = {}
            uncached: List[str] = []
            for proto in all_protos:
                vec = embedding_cache.get(proto)
                if vec:
                    cached_map[proto] = vec
                else:
                    uncached.append(proto)

            # 2) 未缓存的原型批量计算（embed_batch，~10 倍提速），失败回退逐个
            if uncached:
                vectors = self._batch_embed(uncached)
                for proto, vec in zip(uncached, vectors):
                    if vec:
                        cached_map[proto] = vec
                        # 写入模式缓存（不淘汰、持久化），避免 LRU 挤出后重算
                        try:
                            embedding_cache.set_pattern(proto, vec)
                        except Exception as e:
                            logger.warning(f"意图原型写入模式缓存失败: {e}")

            # 3) 组装（embedding 失败的原型跳过）
            vectors_by_intent: Dict[str, List[List[float]]] = {}
            for intent, protos in self.INTENT_PROTOTYPES.items():
                vectors_by_intent[intent] = [
                    cached_map[p] for p in protos if p in cached_map
                ]
            self._prototype_vectors = vectors_by_intent
            total = sum(len(v) for v in vectors_by_intent.values())
            logger.info(
                f"意图原型向量就绪: {total} 个原型 / {len(vectors_by_intent)} 类"
            )
            return vectors_by_intent

    def _batch_embed(self, texts: List[str]) -> List[Optional[List[float]]]:
        """批量计算 embedding（优先 embed_batch，失败回退逐个 embed_query）"""
        try:
            if hasattr(self.embedding, "embed_batch"):
                return self.embedding.embed_batch(texts)
        except Exception as e:
            logger.warning(f"意图原型 embed_batch 失败，回退逐个计算: {e}")
        # 回退：逐个 embed_query（保持与测试 mock 兼容）
        result: List[Optional[List[float]]] = []
        for text in texts:
            try:
                vec = self.embedding.embed_query(text, use_cache=self.enable_cache)
                result.append(vec if vec else None)
            except Exception as e:
                logger.warning(f"意图原型 embedding 失败: {text[:20]}... - {e}")
                result.append(None)
        return result

    def classify(self, question: str) -> Tuple[Optional[str], float]:
        """对问题进行语义意图分类（L1）

        Returns:
            (intent, confidence)：
              intent ∈ {"recommendation", "factual", "comparison",
                        "open_ended", "chitchat"}
              embedding 失败 / 空问题时返回 (None, 0.0)
        """
        q = (question or "").strip()
        if not q:
            return None, 0.0
        try:
            q_vec = self.embedding.embed_query(q, use_cache=self.enable_cache)
        except Exception as e:
            logger.warning(f"意图分类 embedding 失败: {e}")
            return None, 0.0
        if not q_vec:
            return None, 0.0

        best_intent: Optional[str] = None
        best_score = -1.0
        for intent, prototypes in self._get_prototype_vectors().items():
            for pv in prototypes:
                score = cosine_similarity(q_vec, pv)
                if score > best_score:
                    best_score = score
                    best_intent = intent
        if best_intent is None:
            return None, 0.0
        logger.debug(
            f"语义意图分类: {best_intent} (置信度 {best_score:.3f}) | 问题: {q[:30]}"
        )
        return best_intent, best_score


def classify_with_llm(llm, question: str) -> Optional[str]:
    """L2：LLM 意图分类兜底（L1 低置信度时调用）

    无 API Key / LLM 调用失败 / 输出无法解析 → 返回 None（由规则层兜底）。
    返回值为意图字符串（"recommendation" 等），由调用方映射到 QueryType。
    """
    from src.config import settings

    if not settings.dashscope_api_key:
        logger.debug("DASHSCOPE_API_KEY 未配置，跳过 LLM 意图分类")
        return None
    try:
        answer = llm.chat(
            messages=[{
                "role": "user",
                "content": LLM_INTENT_PROMPT.format(
                    question=(question or "")[:200]
                ),
            }],
            system_prompt=None,
        )
    except Exception as e:
        logger.warning(f"LLM 意图分类失败: {e}")
        return None
    if not answer:
        return None
    # 取第一行并小写，容忍额外标点/前后缀（如 "comparison。" / "Intent: factual"）
    first_line = answer.strip().splitlines()[0].strip().lower()
    for key in ("recommendation", "factual", "comparison", "open_ended", "chitchat"):
        if key in first_line:
            return key
    logger.warning(f"LLM 意图分类输出无法解析: {answer[:50]!r}")
    return None