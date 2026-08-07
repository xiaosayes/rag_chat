"""
配置管理模块
支持从环境变量、.env 文件、Pydantic Settings 加载配置
"""

import os
from pathlib import Path
from typing import Literal, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """应用配置"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def __repr__(self) -> str:
        """安全地显示配置信息，屏蔽敏感字段"""
        safe = {}
        for k, v in self.__dict__.items():
            if "api_key" in k.lower() or "secret" in k.lower():
                safe[k] = "***" if v else ""
            else:
                safe[k] = v
        return f"Settings({safe})"

    def __str__(self) -> str:
        return self.__repr__()

    # ========== 阿里云百炼 ==========
    dashscope_api_key: str = Field(
        default_factory=lambda: os.environ.get("DASHSCOPE_API_KEY", ""),
        description="阿里云百炼 API Key",
    )

    # ========== Embedding 配置 ==========
    embedding_model_name: str = Field(
        default="text-embedding-v4",
        description="Embedding 模型名称（百炼，bug-110 已从 v3 升级至 v4）",
    )
    embedding_dimension: int = Field(
        default=1024,
        description="Embedding 向量维度",
    )
    embedding_batch_size: int = Field(
        default=10,
        description="Embedding 批处理大小（单请求上限 10 条，bug-096）",
    )

    # ========== LLM 配置 ==========
    llm_model_name: str = Field(
        default="qwen-plus",
        description="LLM 模型名称（qwen-plus 性价比最优，qwen-max 旗舰级）",
    )
    llm_temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="生成温度",
    )
    llm_max_tokens: int = Field(
        default=4096,
        description="最大生成 Token 数",
    )
    llm_top_p: float = Field(
        default=0.8,
        description="Top-p 采样",
    )
    # bug-106 修复：联网搜索总开关（默认关闭避免误扣费）。
    # 开启后由 rag_pipeline 按需自动启用：开放类/未知类问题或含时效关键词的问题
    # 自动联网，纯知识库事实问题不联网。
    llm_enable_search: bool = Field(
        default=False,
        description="是否启用联网搜索总开关（enable_search，按需自动启用；需百炼账号开通搜索能力）",
    )

    # ========== 意图理解（L1 语义 + L2 LLM 兜底）==========
    # 分层意图分类：L0 规则（is_kb_related）→ L1 向量语义 → L2 LLM 兜底
    intent_semantic_enabled: bool = Field(
        default=True,
        description="启用 L1 向量语义意图分类（复用 Embedding 缓存，语义泛化优于纯规则）",
    )
    intent_semantic_threshold: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        description="L1 语义分类置信度阈值（余弦相似度；低于该值且 L2 开启时走 LLM 兜底）",
    )
    intent_llm_fallback_enabled: bool = Field(
        default=True,
        description="启用 L2 LLM 意图分类兜底（仅 L1 低置信度时调用，按次计费）",
    )

    # ========== 向量数据库 ==========
    vector_db_type: Literal["qdrant", "chroma"] = Field(
        default="qdrant",
        description="向量数据库类型",
    )
    qdrant_host: str = Field(
        default="localhost",
        description="Qdrant 主机地址",
    )
    qdrant_port: int = Field(
        default=6333,
        description="Qdrant 端口",
    )
    qdrant_collection_name: str = Field(
        default="cultural_relics",
        description="Qdrant 集合名称",
    )
    qdrant_memory_mode: bool = Field(
        default=False,
        description="Qdrant 全内存模式（True=使用持久化快照，重启后数据不丢失，查询最快）",
    )

    # ========== 检索配置 ==========
    retriever_top_k: int = Field(
        default=10,
        description="检索 Top-K",
    )
    retriever_hybrid_weight: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="混合检索中语义检索的权重（BM25 权重 = 1 - 此值）",
    )

    # ========== 重排序 ==========
    reranker_enabled: bool = Field(
        default=True,
        description="是否启用重排序",
    )
    reranker_top_k: int = Field(
        default=5,
        description="重排序后保留的 Top-K",
    )
    reranker_model: str = Field(
        default="qwen3-reranker-4b",
        description="重排序模型（qwen3-reranker-4b 推荐，qwen3-reranker-8b 更准）",
    )

    # ========== 数据路径 ==========
    project_root: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent,
        description="项目根目录",
    )
    raw_data_dir: Path = Field(
        default=Path("data/raw"),
        description="原始数据目录",
    )
    processed_data_dir: Path = Field(
        default=Path("data/processed"),
        description="处理后的数据目录",
    )

    # ========== 日志 ==========
    log_level: str = Field(
        default="INFO",
        description="日志级别",
    )

    @property
    def raw_data_path(self) -> Path:
        return self.project_root / self.raw_data_dir

    @property
    def processed_data_path(self) -> Path:
        return self.project_root / self.processed_data_dir

    def validate_api_key(self) -> bool:
        """验证 API Key 是否已配置"""
        if not self.dashscope_api_key:
            raise ValueError(
                "DASHSCOPE_API_KEY 未设置！\n"
                "请通过环境变量或 .env 文件设置：\n"
                "  export DASHSCOPE_API_KEY='your-api-key'  # Linux/Mac\n"
                "  set DASHSCOPE_API_KEY=your-api-key       # Windows CMD\n"
                "  $env:DASHSCOPE_API_KEY='your-api-key'    # Windows PowerShell"
            )
        return True


# 全局单例
settings = Settings()