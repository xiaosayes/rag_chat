"""web-044 本地大模型真实链路冒烟（真实 API，非 pytest，离线测试见 tests/test_local_llm.py）

用法:
  LLM_PROVIDER=local python scripts/smoke_local_llm.py     # 本地 Qwen2.5-14B 通道
  python scripts/smoke_local_llm.py                        # 默认百炼通道（回归对照）

验证:
  1) create_llm 工厂按 provider 出对应实现；非流式 chat + 流式 chat_stream
     （首字延迟/总耗时/字数）真实可用；
  2) provider=local 时薄层兜底（拒答模板 → 湘小图人设流式作答）走本地模型，
     出口无 emoji / ** 残留；
  3) 默认 provider=dashscope 时百炼通道回归正常（双通道并存互不影响）。
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 强制 UTF-8 输出，避免 Windows GBK 控制台乱码
import io
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from src.config import settings
from src.llm import BailianLLM, LocalOpenAILLM, create_llm


def smoke_chat(llm):
    t0 = time.time()
    answer = llm.chat([{"role": "user", "content": "用一句话介绍你自己"}],
                      system_prompt="你是湘小图，图书馆智能问答助手。")
    dt = time.time() - t0
    print(f"[chat]      {dt:.2f}s | {len(answer)}字 | {answer[:80]}")
    return answer


def smoke_stream(llm):
    t0 = time.time()
    first_at = None
    parts = []
    for tok in llm.chat_stream(
            [{"role": "user", "content": "长沙夏天适合带孩子去哪玩？"}],
            system_prompt="你是湘小图，图书馆智能问答助手。回答简洁口语化，100字以内。"):
        if first_at is None:
            first_at = time.time() - t0
        parts.append(tok)
    dt = time.time() - t0
    answer = "".join(parts)
    print(f"[stream]    首字 {first_at:.2f}s / 全程 {dt:.2f}s | {len(answer)}字 | {answer[:80]}")
    return answer


def smoke_fallback_local():
    """provider=local 专属：薄层兜底链路（假内核拒答 → 真实本地作答）。"""
    from kiosk_server.web_fallback import WebFallbackPipeline
    from src.rag_pipeline import KB_NO_INFO_REPLY

    class FakeInner:
        def query_stream(self, question, conversation_history=None, **kw):
            yield {"type": "meta", "from_kb": True, "query_type": "factual"}
            yield KB_NO_INFO_REPLY

    t0 = time.time()
    parts = [x for x in WebFallbackPipeline(FakeInner()).query_stream(
        question="请介绍一下湖南省少年儿童图书馆", conversation_history=[])
        if isinstance(x, str)]
    answer = "".join(parts)
    dt = time.time() - t0
    dirty = ("**" in answer) or any(ord(c) > 0xFFFF for c in answer)
    print(f"[fallback]  {dt:.2f}s | {len(answer)}字 | 清洗残留={'有(!)' if dirty else '无'}")
    print(f"            {answer[:120]}")
    assert answer and KB_NO_INFO_REPLY not in answer, "兜底未接管！"
    assert not dirty, "出口存在 ** 或 emoji 残留！"
    return answer


def main():
    provider = settings.llm_provider
    print(f"== web-044 冒烟 | LLM_PROVIDER={provider} ==")
    llm = create_llm(use_cache=False)
    expect = LocalOpenAILLM if provider == "local" else BailianLLM
    assert isinstance(llm, expect), f"工厂实现不符: {type(llm).__name__} != {expect.__name__}"
    print(f"[factory]   -> {type(llm).__name__} (model={llm.model})")

    smoke_chat(llm)
    smoke_stream(llm)
    if provider == "local":
        smoke_fallback_local()
    print("== 冒烟通过 ==")


if __name__ == "__main__":
    main()
