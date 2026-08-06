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

import gradio as gr
from loguru import logger

from src.config import settings
from src.utils import setup_logger
from src.rag_pipeline import RAGPipeline

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


def _convert_history(history: list) -> list:
    """将 Gradio 对话历史转换为 LLM 消息格式"""
    messages = []
    for user_msg, assistant_msg in history:
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
            marker = "**📚 检索来源**"
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
        history.append(("", "请输入问题"))
        yield history, ""
        return

    try:
        pipe = init_pipeline(project_id)
    except Exception as e:
        history.append((question, f"❌ 初始化失败: {e}"))
        yield history, ""
        return

    if not pipe._is_built:
        history.append((
            question,
            "⚠️ 知识库尚未构建！\n\n请先在终端运行:\n```\npython scripts/generate_mock_data.py -n 50\npython scripts/build_knowledge_base.py --source mixed\n```"
        ))
        yield history, ""
        return

    conversation_history = _convert_history(history)
    history.append((question, ""))
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
                        display = format_answer(full_answer, chunks_info)
                        history[-1] = (question, display)
                        yield history, json.dumps(chunks_info, ensure_ascii=False)
                        _last_update = now
            # 最后一次更新确保完整显示
            display = format_answer(full_answer, chunks_info)
            history[-1] = (question, display)
            yield history, json.dumps(chunks_info, ensure_ascii=False)
        except Exception as e:
            error_msg = f"❌ 查询出错: {e}"
            # 如果已经有部分回答，保留它而不是覆盖
            if full_answer:
                history[-1] = (question, full_answer + f"\n{HISTORY_SEPARATOR}> ❌ 剩余内容生成失败")
            else:
                history[-1] = (question, error_msg)
            yield history, json.dumps(chunks_info, ensure_ascii=False) if chunks_info else ""
    else:
        try:
            # 非流式模式：先显示"正在查询..."提示
            history[-1] = (question, "⏳ 正在查询知识库...")
            yield history, ""

            result = pipe.query(
                question=question, top_k=settings.retriever_top_k, rerank=settings.reranker_enabled,
                conversation_history=conversation_history,
            )
            answer = result["answer"]
            chunks_info = result.get("retrieved_chunks", [])
            timing = result.get("timing", {})
            display = format_answer(answer, chunks_info, timing)
            history[-1] = (question, display)
            yield history, json.dumps(chunks_info, ensure_ascii=False)
        except Exception as e:
            error_msg = f"❌ 查询出错: {e}"
            history[-1] = (question, error_msg)
            yield history, ""


