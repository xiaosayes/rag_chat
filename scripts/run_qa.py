"""
RAG 问答交互脚本
提供交互式问答界面和单次查询模式
"""

import sys
import argparse
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.table import Table
from rich.prompt import Prompt

from src.config import settings
from src.utils import setup_logger
from src.rag_pipeline import RAGPipeline

console = Console()


def single_query(
    pipeline: RAGPipeline,
    question: str,
    show_context: bool = False,
    top_k: int = 10,
    rerank: bool = True,
):
    """单次查询"""
    console.print(f"\n[bold cyan]🔍 问题:[/bold cyan] {question}")

    try:
        result = pipeline.query(
            question=question,
            top_k=top_k,
            rerank=rerank,
        )

        # 显示查询类型
        type_colors = {
            "recommendation": "green",
            "factual": "blue",
            "comparison": "yellow",
            "open_ended": "magenta",
            "unknown": "white",
        }
        qtype = result["query_type"]
        color = type_colors.get(qtype, "white")
        console.print(f"[bold]📊 查询类型:[/bold] [{color}]{qtype}[/{color}]")

        # 显示答案
        console.print()
        console.print(Panel(
            Markdown(result["answer"]),
            title="[bold green]💡 回答[/bold green]",
            border_style="green",
            width=100,
        ))

        # 显示检索到的文物（可选）
        if show_context and result["retrieved_chunks"]:
            table = Table(title="📚 检索到的文物", show_header=True)
            table.add_column("#", style="dim")
            table.add_column("文物名称", style="cyan")
            table.add_column("切片类型", style="blue")
            table.add_column("相关度得分", justify="right", style="green")

            for i, chunk in enumerate(result["retrieved_chunks"], 1):
                table.add_row(
                    str(i),
                    chunk["artifact_name"],
                    chunk["chunk_type"],
                    str(chunk["score"]),
                )
            console.print()
            console.print(table)

        return result

    except Exception as e:
        console.print(f"[bold red]❌ 查询失败:[/bold red] {e}")
        logger.error(f"查询失败: {e}")
        return None


