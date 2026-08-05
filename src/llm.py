"""
LLM 模块
封装阿里云百炼 Qwen 系列模型的 API 调用
"""

from __future__ import annotations

import time
from typing import Any, Dict, Generator, List, Optional

from loguru import logger
from dashscope import Generation

from src.config import settings
from src.cache import llm_cache


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
        """构建完整的消息列表"""
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)
        return full_messages

    def chat(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> str:
        """
        调用大模型生成回复（非流式，带缓存）

        优化：
          启用响应缓存，相同问题不重复调用 API

        注意：dashscope SDK 内部管理 HTTP 连接，无需手动复用。
        百炼 API 的流式模式已自动优化首 token 延迟。
        """
        full_messages = self._build_messages(messages, system_prompt)

        # 检查缓存
        if self.use_cache:
            cached = llm_cache.get_with_key(
                "chat", self.model, full_messages, self.temperature
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
                    **kwargs,
                )

                if resp.status_code == 200:
                    content = resp.output.choices[0].message.content
                    # 写入缓存
                    if self.use_cache:
                        llm_cache.set_with_key(
                            content, "chat", self.model, full_messages, self.temperature
                        )
                    return content
                else:
                    logger.warning(
                        f"LLM API 返回异常 (attempt {attempt + 1}): "
                        f"{resp.status_code} - {resp.message}"
                    )

            except Exception as e:
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
    ) -> Generator[str, None, None]:
        """
        流式调用（生成器，逐 token 产出）

        优化：
          百炼 API 的 stream=True 模式会自动优化首 token 延迟，
          在检索完成后尽快返回第一个 token。

        注意：
          一旦生成器开始 yield token 后遇到异常，不会重试而是直接抛出，
          避免重试时从头开始生成导致调用方收到重复的 token。
        """
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
                )

                # has_yielded 已在 try 前初始化
                for resp in responses:
                    if resp.status_code == 200:
                        content = resp.output.choices[0].message.content
                        if content:
                            yield content
                            has_yielded = True
                    else:
                        logger.warning(f"Stream 返回异常: {resp.status_code}")
                return

            except Exception as e:
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