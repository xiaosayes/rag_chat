"""
文物知识库 RAG 系统 - Gradio Web UI v2
支持检索结果可视化、闲聊路由、响应时间显示
"""

import sys
import threading
import time
import json
from contextlib import suppress
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 语音功能（bug-121）：gradio 导入即触发 pydub 导入，pydub 在 import 时缓存
# ffmpeg 查找结果，因此 HLS 流式音频输出所需的 ffmpeg 引导必须在 gradio 之前执行
from src.audio_bootstrap import ensure_ffmpeg

ensure_ffmpeg()

import gradio as gr
from loguru import logger

from src.config import settings
from src.utils import setup_logger, clean_text_for_tts
from src.asr import IflytekASR, _to_pcm16k, load_dict
from src.tts import CosyVoiceTTS
from src.rag_pipeline import RAGPipeline
from src.project import project_manager

# Gradio 6.0 破坏性变更兼容（bug-098）：
#   - Chatbot 的 show_copy_button / bubble_full_width 参数被移除，改用 buttons / layout
#   - Blocks 构造器的 theme / css 参数被移除，改到 launch() 传入
# 通过主版本号分支，保证 4.x / 5.x / 6.x 均可运行。
_GRADIO_MAJOR = int(gr.__version__.split(".")[0])

_UI_THEME = gr.themes.Soft(
    primary_hue="amber",
    secondary_hue="stone",
    neutral_hue="slate",
)
_UI_CSS = """
.gradio-container { max-width: 1100px !important; margin: auto; }
.chat-message { font-size: 15px; line-height: 1.6; }
footer { display: none !important; }
.chunks-panel { border-left: 3px solid #e5a23b; padding-left: 12px; }
"""


def _extract_text(content):
    """统一提取消息文本内容，兼容 Gradio 6.x 的 content 格式。

    bug-104 修复：Gradio 6 的 Chatbot.preprocess 会把消息 content 从 str
    转为多模态 list 格式（如 [{"type": "text", "text": "..."}]），
    多轮对话时 _iter_history_pairs 取到的 content 为 list，
    导致 _convert_history 中 .find() 崩溃。此处兼容 str / list / 数字 / None。
    """
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    if isinstance(content, (int, float)):
        return str(content)
    return content  # str 或 None


def _iter_history_pairs(history: list):
    """归一化 Gradio 4/5（tuple 列表）与 6.x（dict 列表）的 Chatbot 历史格式。

    bug-101 修复：Gradio 6.0 起 Chatbot 消息格式从 [(user, assistant), ...]
    改为 [{"role": ..., "content": ...}, ...]。此处按元素类型自动检测（不依赖
    Gradio 版本号），统一转换为 (user_msg, assistant_msg) 对，供 _convert_history
    复用原有处理逻辑。
    bug-104 修复：content 统一经 _extract_text 提取为文本（Gradio 6 preprocess
    会把 content 转为 list[dict] 多模态格式）。
    """
    user_msg = None
    for msg in history:
        if isinstance(msg, dict):
            # Gradio 6.x：user/assistant 交替的 dict 消息
            role = msg.get("role")
            content = _extract_text(msg.get("content", ""))
            if role == "user":
                user_msg = content
            elif role == "assistant":
                yield user_msg, content
                user_msg = None
        else:
            # Gradio 4/5：成对的 (user, assistant) 元组/列表
            yield (
                _extract_text(msg[0]) if len(msg) > 0 else None,
                _extract_text(msg[1]) if len(msg) > 1 else None,
            )
            user_msg = None
    if user_msg is not None:
        # 末尾未配对的 user 消息（对应回复为空）
        yield user_msg, None


def _append_conversation(history: list, user_msg: str, assistant_msg: str) -> None:
    """按 Gradio 版本追加一轮对话（用户消息 + 助手消息）。

    bug-101：6.x 的 Chatbot 要求 dict 消息；4/5.x 用 (user, assistant) 元组。
    """
    if _GRADIO_MAJOR >= 6:
        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": assistant_msg})
    else:
        history.append((user_msg, assistant_msg))


def _update_last_assistant(history: list, user_msg: str, assistant_msg: str) -> None:
    """按 Gradio 版本更新最后一条 assistant 消息的内容。

    bug-101：6.x 中最后一条是 assistant dict，直接改 content；4/5.x 替换整个元组。
    """
    if _GRADIO_MAJOR >= 6:
        history[-1]["content"] = assistant_msg
    else:
        history[-1] = (user_msg, assistant_msg)

