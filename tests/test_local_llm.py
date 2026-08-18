# web-044：本地大模型（OpenAI 兼容，Qwen2.5-14B-Instruct-AWQ）与百炼 DashScope
# 双通道并存、可切换（LLM_PROVIDER=dashscope|local）。
# 全部离线：openai.OpenAI 一律 mock；真实链路验证见 scripts/smoke_local_llm.py。
import time
import types

import pytest

from src.config import Settings, settings
from src.llm import BailianLLM, LocalOpenAILLM, create_llm
from src.utils import FatalAPIError


# ---------- 假 OpenAI 客户端（脚本化：每次 create 弹出下一个脚本项） ----------
class FakeCompletions:
    def __init__(self):
        self.calls = []
        self.script = []

    def create(self, **kw):
        self.calls.append(kw)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeOpenAI:
    instances = []

    def __init__(self, **kw):
        self.kwargs = kw
        self.chat = types.SimpleNamespace(completions=FakeCompletions())
        FakeOpenAI.instances.append(self)

    @classmethod
    def last(cls):
        return cls.instances[-1]


@pytest.fixture(autouse=True)
def _fake_openai(monkeypatch):
    FakeOpenAI.instances.clear()
    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    yield


@pytest.fixture
def _no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)


def _msg_resp(content):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))])


def _chunk(text):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=text))])


def _make_local(**over):
    args = dict(model="qwen25-14b", base_url="http://127.0.0.1:18081/v1",
                api_key="k", temperature=0.7, max_tokens=320, top_p=0.8,
                use_cache=False)
    args.update(over)
    return LocalOpenAILLM(**args)


class TestLocalLLMConfig:
    def test_defaults_provider_dashscope(self):
        """默认提供方=百炼（零行为变化）；本地参数有安全默认值，密钥默认为空。"""
        s = Settings(_env_file=None)
        assert s.llm_provider == "dashscope"
        assert s.local_llm_model == "qwen25-14b"
        assert s.local_llm_base_url == "http://127.0.0.1:18081/v1"
        assert s.local_llm_api_key == ""

    def test_env_override(self):
        s = Settings(_env_file=None, llm_provider="local",
                     local_llm_base_url="http://x:1/v1",
                     local_llm_api_key="kk", local_llm_model="m1")
        assert s.llm_provider == "local"
        assert s.local_llm_base_url == "http://x:1/v1"
        assert s.local_llm_api_key == "kk"
        assert s.local_llm_model == "m1"


class TestCreateLLMFactory:
    def test_default_returns_bailian_with_kernel_params(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "dashscope")
        llm = create_llm(use_cache=False)
        assert isinstance(llm, BailianLLM)
        assert llm.model == settings.llm_model_name
        assert llm.temperature == settings.llm_temperature
        assert llm.max_tokens == settings.llm_max_tokens
        assert llm.top_p == settings.llm_top_p
        assert llm.use_cache is False

    def test_local_returns_local_llm_with_local_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "local")
        monkeypatch.setattr(settings, "local_llm_base_url", "http://local:9/v1")
        monkeypatch.setattr(settings, "local_llm_api_key", "sekret")
        monkeypatch.setattr(settings, "local_llm_model", "m-x")
        llm = create_llm(use_cache=False)
        assert isinstance(llm, LocalOpenAILLM)
        assert llm.model == "m-x"
        assert llm.temperature == settings.llm_temperature
        assert llm.max_tokens == settings.llm_max_tokens
        assert FakeOpenAI.last().kwargs["base_url"] == "http://local:9/v1"
        assert FakeOpenAI.last().kwargs["api_key"] == "sekret"

    def test_explicit_model_only_applies_to_dashscope(self, monkeypatch):
        """factory 的 model 参数只作用于百炼路径；local 一律用 local_llm_model。"""
        monkeypatch.setattr(settings, "llm_provider", "dashscope")
        assert create_llm(model="qwen-max", use_cache=False).model == "qwen-max"
        monkeypatch.setattr(settings, "llm_provider", "local")
        monkeypatch.setattr(settings, "local_llm_model", "m-x")
        assert create_llm(model="qwen-max", use_cache=False).model == "m-x"


