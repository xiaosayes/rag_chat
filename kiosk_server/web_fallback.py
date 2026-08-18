"""联网搜索兜底（web-036）：知识库无确切信息且冻结内核按既定策略拒答时接管作答。

背景（实测定位）：内核 rag_pipeline 的「检索相关度低」降级路径中，
事实类（FACTUAL）问题 `_should_enable_search` 返回 False → 即使
LLM_ENABLE_SEARCH=true 也只会产出固定拒答话术 KB_NO_INFO_REPLY。
内核冻结不可改 → 薄层在 query_stream 出口处拦截：识别固定拒答模板，
改走百炼 enable_search 流式回答；其余事件（meta/正常回答）原样透传。
不修改 src/ 任何文件（仅只读导入常量与配置）。
"""
from __future__ import annotations

import logging
from typing import Generator, Iterable, Optional

from src.config import settings
from src.rag_pipeline import KB_NO_INFO_REPLY

logger = logging.getLogger(__name__)

REFUSAL_PREFIX = KB_NO_INFO_REPLY[:12]   # 前缀累积判定（拒答模板可能被分段 yield）
FALLBACK_MAX_TOKENS = 320                # 硬上限（兑底回答须短小；实测模型会无提示 875 字长文）
FALLBACK_HISTORY = 2                     # 仅带最近 1 轮（多带会被内核闲聊人设「小虎/家博会」带偏）

# 兜底专用系统提示词（薄层自有，不复用内核家博会默认主体提示词，避免带偏）
FALLBACK_SYSTEM_PROMPT = (
    "你是“湘小图”，一位友好、可靠的智能问答助手。"
    "用户的问题在本地知识库中没有确切资料，请结合公开信息作答。"
    "要求："
    "1. 回答简洁口语化（用于语音播报），一般控制在100字以内，不使用 Markdown 标记与表情符号；"
    "2. 信息务必准确，不确定就明说暂未查到确切信息，不要编造；"
    "3. 不要提及“联网”“搜索”等实现细节。"
)


def _web_search_stream(question: str,
                       history: Optional[list]) -> Generator[str, None, None]:
    """百炼 enable_search 流式回答（与 src/llm.py 同 SDK 路径与参数；密钥仅在服务端）。"""
    from dashscope import Generation

    from src.utils import strip_emoji   # bug-114 同款：逐 token 去 emoji

    messages = list(history or [])[-FALLBACK_HISTORY:]
    messages.append({"role": "user", "content": question})
    full_messages = [{"role": "system", "content": FALLBACK_SYSTEM_PROMPT}] + messages
    responses = Generation.call(
        model=settings.llm_model_name,
        messages=full_messages,
        api_key=settings.dashscope_api_key,
        temperature=settings.llm_temperature,
        max_tokens=min(settings.llm_max_tokens, FALLBACK_MAX_TOKENS),  # web-040 硬限长
        top_p=settings.llm_top_p,
        stream=True,
        result_format="message",
        enable_search=True,          # 核心：联网搜索
        incremental_output=True,     # bug-102 教训：显式增量输出
    )
    for resp in responses:
        if resp.status_code != 200:
            raise RuntimeError(f"联网兜底 LLM 调用失败: {resp.code} {resp.message}")
        content = resp.output.choices[0].message.content
        if content:
            yield strip_emoji(content).replace("**", "")   # web-040：剥 Markdown 粗体残留


class WebFallbackPipeline:
    """query_stream 出口包装：固定拒答模板 → 联网搜索兜底流（web-036）。

    其余事件（meta dict / 正常回答文本）原样透传；enabled=False 时与内核完全一致。
    """

    def __init__(self, inner, enabled: bool = True):
        self._inner = inner
        self._enabled = enabled

    def query_stream(self, question: str, conversation_history: Optional[list] = None,
                     **kw) -> Iterable:
        stream = self._inner.query_stream(
            question=question, conversation_history=conversation_history, **kw)
        if not self._enabled:
            yield from stream
            return

        it = iter(stream)
        buffered: list = []
        text_seen = ""
        refused = False
        for item in it:
            if isinstance(item, dict):           # meta 先缓冲（拒答时也要透传）
                buffered.append(item)
                continue
            if not item:                         # 空文本不影响判定
                continue
            buffered.append(item)
            text_seen += item
            if text_seen.startswith(REFUSAL_PREFIX):
                refused = True
                break
            if not REFUSAL_PREFIX.startswith(text_seen):
                break                            # 已分歧 → 正常回答
        # 判定结果：refused=拒答；否则透传（含“判定前流已尽”的边界）
        if not refused:
            for item in buffered:
                yield item
            yield from it
            return

        logger.info("知识库无确切信息且内核未联网（事实类问题）→ 薄层联网兜底: %s",
                    question[:40])
        for item in buffered:                    # 只透传 meta，丢弃拒答模板文本
            if isinstance(item, dict):
                yield item
        try:
            yield from _web_search_stream(question, conversation_history)
        except Exception as e:
            logger.warning("联网兜底失败，回退内核拒答话术: %s", e)
            yield KB_NO_INFO_REPLY