# 对话历史与检索来源的分隔符
# 注意：此分隔符与 src/rag_pipeline.py 中的 CHUNK_SEPARATOR 用途不同：
#   - HISTORY_SEPARATOR（本文件）：用于对话历史与检索来源标注之间的分隔
#   - CHUNK_SEPARATOR（rag_pipeline.py）：用于上下文 chunk 之间的分隔
HISTORY_SEPARATOR = "\n\n---\n\n"
HISTORY_SEPARATOR_OLD = "\n---\n"

pipeline: RAGPipeline = None
# bug-011 修复：使用 None 而非空字符串表示空项目，避免与 None 比较时类型不一致
_current_project: Optional[str] = None
# bug-014 修复：添加线程锁，保护全局 pipeline 变量的并发访问
_pipeline_lock = threading.Lock()


def init_pipeline(project_id: str = ""):
    """初始化 RAG 流水线（指定项目）"""
    global pipeline, _current_project
    # 统一规范化：None 和 "" 都视为空（bug-013）
    project_id = project_id or ""
    # 加锁初始化（双重检查锁定模式）
    with _pipeline_lock:
        if pipeline is not None and project_id == _current_project:
            return pipeline
        logger.info(f"初始化 RAG 流水线 - 项目: {project_id or '默认'}")
        # bug-038 修复：锁内创建后用局部变量持有，预热与返回值都使用局部引用，
        # 避免其他线程在锁外替换全局 pipeline 后返回错误项目的实例（竞态）
        new_pipeline = RAGPipeline(
            local_mode=True,
            enable_cache=True,
            memory_mode=settings.qdrant_memory_mode,
            project_id=project_id or None,
        )
        # bug-059 修复：替换前关闭旧 pipeline 的向量库连接，
        # 避免频繁切换项目累积 Qdrant 文件句柄/连接
        if pipeline is not None:
            with suppress(Exception):
                pipeline.vector_store.close()
        pipeline = new_pipeline
        _current_project = project_id
        # 并发预热竞态修复：预热在锁内完成后才释放锁，
        # 避免并发请求在预热完成前拿到 _is_built=False 的 pipeline，
        # 导致 answer_question/get_system_status 误报"知识库尚未构建"
        try:
            new_pipeline._ensure_knowledge_base()
            new_pipeline.warmup()
            logger.info("RAG 流水线就绪")
        except RuntimeError as e:
            logger.warning(f"知识库未构建: {e}")
        except Exception as e:
            logger.warning(f"预热失败: {e}")
    return new_pipeline


# ========== 语音功能（bug-121）：ASR 语音输入 ==========

def asr_stream_chunk(audio_filepath, state, project_id: str = ""):
    """Gradio Audio stream 事件：音频块送达 → 送入讯飞 ASR → 实时更新输入框。

    差分发送：兼容"增量块"与"累计录音"两种前端语义（只发送新增的 PCM 长度）。
    VAD（服务端静默判定）或最长录音超时 → 自动结束转写，填入最终文本。
    """
    if not (settings.xfyun_app_id and settings.xfyun_api_key and settings.xfyun_api_secret):
        yield state, gr.update(), gr.update(value="未配置讯飞密钥（XFYUN_APP_ID/API_KEY/API_SECRET），请在 .env 补充")
        return
    if not audio_filepath:
        yield state, gr.update(), gr.update(value="")
        return
    if state is None:
        cfg = load_dict(project_id, settings.asr_dict_dir)
        state = {
            "session": IflytekASR(
                settings.xfyun_app_id, settings.xfyun_api_key, settings.xfyun_api_secret,
                language=settings.asr_language, accent=settings.asr_accent,
                vad_eos_ms=settings.asr_vad_eos, hotwords=cfg["hotwords"],
                corrections=cfg["corrections"],
            ),
            "sent_bytes": 0,
            "started": time.time(),
            "finalized": False,
        }
    asr = state["session"]
    if state["finalized"]:
        # VAD 已结束转写：忽略后续音频块（等待用户停止录音）
        yield state, gr.update(), gr.update(value="已识别完成，可修改后发送")
        return
    try:
        raw = Path(audio_filepath).read_bytes()
        pcm = _to_pcm16k(raw, settings.asr_sample_rate)
        new_pcm = pcm[state["sent_bytes"]:]
        if new_pcm:
            asr.feed(new_pcm)
            state["sent_bytes"] += len(new_pcm)
    except Exception as e:
        logger.warning(f"ASR 音频处理失败: {e}")
        yield state, gr.update(), gr.update(value=f"识别出错: {e}")
        return
    text = asr.correct(asr.current_text)
    if asr.is_final() or time.time() - state["started"] > settings.asr_max_duration:
        final = asr.finish()
        state["finalized"] = True
        yield state, gr.update(value=final), gr.update(value="已识别完成，可修改后发送")
        return
    yield state, gr.update(value=text) if text else gr.update(), gr.update(value="识别中…")