class TestLocalChat:
    def test_chat_returns_emoji_stripped_content(self):
        llm = _make_local()
        FakeOpenAI.last().chat.completions.script.append(_msg_resp("你好😀世界"))
        out = llm.chat([{"role": "user", "content": "hi"}])
        assert out == "你好世界"
        call = FakeOpenAI.last().chat.completions.calls[0]
        assert call["model"] == "qwen25-14b"
        assert call["stream"] is False
        assert call["temperature"] == 0.7
        assert call["max_tokens"] == 320
        assert call["top_p"] == 0.8

    def test_chat_system_prompt_gets_current_date_note(self):
        """与内核 BailianLLM 行为对齐：system prompt 统一追加当前日期说明。"""
        llm = _make_local()
        FakeOpenAI.last().chat.completions.script.append(_msg_resp("ok"))
        llm.chat([{"role": "user", "content": "hi"}], system_prompt="你是助手")
        msgs = FakeOpenAI.last().chat.completions.calls[0]["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"].startswith("你是助手")
        assert "当前日期" in msgs[0]["content"]

    def test_chat_enable_search_warns_and_ignored(self):
        """本地模型无联网能力：enable_search=True 不追加搜索引导、正常作答。"""
        llm = _make_local()
        FakeOpenAI.last().chat.completions.script.append(_msg_resp("答"))
        out = llm.chat([{"role": "user", "content": "hi"}],
                       system_prompt="你是助手", enable_search=True)
        assert out == "答"
        sys_msg = FakeOpenAI.last().chat.completions.calls[0]["messages"][0]
        assert "联网搜索" not in sys_msg["content"]

    def test_chat_retries_on_transient_error(self, _no_sleep):
        llm = _make_local()
        comp = FakeOpenAI.last().chat.completions
        comp.script.extend([RuntimeError("conn reset"), _msg_resp("恢复")])
        assert llm.chat([{"role": "user", "content": "hi"}]) == "恢复"
        assert len(comp.calls) == 2

    def test_chat_4xx_raises_fatal_without_retry(self, _no_sleep):
        class _FakeAPIError(Exception):
            status_code = 400

        llm = _make_local()
        comp = FakeOpenAI.last().chat.completions
        comp.script.append(_FakeAPIError("bad request"))
        with pytest.raises(FatalAPIError):
            llm.chat([{"role": "user", "content": "hi"}])
        assert len(comp.calls) == 1

    def test_chat_exhausts_retries_raises_runtime(self, _no_sleep):
        llm = _make_local(max_retries=2)
        comp = FakeOpenAI.last().chat.completions
        comp.script.extend([RuntimeError("x"), RuntimeError("y")])
        with pytest.raises(RuntimeError):
            llm.chat([{"role": "user", "content": "hi"}])
        assert len(comp.calls) == 2


