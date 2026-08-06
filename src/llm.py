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
from src.utils import FatalAPIError


# bug-105 修复：注入当前日期并禁止知识截止声明。
# 模型（qwen 系列）基于训练数据知识回答时效性问题时，习惯声明
# "截止到 2024 年 7 月 / 我的知识截止于…"，与用户当前时间不符。
# 在 system prompt 统一注入当前日期并给出时效性回答引导。
_CURRENT_DATE_NOTE = (
    "\n\n【当前日期】今天是{date}。回答时效性问题时以当前日期为准；"
    "不要声明\"截止到XX年XX月\"、\"我的知识截止于XX\"或\"截至XX年\"等表述；"
    "若信息时效无法确认，应说明建议以官方最新发布为准。"
)

# bug-106 修复：启用联网搜索时追加引导——联网结果仅补充时效信息，
# 文物知识类信息仍以 RAG 参考信息为准，避免与网上信息冲突。
_SEARCH_GUIDE_NOTE = (
    "\n\n【联网搜索】本轮已启用联网搜索。联网结果仅用于补充时效性信息"
    "（如最新动态、展览、开放情况、门票等）；文物知识类信息仍以提供的参考信息为准，"
    "回答时不要与参考信息冲突。"
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
                return cached

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
                            yield content
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