def asr_stream_stop(state, project_id: str = ""):
    """Gradio Audio stop 事件：用户停止录音 → 结束 ASR 会话，返回最终文本。"""
    if state and state.get("session"):
        final = state["session"].finish()
        state = None
        yield state, gr.update(value=final), gr.update(value="已识别完成，可修改后发送")
    else:
        yield state, gr.update(), gr.update(value="")


# ========== 语音功能（bug-121）：TTS 语音播报（句子级流式） ==========

def _extract_last_answer_text(history) -> str:
    """取对话历史最后一条 assistant 正文（去掉 **[检索来源]** 及之后内容）。"""
    if not history:
        return ""
    last = history[-1]
    if isinstance(last, dict):
        content = _extract_text(last.get("content", ""))
    elif isinstance(last, (list, tuple)) and len(last) > 1:
        content = _extract_text(last[1])
    else:
        content = ""
    marker = "**[检索来源]**"
    idx = content.find(marker)
    if idx >= 0:
        content = content[:idx].rstrip()
        # 去掉正文末尾残留的分隔符（\n---\n 或 \n\n---\n\n），与 _convert_history 一致
        if content.endswith("---"):
            content = content[:-3].rstrip()
    return content.strip()


def _write_replay_wav(chunks):
    """合并各句 wav 字节写入重播缓存文件，返回 Path。"""
    cache_dir = settings.project_root / "data" / "processed" / "tts_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "last_answer.wav"
    CosyVoiceTTS.write_wav(b"".join(chunks), path)
    return path


def tts_after_answer(chatbot_history, enabled):
    """respond 完成后触发：句子级流式播报 + 完整重播副本（生成器）。"""
    if not enabled:
        yield gr.update(), gr.update(), gr.update(value="")
        return
    if not settings.dashscope_api_key:
        yield gr.update(), gr.update(), gr.update(value="未配置百炼 Key（DASHSCOPE_API_KEY），语音播报不可用")
        return
    if not settings.tts_voice:
        # Task 6 将音色守卫从 synthesize_sentence 移至调用层（计划内矛盾修正）
        yield gr.update(), gr.update(), gr.update(value="未配置 TTS 音色（TTS_VOICE 为空），请在 .env 设置")
        return
    text = _extract_last_answer_text(chatbot_history)
    if not text:
        yield gr.update(), gr.update(), gr.update(value="")
        return
    text = clean_text_for_tts(text)
    tts = CosyVoiceTTS(model=settings.tts_model, voice=settings.tts_voice,
                       chunk_chars=settings.tts_chunk_chars)
    chunks = []
    try:
        for sentence in tts.split_sentences(text, settings.tts_chunk_chars):
            wav = tts.synthesize_sentence(sentence)
            chunks.append(wav)
            # 句子级流式：每句合成完立即 yield（gradio HLS 无缝续播）
            yield gr.update(value=wav), gr.update(), gr.update(value="播报中…")
        replay_path = _write_replay_wav(chunks)
        yield gr.update(), gr.update(value=str(replay_path)), gr.update(value="已播报（可点击重播）")
    except Exception as e:
        logger.warning(f"TTS 播报失败: {e}")
        yield gr.update(), gr.update(), gr.update(value=f"语音播报失败: {e}")