class TestLocalChatStream:
    def test_stream_yields_deltas_emoji_stripped(self):
        llm = _make_local()
        comp = FakeOpenAI.last().chat.completions
        comp.script.append(iter([_chunk("你好"), _chunk("😀"), _chunk("世界")]))
        out = list(llm.chat_stream([{"role": "user", "content": "hi"}]))
        assert "".join(out) == "你好世界"
        call = comp.calls[0]
        assert call["stream"] is True
        assert call["model"] == "qwen25-14b"

    def test_stream_skips_empty_choices(self):
        """流式末尾 chunk 可能无 choices（usage 帧）——不崩、跳过。"""
        llm = _make_local()
        comp = FakeOpenAI.last().chat.completions
        tail = types.SimpleNamespace(choices=[])
        comp.script.append(iter([_chunk("甲"), tail]))
        assert "".join(llm.chat_stream([{"role": "user", "content": "hi"}])) == "甲"

    def test_stream_enable_search_ignored(self):
        llm = _make_local()
        FakeOpenAI.last().chat.completions.script.append(iter([_chunk("答")]))
        out = list(llm.chat_stream([{"role": "user", "content": "hi"}],
                                   system_prompt="你是助手", enable_search=True))
        assert "".join(out) == "答"
        sys_msg = FakeOpenAI.last().chat.completions.calls[0]["messages"][0]
        assert "联网搜索" not in sys_msg["content"]     # 未追加搜索引导
        assert "当前日期" in sys_msg["content"]         # 日期说明仍在（与内核对齐）

    def test_stream_error_after_yield_no_retry(self, _no_sleep):
        """已产出 token 后中断：直接抛出，不重试（避免重复内容，与内核一致）。"""
        def _bad_stream():
            yield _chunk("甲")
            raise RuntimeError("boom")

        llm = _make_local()
        comp = FakeOpenAI.last().chat.completions
        comp.script.extend([_bad_stream(), iter([_chunk("乙")])])
        with pytest.raises(RuntimeError):
            list(llm.chat_stream([{"role": "user", "content": "hi"}]))
        assert len(comp.calls) == 1                     # 第二个脚本项未被消费

    def test_stream_error_before_yield_retries(self, _no_sleep):
        llm = _make_local()
        comp = FakeOpenAI.last().chat.completions
        comp.script.extend([RuntimeError("conn refused"), iter([_chunk("好")])])
        out = list(llm.chat_stream([{"role": "user", "content": "hi"}]))
        assert "".join(out) == "好"
        assert len(comp.calls) == 2


class TestPipelineLLMWiring:
    """RAGPipeline 经 create_llm 工厂取 LLM：provider 切换即全局切换。"""

    def test_pipeline_default_uses_bailian(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "dashscope")
        from src.rag_pipeline import RAGPipeline
        pipe = RAGPipeline(local_mode=True)
        assert isinstance(pipe.llm, BailianLLM)

    def test_pipeline_local_provider_uses_local_llm(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "local")
        monkeypatch.setattr(settings, "local_llm_api_key", "k")
        from src.rag_pipeline import RAGPipeline
        pipe = RAGPipeline(local_mode=True)
        assert isinstance(pipe.llm, LocalOpenAILLM)


class TestLocalContextBudget:
    """web-044 实测缺陷修复：本地模型上下文总长 4096（vLLM max_model_len），
    直接透传 LLM_MAX_TOKENS=4096 会被 vLLM 400 拒绝（prompt+completion 超窗）。
    → 按「上下文预算 - 估算 prompt - 安全余量」钳制 completion max_tokens。"""

    def test_config_default_context_tokens(self):
        assert Settings(_env_file=None).local_llm_context_tokens == 4096

    def test_factory_wires_context_tokens(self, monkeypatch):
        monkeypatch.setattr(settings, "llm_provider", "local")
        monkeypatch.setattr(settings, "local_llm_api_key", "k")
        monkeypatch.setattr(settings, "local_llm_context_tokens", 2048)
        llm = create_llm(use_cache=False)
        assert llm.context_tokens == 2048

    def test_chat_clamps_max_tokens_to_context_budget(self):
        llm = _make_local(max_tokens=4096, context_tokens=100)
        FakeOpenAI.last().chat.completions.script.append(_msg_resp("ok"))
        llm.chat([{"role": "user", "content": "hi"}])   # est=10 → 预算 100-10-32=58
        assert FakeOpenAI.last().chat.completions.calls[0]["max_tokens"] == 58

    def test_chat_keeps_max_tokens_when_within_budget(self):
        llm = _make_local(max_tokens=320, context_tokens=4096)
        FakeOpenAI.last().chat.completions.script.append(_msg_resp("ok"))
        llm.chat([{"role": "user", "content": "hi"}])
        assert FakeOpenAI.last().chat.completions.calls[0]["max_tokens"] == 320

    def test_stream_clamps_max_tokens_to_context_budget(self):
        llm = _make_local(max_tokens=4096, context_tokens=100)
        FakeOpenAI.last().chat.completions.script.append(iter([_chunk("ok")]))
        list(llm.chat_stream([{"role": "user", "content": "hi"}]))
        assert FakeOpenAI.last().chat.completions.calls[0]["max_tokens"] == 58