def format_answer(answer: str, chunks: list, timing: dict = None) -> str:
    """格式化答案，追加检索来源和时间信息"""
    parts = [answer]

    # 追加检索来源（仅当有检索结果时）
    if chunks:
        parts.append(f"\n{HISTORY_SEPARATOR}**📚 检索来源**\n")
        # P1-4 修复：分数阈值自适应——混合检索的 RRF 融合分（约 0.01 量级）
        # 与重排后的相关性分数（0~1）量级差异大，固定阈值 0.7/0.4 会导致
        # RRF 场景下所有结果恒为灰色 ⚪，无法区分相关度
        scores = [(c.get("score") or 0) for c in chunks[:5]]
        max_score = max(scores) if scores else 0
        # RRF 融合分无绝对意义（量级约 0.001~0.01），按显示排名上色：
        # 第1名 🟢，第2-3名 🟡，其余 ⚪
        rrf_scale = 0 < max_score < 0.1
        for i, c in enumerate(chunks[:5], 1):
            # bug-041 修复：字段缺失/为 None 时使用默认值，避免 score 比较崩溃
            name = c.get("artifact_name") or "未知"
            score = c.get("score") or 0
            ctype = c.get("chunk_type") or "full"
            if rrf_scale:
                score_bar = "🟢" if i <= 1 else "🟡" if i <= 3 else "⚪"
            else:
                score_bar = "🟢" if score > 0.7 else "🟡" if score > 0.4 else "⚪"
            parts.append(f"{i}. **{name}**  {score_bar} 相关度: {score:.3f}  [{ctype}]")

    # 追加响应时间（仅非流式有）
    if timing:
        total = timing.get("total", 0)
        parts.append(f"\n\n> ⏱ 响应时间: {total}ms")

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
                return f"⚠️ 知识库状态: {stats['error']}"
            project_name = pipe.project_cfg.name if pipe.project_cfg else "默认"
            return (
                f"✅ 系统就绪\n\n"
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
            return "⚠️ 知识库未构建\n\n请先在终端运行:\n```\npython scripts/generate_mock_data.py -n 50\npython scripts/build_knowledge_base.py --source mixed\n```"
    except Exception as e:
        return f"❌ 状态检查失败: {e}"


def create_ui(default_stream: bool = True):
    """创建 Gradio 界面 v2（含检索结果可视化）

    Args:
        default_stream: 流式输出复选框的默认值（--no-stream 时置 False）
    """
    with gr.Blocks(
        title="文物知识库 RAG 问答系统",
        # bug-098：Gradio 6.0 将 theme/css 移到 launch()，构造器传参会警告并失效
        **({} if _GRADIO_MAJOR >= 6 else {"theme": _UI_THEME, "css": _UI_CSS}),
    ) as demo:
        # 项目选择器
        with gr.Row():
            project_dropdown = gr.Dropdown(
                choices=[("博物馆知识库", "museum"), ("企业知识库", "enterprise")],
                value="museum",
                label="选择项目",
                scale=1,
            )
            status_btn = gr.Button("🔄 刷新状态", variant="secondary", scale=1)
            status_text = gr.Markdown("正在检测系统状态...", scale=4)

        with gr.Row():
            # 左侧：对话
            with gr.Column(scale=7):
                chatbot = gr.Chatbot(
                    label="对话",
                    height=500,
                    render_markdown=True,
                    avatar_images=(None, "🏛️"),
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
                    use_stream = gr.Checkbox(label="流式输出", value=default_stream)
                    clear_btn = gr.Button("🗑️ 清空对话", variant="secondary", size="sm", scale=1)

            # 右侧：检索结果面板
            with gr.Column(scale=3):
                gr.Markdown("### 📊 检索结果")
                chunks_json = gr.JSON(
                    label="匹配的文物",
                    value=[],
                    visible=True,
                )
                gr.Markdown(
                    "**💡 提示**\n"
                    "- 文物问题 → 自动检索知识库\n"
                    "- 闲聊问候 → 直接 AI 回答\n"
                    "- 绿色 🟢 = 高相关度\n"
                    "- 黄色 🟡 = 中等相关度\n"
                    "- 灰色 ⚪ = 低相关度"
                )

        # 示例问题
        with gr.Row():
            gr.Markdown("**💡 试试这些问题:**")

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

        msg.submit(respond, [msg, chatbot, use_stream, project_dropdown], [msg, chatbot, chunks_json])
        submit_btn.click(respond, [msg, chatbot, use_stream, project_dropdown], [msg, chatbot, chunks_json])
        clear_btn.click(clear_history, None, [chatbot, chunks_json])
        status_btn.click(get_system_status, [project_dropdown], [status_text])

        for btn in example_btns:
            btn.click(respond, [btn, chatbot, use_stream, project_dropdown], [msg, chatbot, chunks_json])

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
    args = parser.parse_args()

    setup_logger(settings.log_level)
    logger.info("正在初始化 RAG 系统...")
    try:
        init_pipeline(args.project)
    except Exception as e:
        logger.warning(f"初始化警告: {e}")

    demo = create_ui(default_stream=not args.no_stream)
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
    demo.launch(**launch_kwargs)


if __name__ == "__main__":
    main()