def _convert_history(history: list) -> list:
    """将 Gradio 对话历史转换为 LLM 消息格式（兼容 Gradio 4/5 tuple 与 6.x dict 格式）"""
    messages = []
    for user_msg, assistant_msg in _iter_history_pairs(history):
        if user_msg:
            # 如果前一条消息也是 user（中间 assistant 回复为空），
            # bug-034 修复：不能 continue（会跳过本轮的 assistant 消息，导致整轮对话丢失），
            # 上一条 user 是没有对应回复的孤儿消息，用当前 user 消息替换它，
            # 既避免连续 user，又保留最新问题及本轮回答
            if messages and messages[-1]["role"] == "user":
                messages[-1]["content"] = user_msg
            else:
                messages.append({"role": "user", "content": user_msg})
        if assistant_msg:
            # 只保留纯文本内容（去掉检索来源标注）
            # P1-1 修复：改为按检索来源标记定位并截断，
            # 避免旧分隔符 "\n---\n" 误伤回答正文中的 Markdown 水平线，
            # 导致多轮对话中该回答在分隔线之后的内容丢失
            marker = "**[检索来源]**"
            marker_idx = assistant_msg.find(marker)
            if marker_idx >= 0:
                clean = assistant_msg[:marker_idx].rstrip()
                # 去掉正文末尾残留的分隔符（\n---\n 或 \n\n---\n\n）
                if clean.endswith("---"):
                    clean = clean[:-3].rstrip()
            else:
                clean = assistant_msg.strip()
            if clean:
                messages.append({"role": "assistant", "content": clean})
            elif messages and messages[-1]["role"] == "user":
                # bug-028 修复：assistant 回复为空（只有检索来源无正文），
                # 删除对应的 user 消息，避免 LLM 收到不完整的上下文
                messages.pop()
    # 修复 bug-030：如果最后一条消息是 user 角色（对应的 assistant 回复为空），
    # 删除该消息，避免后续拼接时出现连续两个 user 角色消息违反 LLM API 格式要求
    if messages and messages[-1]["role"] == "user":
        messages.pop()
    return messages


def answer_question(question: str, history: list, use_stream: bool, project_id: str = ""):
    """
    回答用户问题（支持多项目、多轮对话、闲聊路由、检索可视化）
    """
    if not question or not question.strip():
        _append_conversation(history, "", "请输入问题")
        yield history, ""
        return

    try:
        pipe = init_pipeline(project_id)
    except Exception as e:
        _append_conversation(history, question, f"初始化失败: {e}")
        yield history, ""
        return

    if not pipe._is_built:
        _append_conversation(history, question,
            "知识库尚未构建！\n\n请先在终端运行:\n```\npython scripts/generate_mock_data.py -n 50\npython scripts/build_knowledge_base.py --source mixed\n```"
        )
        yield history, ""
        return

    conversation_history = _convert_history(history)
    _append_conversation(history, question, "")
    chunks_info = []

    if use_stream:
        full_answer = ""
        first_token_received = False
        try:
            for item in pipe.query_stream(
                question=question, top_k=settings.retriever_top_k, rerank=settings.reranker_enabled,
                conversation_history=conversation_history,
            ):
                if isinstance(item, dict) and item.get("type") == "meta":
                    # 检索结果元数据
                    chunks_info = item.get("chunks", [])
                    from_kb = item.get("from_kb", True)
                    qtype = item.get("query_type", "unknown")
                    continue
                else:
                    if not first_token_received:
                        first_token_received = True
                        _last_update = time.time()
                    full_answer += item
                    # bug-021 修复：改为基于时间间隔更新（每 100ms），避免 token 长度不均匀导致的更新频率不稳定
                    now = time.time()
                    if now - _last_update > 0.1 or len(full_answer) < 5:
                        # bug-115：展示前清洗答案正文（TTS + 字幕纯文本），
                        # 检索来源的 **名称** 加粗结构由 format_answer 保留
                        display = format_answer(clean_text_for_tts(full_answer), chunks_info)
                        _update_last_assistant(history, question, display)
                        yield history, json.dumps(chunks_info, ensure_ascii=False)
                        _last_update = now
            # 最后一次更新确保完整显示
            display = format_answer(clean_text_for_tts(full_answer), chunks_info)
            _update_last_assistant(history, question, display)
            yield history, json.dumps(chunks_info, ensure_ascii=False)
        except Exception as e:
            error_msg = f"查询出错: {e}"
            # 如果已经有部分回答，保留它而不是覆盖
            if full_answer:
                _update_last_assistant(history, question, full_answer + f"\n{HISTORY_SEPARATOR}> 剩余内容生成失败")
            else:
                _update_last_assistant(history, question, error_msg)
            yield history, json.dumps(chunks_info, ensure_ascii=False) if chunks_info else ""
    else:
        try:
            # 非流式模式：先显示"正在查询..."提示
            _update_last_assistant(history, question, "正在查询知识库...")
            yield history, ""

            result = pipe.query(
                question=question, top_k=settings.retriever_top_k, rerank=settings.reranker_enabled,
                conversation_history=conversation_history,
            )
            answer = result["answer"]
            chunks_info = result.get("retrieved_chunks", [])
            timing = result.get("timing", {})
            # bug-115：展示前清洗答案正文（TTS + 字幕纯文本）
            display = format_answer(clean_text_for_tts(answer), chunks_info, timing)
            _update_last_assistant(history, question, display)
            yield history, json.dumps(chunks_info, ensure_ascii=False)
        except Exception as e:
            error_msg = f"查询出错: {e}"
            _update_last_assistant(history, question, error_msg)
            yield history, ""