class TestLocalPromptFitting:
    """web-045 修复：KB 路径 prompt 超窗（内核上下文可达 30000 字）被 vLLM 400 拒绝、
    前端空气泡。LocalOpenAILLM 须在发送前把 prompt 适配进上下文窗口：
    先丢最老历史（保留 system 与当前问题），仍超再截 system 尾部（保留头部指令+截断标记）。
    预算 = context_tokens - 32 余量 - min(max_tokens, 256) 保底 completion。"""

    @staticmethod
    def _budget(llm):
        return llm.context_tokens - 32 - min(llm.max_tokens, 256)

    @staticmethod
    def _est(llm, msgs):
        return sum(llm.count_tokens(m["content"]) for m in msgs)

    def test_short_messages_pass_through(self):
        llm = _make_local()
        FakeOpenAI.last().chat.completions.script.append(_msg_resp("ok"))
        llm.chat([{"role": "user", "content": "hi"}], system_prompt="短提示")
        sent = FakeOpenAI.last().chat.completions.calls[0]["messages"]
        assert sent[-1] == {"role": "user", "content": "hi"}
        assert sent[0]["role"] == "system"
        assert sent[0]["content"].startswith("短提示")

    def test_long_history_dropped_oldest_first(self):
        llm = _make_local(context_tokens=1000, max_tokens=256)   # 预算 712
        FakeOpenAI.last().chat.completions.script.append(_msg_resp("ok"))
        hist = [{"role": "user" if i % 2 == 0 else "assistant",
                 "content": "旧对话内容" * 40} for i in range(10)]   # 每条 est≈310
        llm.chat(hist + [{"role": "user", "content": "当前问题"}], system_prompt="短")
        sent = FakeOpenAI.last().chat.completions.calls[0]["messages"]
        assert sent[-1] == {"role": "user", "content": "当前问题"}   # 当前问题必保留
        assert sent[0]["role"] == "system"                            # system 保留
        assert self._est(llm, sent) <= self._budget(llm)              # 适配进预算
        assert len(sent) < 12                                         # 有历史被丢弃
        # 丢弃的是最老的：若保留了历史，只能是原历史后缀
        kept = [m for m in sent if m["role"] != "system"]
        assert kept == (hist + [{"role": "user", "content": "当前问题"}])[-len(kept):]

    def test_oversized_system_truncated_tail_with_marker(self):
        llm = _make_local(context_tokens=1000, max_tokens=256)   # 预算 712
        FakeOpenAI.last().chat.completions.script.append(_msg_resp("ok"))
        big = "指令开头。" + "参考资料文字。" * 300                 # est≈3000，丢光历史仍超
        llm.chat([{"role": "user", "content": "问题"}], system_prompt=big)
        sent = FakeOpenAI.last().chat.completions.calls[0]["messages"]
        sys_sent = sent[0]["content"]
        assert sys_sent.startswith("指令开头。")                     # 头部指令保留
        assert "截断" in sys_sent                                   # 截断标记告知模型
        assert self._est(llm, sent) <= self._budget(llm)
        assert sent[-1] == {"role": "user", "content": "问题"}

    def test_stream_fits_prompt_too(self):
        llm = _make_local(context_tokens=1000, max_tokens=256)
        FakeOpenAI.last().chat.completions.script.append(iter([_chunk("ok")]))
        big = "指令开头。" + "参考资料文字。" * 300
        list(llm.chat_stream([{"role": "user", "content": "问题"}], system_prompt=big))
        sent = FakeOpenAI.last().chat.completions.calls[0]["messages"]
        assert self._est(llm, sent) <= self._budget(llm)
        assert sent[-1] == {"role": "user", "content": "问题"}
