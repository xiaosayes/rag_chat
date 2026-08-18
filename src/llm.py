"""
LLM 模块
封装阿里云百炼 Qwen 系列模型的 API 调用
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional

from loguru import logger
from dashscope import Generation

from src.config import settings
from src.cache import llm_cache
from src.utils import FatalAPIError, strip_emoji


# bug-105 修复：注入当前日期并禁止知识截止声明。
# 模型（qwen 系列）基于训练数据知识回答时效性问题时，习惯声明
# "截止到 2024 年 7 月 / 我的知识截止于…"，与用户当前时间不符。
# 在 system prompt 统一注入当前日期并给出时效性回答引导。
# bug-117 修复：明确要求相对日期（明天/后天/几天后）必须以今天为基准
# 准确计算；无法确认时宁可只给具体日期，避免"8月22日上映（就在后天）"
# 这类日期计算幻觉（实测 8月7日问 8月22日上映，LLM 误称"后天"）。
_CURRENT_DATE_NOTE = (
    "\n\n【当前日期】今天是{date}。回答时效性问题时以当前日期为准；"
    "不要声明\"截止到XX年XX月\"、\"我的知识截止于XX\"或\"截至XX年\"等表述；"
    "若信息时效无法确认，应说明建议以官方最新发布为准。"
    "涉及\"明天/后天/几天后/几天前\"等相对日期时，必须严格以今天\"{date}\""
    "为基准计算相隔天数；若对计算结果没有把握，宁可只给出具体日期"
    "（如\"X月X日\"），不要给出可能算错的相对表述。"
)

# bug-106 修复：启用联网搜索时追加引导——联网结果仅补充时效信息，
# 文物知识类信息仍以 RAG 参考信息为准，避免与网上信息冲突。
# bug-116 再修复（服务器实证）：原措辞"联网仅补充时效信息+以参考信息为准"
# 会被 factual prompt 的"参考信息不足请如实说明"压制，导致时效性问题
# （如电影什么时候上映）被家博会无关参考信息拒答。改为明确优先级：
# 时效/动态/时间类信息以联网结果为准，参考信息缺失不妨碍回答。
_SEARCH_GUIDE_NOTE = (
    "\n\n【联网搜索】本轮已启用联网搜索。联网结果可用于回答时效性、"
    "动态性、时间性信息（如最新动态、上映时间、展览、开放情况、门票、活动等）；"
    "此类问题若参考信息中缺失或与联网结果不一致，请以联网结果为准回答，"
    "不要因参考信息缺失而拒绝回答。文物知识类信息（如年代、形制、工艺等）"
    "仍以提供的参考信息为准，不要与参考信息冲突。"
)


def _build_current_date_note() -> str:
    """生成当前日期说明（2026年1月5日 格式，跨平台）"""
    now = datetime.now()
    return _CURRENT_DATE_NOTE.format(
        date=f"{now.year}年{now.month}月{now.day}日"
    )


class BailianLLM:
    """
    阿里云百炼 LLM API 封装
    支持 Qwen 系列模型（qwen-max, qwen-plus, qwen-turbo 等）
    """

    def __init__(
        self,
        model: str = "qwen-max",
        api_key: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        top_p: float = 0.8,
        max_retries: int = 3,
        use_cache: bool = True,
    ):
        self.model = model
        self.api_key = api_key or settings.dashscope_api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.max_retries = max_retries
        self.use_cache = use_cache

    def _build_messages(self, messages, system_prompt=None):
        """构建完整的消息列表

        bug-105 修复：system prompt 统一追加当前日期说明，
        避免模型声明"截止到训练数据日期"（如 2024 年 7 月）并误当当前时间。
        """
        full_messages = []
        if system_prompt:
            full_messages.append({
                "role": "system",
                "content": system_prompt + _build_current_date_note(),
            })
        full_messages.extend(messages)
        return full_messages

    def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        enable_search: bool = False,
        **kwargs,
    ) -> str:
        """
        调用大模型生成回复（非流式，带缓存）

        优化：
          启用响应缓存，相同问题不重复调用 API

        注意：dashscope SDK 内部管理 HTTP 连接，无需手动复用。
        百炼 API 的流式模式已自动优化首 token 延迟。

        bug-106：enable_search 为 True 时启用联网搜索；并入 call_kwargs
        以区分缓存 key（搜索回答与不搜索回答不可混用缓存）。
        """
        # bug-106：enable_search 并入 call_kwargs（透传 SDK 且参与缓存 key）
        call_kwargs = dict(kwargs) if kwargs else {}
        call_kwargs["enable_search"] = enable_search
        if enable_search and system_prompt:
            system_prompt = system_prompt + _SEARCH_GUIDE_NOTE
        full_messages = self._build_messages(messages, system_prompt)

        # 检查缓存
        if self.use_cache:
            # P1-3 修复：缓存 key 补齐生成参数（max_tokens/top_p/额外 kwargs），
            # 避免不同生成参数请求共享同一缓存条目导致返回错误参数的旧答案
            cached = llm_cache.get_with_key(
                "chat", self.model, full_messages, self.temperature,
                self.max_tokens, self.top_p, call_kwargs,
            )
            if cached is not None:
                logger.debug("LLM 响应命中缓存")
                # bug-114：过滤旧缓存中的 emoji（升级前缓存的回答可能含表情）
                return strip_emoji(cached)

        for attempt in range(self.max_retries):
            try:
                resp = Generation.call(
                    model=self.model,
                    messages=full_messages,
                    api_key=self.api_key,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    top_p=self.top_p,
                    stream=False,
                    result_format="message",
                    **call_kwargs,
                )

                if resp.status_code == 200:
                    content = resp.output.choices[0].message.content
                    # bug-114：移除回答中的 emoji，保持输出纯文本
                    content = strip_emoji(content)
                    # 写入缓存
                    if self.use_cache:
                        # P1-3 修复：与查询侧缓存 key 保持一致，补齐生成参数
                        llm_cache.set_with_key(
                            content, "chat", self.model, full_messages, self.temperature,
                            self.max_tokens, self.top_p, call_kwargs,
                        )
                    return content
                else:
                    logger.warning(
                        f"LLM API 返回异常 (attempt {attempt + 1}): "
                        f"{resp.status_code} - {resp.message}"
                    )
                    # P1-1 修复：非 200 响应（如 429 限流）同样退避后重试，避免无间隔连续请求
                    # bug-095 修复：4xx（除 429 外）为确定性客户端错误，直接失败并带出服务端详情
                    if 400 <= resp.status_code < 500 and resp.status_code != 429:
                        raise FatalAPIError(
                            f"LLM API 返回 {resp.status_code}: {resp.message}"
                        )
                    if attempt < self.max_retries - 1:
                        time.sleep(2 ** attempt)

            except Exception as e:
                # bug-095 修复：确定性客户端错误直接抛出，不进入重试循环
                if isinstance(e, FatalAPIError):
                    raise
                logger.warning(
                    f"LLM 请求失败 (attempt {attempt + 1}): {e}"
                )
                if attempt < self.max_retries - 1:
                    wait_time = 2 ** attempt
                    time.sleep(wait_time)

        raise RuntimeError(f"LLM API 调用失败（已达最大重试次数）")

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        enable_search: bool = False,
    ) -> Generator[str, None, None]:
        """
        流式调用（生成器，逐 token 产出）

        优化：
          百炼 API 的 stream=True 模式会自动优化首 token 延迟，
          在检索完成后尽快返回第一个 token。

        注意：
          一旦生成器开始 yield token 后遇到异常，不会重试而是直接抛出，
          避免重试时从头开始生成导致调用方收到重复的 token。

        bug-106：enable_search 为 True 时启用联网搜索（流式模式同样支持）。
        """
        # bug-106：启用搜索时追加引导，让模型优先使用 RAG 参考信息
        if enable_search and system_prompt:
            system_prompt = system_prompt + _SEARCH_GUIDE_NOTE
        full_messages = self._build_messages(messages, system_prompt)

        for attempt in range(self.max_retries):
            # 在 try 块之前初始化，确保 Generation.call() 抛出异常时
            # except 块中也能安全访问 has_yielded（避免 UnboundLocalError）
            has_yielded = False
            try:
                responses = Generation.call(
                    model=self.model,
                    messages=full_messages,
                    api_key=self.api_key,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    top_p=self.top_p,
                    stream=True,
                    result_format="message",
                    # bug-106：启用联网搜索（按需，由 rag_pipeline 决策）
                    enable_search=enable_search,
                    # bug-102 根因修复：dashscope 对 qwen 系列默认将流式响应合并为
                    # "每个 chunk 是到当前为止的累积全文"（incremental_to_full 模式），
                    # 若按增量追加会导致内容膨胀重复（实测 195 件文物循环）。
                    # 显式要求增量输出，每个 chunk 的 content 为独立 token 增量。
                    incremental_output=True,
                )

                # has_yielded 已在 try 前初始化
                for resp in responses:
                    if resp.status_code == 200:
                        content = resp.output.choices[0].message.content
                        if content:
                            # bug-114：逐 token 移除 emoji（含 ZWJ/变体符残留），
                            # 保证流式输出全程不出现表情图标
                            yield strip_emoji(content)
                            has_yielded = True
                    else:
                        logger.warning(f"Stream 返回异常: {resp.status_code} - {resp.message}")
                        # P1-1 修复：非 200 响应走重试逻辑（含退避）；
                        # 若已 yield 过 token 则由 except 分支直接中断，避免重复内容
                        # bug-095 修复：4xx（除 429 外）为确定性客户端错误，直接抛出
                        if 400 <= resp.status_code < 500 and resp.status_code != 429:
                            raise FatalAPIError(f"Stream 返回异常: {resp.status_code} - {resp.message}")
                        raise RuntimeError(f"Stream 返回异常: {resp.status_code} - {resp.message}")
                return

            except Exception as e:
                # bug-095 修复：确定性客户端错误（4xx 非 429）直接抛出，不重试
                if isinstance(e, FatalAPIError):
                    raise
                # 如果已经 yield 过 token，直接抛出异常，避免重试产生重复内容
                if has_yielded:
                    raise RuntimeError(f"Stream 输出中断: {e}")
                logger.warning(
                    f"Stream 请求失败 (attempt {attempt + 1}): {e}"
                )
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)

        raise RuntimeError("Stream 调用失败")

    def count_tokens(self, text: str) -> int:
        """估算 Token 数量（粗略估算）"""
        # 中文约 1.5 tokens/字，英文约 1 token/4字符
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other_chars = len(text) - chinese_chars
        return int(chinese_chars * 1.5 + other_chars / 4) + 10


class LocalOpenAILLM:
    """本地 OpenAI 兼容 LLM 封装（web-044，如 vLLM 部署的 Qwen2.5-14B-Instruct-AWQ）

    与 BailianLLM 接口对齐（chat / chat_stream / count_tokens），供 create_llm
    按 settings.llm_provider 切换。行为与内核对齐：
      - system prompt 统一追加当前日期说明（bug-105 同款）；
      - 流式增量输出、逐 token 去 emoji（bug-114 同款）；
      - 指数退避重试；已 yield token 后中断不重试（避免重复内容）；
      - 4xx（除 429）视为确定性客户端错误，直接抛 FatalAPIError。
    差异（本地服务无私有联网能力）：enable_search=True 仅告警并忽略，
    不追加 _SEARCH_GUIDE_NOTE（避免引导模型依赖不存在的联网结果）。
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        top_p: float = 0.8,
        max_retries: int = 3,
        use_cache: bool = True,
        context_tokens: int = 4096,
    ):
        try:
            from openai import OpenAI
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "本地 LLM 需要 openai 包：pip install 'openai>=1.40,<2'（web-044）"
            ) from e
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.max_retries = max_retries
        self.use_cache = use_cache
        self.context_tokens = context_tokens
        # OpenAI 客户端要求非空 key；本地服务常不校验，给占位值
        self._client = OpenAI(base_url=base_url, api_key=api_key or "local-no-key")

    def _build_messages(self, messages, system_prompt=None):
        """与 BailianLLM 一致：system prompt 追加当前日期说明（零改动 BailianLLM，
        此处保留 6 行重复换取冻结路径字节级不变）。"""
        full_messages = []
        if system_prompt:
            full_messages.append({
                "role": "system",
                "content": system_prompt + _build_current_date_note(),
            })
        full_messages.extend(messages)
        return full_messages

    # web-045：system 截断标记（告知模型参考信息被裁，避免幻觉衔接）
    _TRUNC_MARK = "……（参考信息过长已截断）"

    def _prompt_budget(self) -> int:
        """prompt token 预算：窗口 - 32 安全余量 - min(max_tokens, 256) 保底 completion"""
        return self.context_tokens - 32 - min(self.max_tokens, 256)

    def _est_tokens(self, full_messages: List[Dict[str, str]]) -> int:
        return sum(self.count_tokens(m.get("content") or "") for m in full_messages)

    def _fit_messages_to_window(
        self, full_messages: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:
        """把 prompt 适配进上下文窗口（web-045 实测修复）

        背景：KB 路径 prompt = 指令 + 检索上下文（内核 MAX_CONTEXT_CHARS=30000）+ 历史，
        本地小窗口模型（4096）直接被 serving 侧 400 拒绝（实测 6969 tokens 超窗）
        → FatalAPIError → 前端空气泡。适配策略（保序、保当前问题）：
          1. 从对话头部丢弃最老消息（保留 system 与最后一条 user 问题；
             连续后缀丢弃保证角色交替不被破坏）；
          2. 仍超 → 截 system 内容尾部（保留头部指令，加截断标记）。
        """
        budget = self._prompt_budget()
        if self._est_tokens(full_messages) <= budget:
            return full_messages

        has_system = bool(full_messages) and full_messages[0].get("role") == "system"
        system = full_messages[:1] if has_system else []
        convo = list(full_messages[1:] if has_system else full_messages)

        # 1) 丢最老历史（至少保留最后一条=当前问题）
        dropped = 0
        while len(convo) > 1 and self._est_tokens(system + convo) > budget:
            convo.pop(0)
            dropped += 1

        # 2) 仍超 → 截 system 尾部（按粗估反推字符数；中文 1.5 tok/字为保守方向）
        truncated = False
        if system and self._est_tokens(system + convo) > budget:
            convo_est = self._est_tokens(convo)
            sys_token_budget = max(budget - convo_est, 50)
            chars = max(int((sys_token_budget - 10) / 1.5), 50)
            content = system[0].get("content") or ""
            if len(content) > chars:
                content = content[:max(chars - len(self._TRUNC_MARK), 1)] + self._TRUNC_MARK
                system = [{**system[0], "content": content}]
                truncated = True

        logger.info(
            f"本地 LLM prompt 适配: dropped_history={dropped}, "
            f"system_truncated={truncated}, est={self._est_tokens(system + convo)}"
            f"/{budget}（web-045）")
        return system + convo

    def _effective_max_tokens(self, full_messages: List[Dict[str, str]]) -> int:
        """按上下文预算钳制 completion max_tokens（web-044 实测修复）

        本地模型上下文总长有限（vLLM max_model_len，如 4096）：prompt + completion
        超窗会被 serving 侧 400 拒绝（实测 169+4096=4265>4096）。按
        「context_tokens - 估算 prompt - 安全余量 32」钳制，保底 1。
        """
        est_prompt = sum(self.count_tokens(m.get("content") or "") for m in full_messages)
        budget = self.context_tokens - est_prompt - 32
        effective = min(self.max_tokens, max(budget, 1))
        if effective < self.max_tokens:
            logger.info(
                f"本地 LLM completion 限长钳制: {self.max_tokens} -> {effective} "
                f"(context={self.context_tokens}, est_prompt={est_prompt})")
        return effective

    @staticmethod
    def _check_fatal(e: Exception) -> None:
        """4xx（除 429）→ FatalAPIError（duck-typed：openai.APIStatusError 携带 status_code）"""
        status = getattr(e, "status_code", None)
        if isinstance(status, int) and 400 <= status < 500 and status != 429:
            raise FatalAPIError(f"本地 LLM 返回 {status}: {e}")

    def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        enable_search: bool = False,
        **kwargs,
    ) -> str:
        """非流式调用（带缓存，缓存 key 含 provider 标记，与百炼路径隔离）"""
        if enable_search:
            logger.warning("本地 LLM 不支持联网搜索，已忽略 enable_search（web-044）")
        full_messages = self._fit_messages_to_window(
            self._build_messages(messages, system_prompt))
        cache_extra = dict(kwargs) if kwargs else {}
        cache_extra["provider"] = "local"

        if self.use_cache:
            cached = llm_cache.get_with_key(
                "chat", self.model, full_messages, self.temperature,
                self.max_tokens, self.top_p, cache_extra,
            )
            if cached is not None:
                logger.debug("本地 LLM 响应命中缓存")
                return strip_emoji(cached)

        for attempt in range(self.max_retries):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=full_messages,
                    temperature=self.temperature,
                    max_tokens=self._effective_max_tokens(full_messages),
                    top_p=self.top_p,
                    stream=False,
                    **kwargs,
                )
                content = resp.choices[0].message.content or ""
                content = strip_emoji(content)
                if self.use_cache:
                    llm_cache.set_with_key(
                        content, "chat", self.model, full_messages, self.temperature,
                        self.max_tokens, self.top_p, cache_extra,
                    )
                return content
            except Exception as e:
                self._check_fatal(e)
                logger.warning(f"本地 LLM 请求失败 (attempt {attempt + 1}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)

        raise RuntimeError("本地 LLM 调用失败（已达最大重试次数）")

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        enable_search: bool = False,
    ) -> Generator[str, None, None]:
        """流式调用（增量输出；本地无联网能力，enable_search 仅告警并忽略）"""
        if enable_search:
            logger.warning("本地 LLM 不支持联网搜索，已忽略 enable_search（web-044）")
        full_messages = self._fit_messages_to_window(
            self._build_messages(messages, system_prompt))

        for attempt in range(self.max_retries):
            has_yielded = False
            try:
                stream = self._client.chat.completions.create(
                    model=self.model,
                    messages=full_messages,
                    temperature=self.temperature,
                    max_tokens=self._effective_max_tokens(full_messages),
                    top_p=self.top_p,
                    stream=True,
                )
                for chunk in stream:
                    if not chunk.choices:      # 末尾 usage 帧无 choices
                        continue
                    content = chunk.choices[0].delta.content
                    if content:
                        yield strip_emoji(content)
                        has_yielded = True
                return
            except Exception as e:
                self._check_fatal(e)
                if has_yielded:
                    raise RuntimeError(f"本地 LLM Stream 输出中断: {e}")
                logger.warning(f"本地 LLM Stream 请求失败 (attempt {attempt + 1}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)

        raise RuntimeError("本地 LLM Stream 调用失败")

    def count_tokens(self, text: str) -> int:
        """估算 Token 数量（与 BailianLLM 同款粗略估算）"""
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other_chars = len(text) - chinese_chars
        return int(chinese_chars * 1.5 + other_chars / 4) + 10


def create_llm(model: Optional[str] = None, use_cache: bool = True):
    """LLM 工厂（web-044）：按 settings.llm_provider 返回对应实现。

    - dashscope（默认）：BailianLLM，参数与内核既有接线完全一致
      （model 参数缺省回退 settings.llm_model_name）；
    - local：LocalOpenAILLM，模型名/地址/密钥取 local_llm_* 配置
      （model 参数不作用于本地路径），生成参数复用 llm_temperature/
      llm_max_tokens/llm_top_p（一体机薄层 web-041 进程级钳制同样生效）。
    """
    if settings.llm_provider == "local":
        return LocalOpenAILLM(
            model=settings.local_llm_model,
            base_url=settings.local_llm_base_url,
            api_key=settings.local_llm_api_key,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            top_p=settings.llm_top_p,
            use_cache=use_cache,
            context_tokens=settings.local_llm_context_tokens,
        )
    return BailianLLM(
        model=model or settings.llm_model_name,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        top_p=settings.llm_top_p,
        use_cache=use_cache,
    )