def format_answer(answer: str, chunks: list, timing: dict = None) -> str:
    """格式化答案，追加检索来源和时间信息"""
    parts = [answer]

    # 追加检索来源（仅当有检索结果时）
    if chunks:
        parts.append(f"\n{HISTORY_SEPARATOR}**[检索来源]**\n")
        # P1-4 修复：分数阈值自适应——混合检索的 RRF 融合分（约 0.01 量级）
        # 与重排后的相关性分数（0~1）量级差异大，固定阈值 0.7/0.4 会导致
        # RRF 场景下所有结果恒为灰色，无法区分相关度
        scores = [(c.get("score") or 0) for c in chunks[:5]]
        max_score = max(scores) if scores else 0
        # RRF 融合分无绝对意义（量级约 0.001~0.01），按显示排名标记：
        # 第1名 [高]，第2-3名 [中]，其余 [低]
        rrf_scale = 0 < max_score < 0.1
        for i, c in enumerate(chunks[:5], 1):
            # bug-041 修复：字段缺失/为 None 时使用默认值，避免 score 比较崩溃
            name = c.get("artifact_name") or "未知"
            score = c.get("score") or 0
            ctype = c.get("chunk_type") or "full"
            if rrf_scale:
                score_label = "[高]" if i <= 1 else "[中]" if i <= 3 else "[低]"
            else:
                score_label = "[高]" if score > 0.7 else "[中]" if score > 0.4 else "[低]"
            parts.append(f"{i}. **{name}**  {score_label} 相关度: {score:.3f}  [{ctype}]")

    # 追加响应时间（仅非流式有）
    if timing:
        total = timing.get("total", 0)
        parts.append(f"\n\n> 响应时间: {total}ms")

    return "\n".join(parts)


def clear_history():
    """清空对话历史"""
    return [], ""


def get_system_status(project_id: str = ""):
    """获取系统状态（指定项目）"""
    try:
        pipe = init_pipeline(project_id)
        if pipe._is_built:
            stats = pipe.get_stats()
            if "error" in stats:
                return f"知识库状态异常: {stats['error']}"
            project_name = pipe.project_cfg.name if pipe.project_cfg else "默认"
            return (
                f"系统就绪\n\n"
                f"**当前项目**: {project_name}\n\n"
                f"**知识库统计**\n"
                f"- 向量数量: {stats.get('vector_count', 'N/A')}\n"
                f"- 向量维度: {stats.get('vector_size', 'N/A')}\n"
                f"- 距离算法: {stats.get('distance', 'N/A')}\n\n"
                f"**模型配置**\n"
                f"- LLM: {settings.llm_model_name}\n"
                f"- Embedding: {settings.embedding_model_name}\n"
                f"- 缓存: 已启用\n\n"
                f"**提示**\n"
                f"- 知识库问题 → 自动检索知识库\n"
                f"- 闲聊问候 → 直接 LLM 回答（更快）\n"
                f"- 支持追问: \"它是什么？\""
            )
        else:
            return "知识库未构建\n\n请先在终端运行:\n```\npython scripts/generate_mock_data.py -n 50\npython scripts/build_knowledge_base.py --source mixed\n```"
    except Exception as e:
        return f"状态检查失败: {e}"