def interactive_mode(pipeline: RAGPipeline, rerank: bool = True):
    """交互式问答模式"""
    console.print()
    console.print(Panel.fit(
        "[bold yellow]🦁 文物知识库 RAG 问答系统[/bold yellow]\n\n"
        "[cyan]输入您的问题，我将基于知识库为您解答。\n"
        "输入以下命令可执行特殊操作：[/cyan]\n"
        "  [green]/stats[/green] - 查看知识库统计\n"
        "  [green]/context[/green] - 切换是否显示检索上下文\n"
        "  [green]/rerank[/green] - 切换是否启用重排序\n"
        "  [green]/clear[/green] - 清屏\n"
        "  [green]/exit[/green] 或 [green]/quit[/green] - 退出系统",
        title="欢迎",
        border_style="yellow",
    ))

    show_context = False
    rerank_enabled = rerank

    while True:
        try:
            question = Prompt.ask("\n[bold cyan]您的问题[/bold cyan]")

            if not question.strip():
                continue

            if question.startswith("/"):
                cmd = question.lower().strip()
                if cmd in ("/exit", "/quit", "/q"):
                    console.print("[bold yellow]再见！[/bold yellow]")
                    break
                elif cmd == "/stats":
                    stats = pipeline.get_stats()
                    if "error" in stats:
                        console.print(f"[red]{stats['error']}[/red]")
                    else:
                        table = Table(title="知识库统计")
                        for k, v in stats.items():
                            table.add_row(k, str(v))
                        console.print(table)
                elif cmd == "/context":
                    show_context = not show_context
                    console.print(
                        f"[green]显示上下文已{'开启' if show_context else '关闭'}[/green]"
                    )
                elif cmd == "/rerank":
                    rerank_enabled = not rerank_enabled
                    console.print(
                        f"[green]重排序已{'开启' if rerank_enabled else '关闭'}[/green]"
                    )
                elif cmd == "/clear":
                    console.clear()
                else:
                    console.print(f"[red]未知命令: {cmd}[/red]")
            else:
                single_query(pipeline, question, show_context=show_context, rerank=rerank_enabled)

        except KeyboardInterrupt:
            console.print("\n[bold yellow]再见！[/bold yellow]")
            break
        except Exception as e:
            console.print(f"[bold red]错误:[/bold red] {e}")
            logger.error(f"交互错误: {e}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="文物知识库 RAG 问答系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 交互式模式（默认项目）
  python scripts/run_qa.py

  # 指定项目交互式
  python scripts/run_qa.py --project museum

  # 单次查询
  python scripts/run_qa.py -q "推荐一些代表性的文物"

  # 指定项目单次查询
  python scripts/run_qa.py --project enterprise -q "公司的主要产品"

  # 单次查询并显示检索上下文
  python scripts/run_qa.py -q "司母戊鼎有多重" -c

  # 查看知识库统计
  python scripts/run_qa.py --stats
        """,
    )
    parser.add_argument("-q", "--question", help="要查询的问题（单次查询模式）")
    parser.add_argument("-c", "--context", action="store_true", help="显示检索上下文")
    parser.add_argument("--stats", action="store_true", help="查看知识库统计信息")
    parser.add_argument("--no-rerank", action="store_true", help="禁用重排序")
    parser.add_argument("--top-k", type=int, default=10, help="检索 Top-K 数量")
    parser.add_argument("--project", type=str, default="", help="项目 ID（如 museum、enterprise）")

    args = parser.parse_args()

    # 设置日志
    setup_logger(settings.log_level)

    # 检查 API Key
    try:
        settings.validate_api_key()
    except ValueError as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(1)

    # 初始化 RAG 流水线（支持项目隔离）
    project_id = args.project or None
    pipeline = RAGPipeline(
        local_mode=True,
        memory_mode=settings.qdrant_memory_mode,
        project_id=project_id,
    )

    # 确定切片缓存路径（按项目）
    if project_id:
        from src.project import project_manager
        project_cfg = project_manager.get_project(project_id)
        chunk_paths = [project_cfg.chunk_cache_path]
    else:
        chunk_paths = [
            settings.processed_data_path / "chunks.json",
            settings.processed_data_path / "chunks_documents.json",
        ]

    if not any(p.exists() for p in chunk_paths):
        console.print(
            "[bold yellow]⚠ 知识库尚未构建！[/bold yellow]\n"
            "请先运行: [cyan]python scripts/build_knowledge_base.py --project {}" .format(project_id or "museum") + "[/cyan]"
        )
        sys.exit(1)

    # 加载知识库（从缓存加载 BM25 索引）
    try:
        from src.utils import load_json
        from src.chunking import Chunk
        loaded = False
        for chunk_path in chunk_paths:
            if chunk_path.exists():
                chunk_data = load_json(chunk_path)
                # 过滤 Chunk 不接受的字段，避免缓存格式变更时崩溃（bug-012）
                valid_fields = set(Chunk.__dataclass_fields__.keys())
                chunks = [
                    Chunk(**{k: v for k, v in c.items() if k in valid_fields})
                    for c in chunk_data
                ]
                pipeline.bm25_retriever.build(chunks)
                pipeline._is_built = True
                loaded = True
                logger.info(f"从缓存加载 BM25 索引: {chunk_path} ({len(chunks)} 个切片)")
                break
        if not loaded:
            console.print("[bold red]未找到有效的切片缓存文件[/bold red]")
            sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]加载知识库失败: {e}[/bold red]")
        sys.exit(1)

    if args.stats:
        # 查看统计信息
        stats = pipeline.get_stats()
        if "error" in stats:
            console.print(f"[red]{stats['error']}[/red]")
        else:
            table = Table(title="知识库统计")
            for k, v in stats.items():
                table.add_row(k, str(v))
            console.print(table)
    elif args.question:
        # 单次查询模式（传递 --no-rerank 和 --top-k 参数）
        single_query(
            pipeline,
            args.question,
            show_context=args.context,
            top_k=args.top_k,
            rerank=not args.no_rerank,
        )
    else:
        # 交互式模式
        interactive_mode(pipeline, rerank=not args.no_rerank)


if __name__ == "__main__":
    main()