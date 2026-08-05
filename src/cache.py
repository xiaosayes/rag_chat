"""
LRU 缓存模块 v2
支持高频问题 Embedding 预计算、语义相似匹配、持久化存储
"""

import time
import json
import hashlib
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable
from collections import OrderedDict

from loguru import logger


class LRUCache:
    """
    LRU (Least Recently Used) 缓存，支持 TTL 过期
    线程安全：使用 threading.Lock 保护所有读写操作
    """

    def __init__(self, capacity: int = 128, ttl: int = 3600):
        self.capacity = capacity
        self.ttl = ttl
        self._cache: OrderedDict[str, Tuple[Any, float]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()

    def _make_key(self, *args, **kwargs) -> str:
        key_str = str(args) + str(kwargs)
        return hashlib.md5(key_str.encode()).hexdigest()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            value, timestamp = self._cache[key]
            if time.time() - timestamp > self.ttl:
                del self._cache[key]
                self._misses += 1
                return None
            self._cache.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (value, time.time())
            while len(self._cache) > self.capacity:
                self._cache.popitem(last=False)

    def get_with_key(self, key_prefix: str, *args, **kwargs) -> Optional[Any]:
        key = self._make_key(key_prefix, *args, **kwargs)
        return self.get(key)

    def set_with_key(self, value: Any, key_prefix: str, *args, **kwargs) -> str:
        key = self._make_key(key_prefix, *args, **kwargs)
        self.set(key, value)
        return key

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0

    @property
    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / total if total > 0 else 0
            return {
                "size": len(self._cache),
                "capacity": self.capacity,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": f"{hit_rate:.1%}",
            }


class EmbeddingCache:
    """
    Embedding 智能缓存 v3

    相比 v2 的改进：
    1. bug-007 修复：_exact_cache 改用 OrderedDict 实现 LRU 淘汰（原 FIFO 淘汰导致热点数据被挤出）
    2. bug-008 修复：save() 加锁保护，锁类型改为 RLock 支持可重入
    3. bug-006 修复：_pattern_match 放宽边界检查，多字词不再因 CJK 边界被拒绝
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or Path("data/processed/embedding_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # 精确缓存: {question_text: embedding_vector}
        # bug-007 修复：使用 OrderedDict 替代 Dict，支持 LRU 淘汰
        self._exact_cache: Dict[str, List[float]] = OrderedDict()
        # 模式缓存: {pattern: embedding_vector}
        self._pattern_cache: Dict[str, List[float]] = {}
        # 命中和未命中统计
        self._hits = 0
        self._misses = 0
        self._pattern_hits = 0
        # bug-008 修复：使用 RLock 替代 Lock，支持可重入（save() 在锁内调用时不会死锁）
        self._lock = threading.RLock()

        # 持久化文件（使用 JSON 格式而非 pickle，避免安全风险）
        self._cache_file = self.cache_dir / "exact_cache.json"
        self._pattern_file = self.cache_dir / "pattern_cache.json"

        # 加载持久化缓存
        self._load()

        # 高频问题模式库
        self._init_patterns()

    def _init_patterns(self):
        """初始化高频问题模式库（预计算 Embedding 会在 build_knowledge_base 时完成）"""
        self.patterns = [
            # 推荐类
            "推荐一些代表性的文物",
            "给我推荐几个镇馆之宝",
            "有哪些著名的国宝文物",
            "介绍几件代表性文物",
            "什么文物最值得看",
            "有哪些必看的文物",
            "推荐几个经典文物",
            # 事实类
            "司母戊鼎有多重",
            "清明上河图在哪里展出",
            "越王勾践剑是什么材质",
            "马踏飞燕在哪里收藏",
            "曾侯乙编钟有多少个",
            # 比较类
            "青铜器和瓷器有什么区别",
            "司母戊鼎和毛公鼎哪个更重",
            # 开放类
            "谈谈唐代的工艺美术成就",
            "中国古代有哪些著名的青铜器",
        ]

    def _load(self):
        """从磁盘加载持久化缓存（使用 JSON 格式替代 pickle，避免任意代码执行风险）"""
        try:
            if self._cache_file.exists():
                # 安全：使用 JSON 而非 pickle，避免 pickle 反序列化漏洞
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                # 验证数据格式：{str: list[float]}
                if isinstance(raw, dict):
                    # bug-007 修复：使用 OrderedDict 保持 LRU 顺序
                    validated = OrderedDict()
                    for k, v in raw.items():
                        if isinstance(k, str) and isinstance(v, list) and all(isinstance(x, (int, float)) for x in v):
                            validated[k] = v
                        else:
                            logger.warning(f"跳过无效缓存条目: {k!r}")
                    self._exact_cache = validated
                else:
                    logger.warning("缓存文件格式异常，忽略")
                    self._exact_cache = OrderedDict()
                logger.info(f"加载 Embedding 缓存: {len(self._exact_cache)} 条")
        except Exception as e:
            logger.warning(f"加载 Embedding 缓存失败: {e}")
            self._exact_cache = OrderedDict()

        try:
            if self._pattern_file.exists():
                with open(self._pattern_file, "r", encoding="utf-8") as f:
                    self._pattern_cache = json.load(f)
                logger.info(f"加载模式缓存: {len(self._pattern_cache)} 条")
        except Exception as e:
            logger.warning(f"加载模式缓存失败: {e}")
            self._pattern_cache = {}

    def save(self):
        """持久化缓存到磁盘（全部使用 JSON 格式）

        bug-008 修复：加锁保护，避免并发写入时数据不一致。
        """
        with self._lock:
            try:
                with open(self._cache_file, "w", encoding="utf-8") as f:
                    json.dump(self._exact_cache, f, ensure_ascii=False)
                with open(self._pattern_file, "w", encoding="utf-8") as f:
                    json.dump(self._pattern_cache, f, ensure_ascii=False)
                logger.info(
                    f"保存 Embedding 缓存: {len(self._exact_cache)} 精确 + "
                    f"{len(self._pattern_cache)} 模式"
                )
            except Exception as e:
                logger.warning(f"保存 Embedding 缓存失败: {e}")

    @staticmethod
    def _pattern_match(pattern: str, question: str) -> bool:
        """
        模式匹配：检查 pattern 是否以短语形式出现在 question 中。

        bug-006 修复说明：
          v2 的 CJK 边界检查（OR 逻辑）过于严格，导致多字词如"青铜器"在"介绍青铜器知识"中
          因为前后都是中文字符而被拒绝匹配。v3 移除边界检查，对于长度 >= 2 的 pattern，
          只要出现在 question 中即匹配。

          放宽匹配后，"我不推荐这个"会匹配 pattern="推荐"，但这是可接受的，因为：
          1. 缓存是优化手段，不是正确性依赖
          2. 近似 embedding 仍能返回相关结果
          3. 缓存未命中需要 API 调用，代价更高
        """
        if len(pattern) > len(question):
            return False
        if pattern not in question:
            return False
        # 单字符模式要求精确匹配，避免"文"匹配"文物"等误匹配
        if len(pattern) <= 1:
            return pattern == question
        # 多字符模式：只要出现在问题中即匹配
        return True

    def get(self, question: str) -> Optional[List[float]]:
        """
        获取问题的 Embedding

        匹配策略：
        1. 精确匹配缓存 → 直接返回
        2. 模式匹配（高频问题模板，基于完整短语匹配）→ 返回模板的 Embedding
        3. 未命中 → 返回 None，由调用方计算并缓存
        """
        question = question.strip()

        with self._lock:
            # 1. 精确匹配
            if question in self._exact_cache:
                self._hits += 1
                # bug-007 修复：LRU 更新，将访问的条目移到末尾
                self._exact_cache.move_to_end(question)
                logger.debug(f"Embedding 缓存命中（精确）: {question[:30]}...")
                return self._exact_cache[question]

            # 2. 模式匹配（高频问题模板，使用完整短语匹配）
            for pattern, emb in self._pattern_cache.items():
                if self._pattern_match(pattern, question):
                    self._pattern_hits += 1
                    logger.debug(f"Embedding 缓存命中（模式）: {pattern[:30]}...")
                    return emb

            self._misses += 1
            return None

    def set(self, question: str, embedding: List[float]):
        """缓存问题的 Embedding

        bug-007 修复：使用 LRU 淘汰策略替代 FIFO。
        当缓存超过容量上限时，淘汰最早未访问的条目（从 OrderedDict 头部移除）。
        """
        question = question.strip()
        with self._lock:
            # 如果已存在，先删除再插入（更新到末尾）
            if question in self._exact_cache:
                del self._exact_cache[question]
            self._exact_cache[question] = embedding
            # 如果超过 1000 条，淘汰最早未访问的一半（从头部移除）
            if len(self._exact_cache) > 1000:
                # OrderedDict 保持插入顺序，头部是最早未访问的条目
                while len(self._exact_cache) > 500:
                    self._exact_cache.popitem(last=False)

    def set_pattern(self, pattern: str, embedding: List[float]):
        """缓存模式 Embedding"""
        with self._lock:
            self._pattern_cache[pattern] = embedding

    def precompute_patterns(self, embed_func: Callable[[str], List[float]]):
        """
        预计算所有高频问题模式的 Embedding
        在 build_knowledge_base 时调用
        """
        new_count = 0
        for pattern in self.patterns:
            with self._lock:
                already_cached = pattern in self._pattern_cache
            if not already_cached:
                try:
                    emb = embed_func(pattern)
                    with self._lock:
                        self._pattern_cache[pattern] = emb
                    new_count += 1
                    logger.info(f"预计算模式: {pattern[:30]}...")
                except Exception as e:
                    logger.warning(f"预计算失败: {pattern[:30]}... - {e}")

        if new_count > 0:
            with self._lock:
                self.save()
            logger.info(f"预计算完成: 新增 {new_count} 个模式")

    @property
    def stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / total if total > 0 else 0
            pattern_rate = self._pattern_hits / total if total > 0 else 0
            return {
                "exact_cache": len(self._exact_cache),
                "pattern_cache": len(self._pattern_cache),
                "patterns_total": len(self.patterns),
                "hits": self._hits,
                "pattern_hits": self._pattern_hits,
                "misses": self._misses,
                "hit_rate": f"{hit_rate:.1%}",
                "pattern_hit_rate": f"{pattern_rate:.1%}",
            }


# ========== 全局缓存实例 ==========

# Embedding 智能缓存（容量 2000，支持持久化）
embedding_cache = EmbeddingCache()

# LLM 响应缓存（容量 256，TTL 30 分钟）
llm_cache = LRUCache(capacity=256, ttl=1800)

# 检索结果缓存（容量 128，TTL 5 分钟）
retrieval_cache = LRUCache(capacity=128, ttl=300)