def create_ui(default_stream: bool = True, default_project: str = ""):
    """创建 Gradio 界面 v2（含检索结果可视化）

    Args:
        default_stream: 流式输出复选框的默认值（--no-stream 时置 False）
        default_project: 启动时指定的项目 ID（--project 参数），
            作为下拉框默认选中值，避免页面加载时误切换全局 pipeline（bug-111）
    """
    # bug-111 修复：下拉框 choices 动态来自 ProjectManager（含自定义/外部项目），
    # 默认值跟随启动参数 --project。此前 choices/value 硬编码 museum/enterprise，
    # 导致 --project jiabohui 启动后页面加载（demo.load → get_system_status）
    # 把全局 pipeline 切换成 museum，所有回答都变成博物馆的。
    _projects = project_manager.list_projects()
    _project_ids = [p["id"] for p in _projects]
    if not _projects:
        _project_choices = [("默认", "")]
        _default_project = ""
    else:
        _project_choices = [(p["name"], p["id"]) for p in _projects]
        _default_project = (
            default_project if default_project in _project_ids else _projects[0]["id"]
        )
    with gr.Blocks(
        title="文物知识库 RAG 问答系统",
        # bug-098：Gradio 6.0 将 theme/css 移到 launch()，构造器传参会警告并失效
        **({} if _GRADIO_MAJOR >= 6 else {"theme": _UI_THEME, "css": _UI_CSS}),
    ) as demo:
        # 项目选择器
        with gr.Row():
            project_dropdown = gr.Dropdown(
                choices=_project_choices,
                value=_default_project,
                label="选择项目",
                scale=1,
            )
            status_btn = gr.Button("刷新状态", variant="secondary", scale=1)
            status_text = gr.Markdown("正在检测系统状态...", scale=4)

        with gr.Row():
            # 左侧：对话
            with gr.Column(scale=7):
                chatbot = gr.Chatbot(
                    label="对话",
                    height=500,
                    render_markdown=True,
                    # bug-108 修复：不再传 emoji 头像。Gradio 6 将 avatar_images 的 str
                    # 当作文件路径解析为 FileData（如 "🏛️" → 路径不存在），前端渲染
                    # Chatbot 时请求无效文件导致组件区域崩溃消失（页面一闪变空白）。
                    avatar_images=None,
                    # bug-098：Gradio 6.0 移除 show_copy_button / bubble_full_width，
                    # 改用 buttons=["copy"] / layout="bubble"
                    **({"buttons": ["copy"], "layout": "bubble"} if _GRADIO_MAJOR >= 6
                       else {"show_copy_button": True, "bubble_full_width": False}),
                )

                with gr.Row():
                    msg = gr.Textbox(
                        label="输入问题",
                        placeholder="输入问题，按 Enter 发送",
                        scale=8,
                        container=False,
                    )
                    submit_btn = gr.Button("发送", variant="primary", scale=1, min_width=80)

                with gr.Row():
                    voice_audio = gr.Audio(
                        sources=["microphone"],
                        streaming=True,
                        type="filepath",
                        label="语音输入（点击开始说话，说完静默约 2 秒自动转写）",
                        scale=6,
                    )
                    voice_status = gr.Markdown("", scale=4)
                    asr_state = gr.State(None)

                with gr.Row():
                    use_stream = gr.Checkbox(label="流式输出", value=default_stream)
                    clear_btn = gr.Button("清空对话", variant="secondary", size="sm", scale=1)

            # 右侧：检索结果面板
            with gr.Column(scale=3):
                gr.Markdown("### 检索结果")
                chunks_json = gr.JSON(
                    label="匹配的文物",
                    value=[],
                    visible=True,
                )
                gr.Markdown(
                    "**提示**\n"
                    "- 文物问题 → 自动检索知识库\n"
                    "- 闲聊问候 → 直接 AI 回答\n"
                    "- [高] = 高相关度\n"
                    "- [中] = 中等相关度\n"
                    "- [低] = 低相关度"
                )

                gr.Markdown("### 语音播报")
                tts_audio = gr.Audio(streaming=True, label="播报（自动播放）", visible=True)
                tts_replay = gr.Audio(label="重播", visible=True)
                tts_enabled = gr.Checkbox(label="语音播报", value=True)
                tts_status = gr.Markdown("")

        # 示例问题
        with gr.Row():
            gr.Markdown("**试试这些问题:**")

        examples = [
            "推荐一些代表性的文物",
            "公司的主要产品有哪些",
            "司母戊鼎有多重",
            "员工入职流程是什么",
            "你好，你是谁",
            "今天天气怎么样",
        ]
        example_btns = []
        for i in range(0, len(examples), 2):
            with gr.Row():
                for j in range(2):
                    idx = i + j
                    if idx < len(examples):
                        btn = gr.Button(examples[idx], variant="secondary", size="sm", scale=1)
                        example_btns.append(btn)

        # ========== 事件绑定 ==========

        def respond(message, chat_history, stream, project):
            if not message or not message.strip():
                yield "", chat_history, "[]"
                return
            for result in answer_question(message, chat_history, stream, project):
                yield "", result[0], result[1]

        msg_submit = msg.submit(respond, [msg, chatbot, use_stream, project_dropdown], [msg, chatbot, chunks_json])
        submit_click = submit_btn.click(respond, [msg, chatbot, use_stream, project_dropdown], [msg, chatbot, chunks_json])
        clear_btn.click(clear_history, None, [chatbot, chunks_json])
        status_btn.click(get_system_status, [project_dropdown], [status_text])

        # 语音功能（bug-121）：ASR 流式输入
        voice_audio.stream(
            asr_stream_chunk,
            [voice_audio, asr_state, project_dropdown],
            [asr_state, msg, voice_status],
            stream_every=0.5,
        )
        voice_audio.stop(
            asr_stream_stop,
            [asr_state, project_dropdown],
            [asr_state, msg, voice_status],
        )

        for btn in example_btns:
            btn.click(respond, [btn, chatbot, use_stream, project_dropdown], [msg, chatbot, chunks_json])

        # 语音功能（bug-121）：回答完成后自动语音播报（句子级流式播放 + 重播）
        # 示例按钮不触发播报（避免误播），用户可后续按需加入
        for dep in (msg_submit, submit_click):
            dep.then(tts_after_answer,
                     inputs=[chatbot, tts_enabled],
                     outputs=[tts_audio, tts_replay, tts_status])

        demo.load(get_system_status, [project_dropdown], [status_text])

    return demo


