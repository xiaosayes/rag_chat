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

    # ========== 讯飞语音识别 (ASR) ==========
    xfyun_app_id: str = Field(default="", description="讯飞开放平台 APP_ID（语音听写 IAT）")
    xfyun_api_key: str = Field(default="", description="讯飞开放平台 API_KEY")
    xfyun_api_secret: str = Field(default="", description="讯飞开放平台 API_SECRET")
    asr_language: str = Field(default="zh_cn", description="识别语言（zh_cn 普通话）")
    asr_accent: str = Field(default="mandarin", description="口音（mandarin 普通话）")
    asr_vad_eos: int = Field(default=2000, ge=0, description="服务端静音检测 ms（信飞 vad 不主动结束，仅作兜底；主结束方式为客户端静音检测）")
    asr_max_duration: int = Field(default=30, ge=1, description="最长录音秒数兜底（超时强制结束）")
    asr_sample_rate: int = Field(default=16000, description="IAT 采样率（16k PCM）")
    asr_silence_threshold: int = Field(default=500, ge=0, description="静音 RMS 阈值（低于此值的音频块视为静音，用于自动结束转写）")
    asr_silence_blocks: int = Field(default=4, ge=1, description="连续静音块数（每块约 0.5s，4 块 ≈ 停顿 2s 自动结束）")
    asr_dict_dir: Path = Field(default=Path("data/voice"), description="多音字/热词配置目录")

    # ========== 语音助手（audit-ASR）：唤醒 + VAD + 双计时 + 打断 ==========
    # 默认关闭：关闭时保持 bug-121 手动点击录音语义（现有测试契约）；服务器 .env 置 true 开启
    voice_assist_enabled: bool = Field(default=False, description="语音助手总开关（唤醒词/双计时/打断；关闭=手动点击录音）")
    asr_wake_words: str = Field(default="你好小虎", description="唤醒词，逗号分隔多个；asr_dict.json 的 wake_words 可项目级覆盖")
    asr_wake_greeting: str = Field(default="您好，我是小虎，请问有什么可以帮您？", description="唤醒应答语（合成一次缓存复用）")
    asr_initial_wait_s: float = Field(default=8.0, gt=0, description="播报结束/唤醒后等待开口的初始窗口秒数（超时无语音→回待机）")
    asr_extend_wait_s: float = Field(default=2.0, gt=0, description="每段语音结束后的提问静默判定秒数（期间再说话则续接为同一问题）")
    # silero VAD 参数（audit-ASR 需求2，用户标定）
    vad_threshold: float = Field(default=0.5, ge=0, le=1, description="语音概率阈值")
    vad_min_speech_ms: int = Field(default=400, ge=0, description="最短有效语音 ms（过滤'嗯''啊'；达到即提前确认语音开始）")
    vad_min_silence_ms: int = Field(default=800, ge=0, description="语音内连续静音多久判定为段结束 ms")
    vad_speech_pad_ms: int = Field(default=200, ge=0, description="语音段前后补偿 ms")
    vad_max_speech_s: int = Field(default=15, ge=1, description="单段语音最大秒数（强制切段，防卡死）")
    silero_vad_model_path: str = Field(default="", description="silero_vad.onnx 路径覆盖（默认自动定位 silero_vad 包内置模型）")

    # ========== 语音合成 (TTS) ==========
    tts_enabled: bool = Field(default=True, description="语音播报总开关（默认开）")
    tts_model: str = Field(default="cosyvoice-v3-flash", description="TTS 模型（一期；二期真人音色用 cosyvoice-v3.5-flash）")
    tts_voice: str = Field(default="", description="TTS 音色（默认小男孩，真实 API 确认后填入）")
    tts_chunk_chars: int = Field(default=1000, ge=100, description="TTS 长文本分段长度（字符）")
    tts_accum_chars: int = Field(default=60, ge=1, description="后续喂入批量阈值（字）：攒够该字数的完整句（句末标点切）才喂入。audit-TTS 第五轮：streaming_call 边界烙静默/韵律断层，20 字小块致每回答 4-5 处断句 → 批量化减边界 + 只切句末")
    tts_first_batch_blocks: int = Field(default=5, ge=1, description="[已弃用] bug-121 分段合成的首播门；audit-TTS 单会话流式改造后不再使用（保留字段兼容旧 .env）")
    # audit-TTS：单会话流式合成（首句 ≤1s + 全程无停顿）
    tts_first_fragment_chars: int = Field(default=8, ge=1, description="[已弃用] 第五轮断句修复后首播单元改由 _take_first_unit 决定（句末标点优先，逗号/80 字兜底）；保留字段兼容旧 .env")
    tts_speech_rate: float = Field(default=1.1, ge=0.5, le=2.0, description="TTS 语速倍率（CosyVoice speech_rate，0.5~2.0）：用户反馈原速不紧不慢，1.1 加快 10%")
    tts_batch_seconds: float = Field(default=2.0, gt=0, description="播报标准段时长（秒音频）：连续 AAC 流按帧界切片发布。audit-TTS：0.9s 段在高 RTT 客户端下请求周期追不平消费（拉 playlist→拉段串行，2 次 RTT/段）→ 2.0s 减半请求频率；首播爬坡 0.4/0.6/0.8 不受影响；TD patch clamp 到 2")
    tts_first_batch_seconds: float = Field(default=0.4, gt=0, description="首播批次时长（秒音频）：小批次快速开播")
    tts_stream_watchdog_seconds: float = Field(default=15.0, gt=0, description="流式会话看门狗：有待播文本但无音频超过该时长 → 重建会话（最多 2 次）")

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
    llm_relevance_check_enabled: bool = Field(
        default=True,
        description=(
            "启用 LLM 相关性语义确认（bug-116 补8 结果侧闸门：重排低分区间时"
            "由 LLM 判断检索结果能否回答问题，避免绝对分数阈值跨知识库/模型不稳定）"
        ),
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