def main():
    import argparse
    parser = argparse.ArgumentParser(description="文物知识库 RAG Web UI v2")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=7860, help="监听端口")
    parser.add_argument("--share", action="store_true", help="创建公开链接")
    # bug-054 修复：补全 README/DEPLOY_GUIDE 中已声明但缺失的 --project / --no-stream 参数
    parser.add_argument("--project", type=str, default="", help="项目 ID（如 museum、enterprise）")
    parser.add_argument("--no-stream", action="store_true", help="禁用流式输出")
    # bug-122：语音输入（麦克风）需 HTTPS 安全上下文，原生 SSL 启动：
    # python app.py --ssl-keyfile certs/key.pem --ssl-certfile certs/cert.pem
    parser.add_argument("--ssl-keyfile", type=str, default="", help="HTTPS 证书私钥路径（语音输入需 HTTPS，两个 ssl 参数需同时提供）")
    parser.add_argument("--ssl-certfile", type=str, default="", help="HTTPS 证书路径（语音输入需 HTTPS，两个 ssl 参数需同时提供）")
    args = parser.parse_args()

    setup_logger(settings.log_level)
    logger.info("正在初始化 RAG 系统...")
    try:
        init_pipeline(args.project)
    except Exception as e:
        logger.warning(f"初始化警告: {e}")

    demo = create_ui(default_stream=not args.no_stream, default_project=args.project)
    logger.info(f"启动 Web UI: http://{args.host}:{args.port}")
    launch_kwargs = {
        "server_name": args.host,
        "server_port": args.port,
        "share": args.share,
        "show_error": True,
    }
    if _GRADIO_MAJOR >= 6:
        # bug-098：Gradio 6.0 将 theme/css 移到 launch()
        launch_kwargs["theme"] = _UI_THEME
        launch_kwargs["css"] = _UI_CSS
    if args.ssl_keyfile or args.ssl_certfile:
        # bug-122：浏览器 getUserMedia 要求安全上下文（HTTPS/localhost），
        # 自签或正式证书均可；仅提供其一则报错提示
        if args.ssl_keyfile and args.ssl_certfile:
            launch_kwargs["ssl_keyfile"] = args.ssl_keyfile
            launch_kwargs["ssl_certfile"] = args.ssl_certfile
        else:
            raise SystemExit("--ssl-keyfile 与 --ssl-certfile 必须同时提供")
    demo.launch(**launch_kwargs)


if __name__ == "__main__":
    main()