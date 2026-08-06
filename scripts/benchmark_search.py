"""
联网搜索性能对比脚本（bug-106）

用法（先跑关闭，再跑开启，对比输出）：
  LLM_ENABLE_SEARCH=false python scripts/benchmark_search.py
  LLM_ENABLE_SEARCH=true python scripts/benchmark_search.py

说明：
  - 关闭缓存（enable_cache=False），避免 LLM/检索缓存命中导致测速失真
  - 每类问题测 3 轮取均值，输出分段耗时（classify/retrieve/rerank/llm/total）
  - 时效性问题触发联网；知识库事实问题不触发（验证无差异）
"""

import sys
import time
import argparse
import statistics
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.table import Table

from src.config import settings
from src.utils import setup_logger
from src.rag_pipeline import RAGPipeline
from src.chunking import Chunk
from src.utils import load_json

console = Console()

# 三类对比问题：触发联网 / 不触发联网 / 开放类（触发联网）
BENCH_QUESTIONS = [
    ("时效性问题（应触发联网）", "最近博物馆有什么新展览"),
    ("知识库事实问题（不应触发联网）", "司母戊鼎是什么时期的青铜器"),
    ("开放类问题（应触发联网）", "谈谈你对文物保护的看法"),
]
ROUNDS = 3


def load_pipeline(project_id: str) -> RAGPipeline:
    """加载知识库（复用 run_qa 的缓存加载逻辑，同时禁用检索与 LLM 缓存）"""
    pipeline = RAGPipeline(
        local_mode=True,
        memory_mode=settings.qdrant_memory_mode,
        project_id=project_id or None,
        enable_cache=False,  # 关闭缓存，保证测速真实
    )
    pipeline.llm.use_cache = False  # 关闭 LLM 缓存

    if project_id:
        from src.project import project_manager
        project_cfg = project_manager.get_project(project_id)
        chunk_paths = [project_cfg.chunk_cache_path]
    else:
        chunk_paths = [
            settings.processed_data_path / "chunks.json",
            settings.processed_data_path / "chunks_documents.json",
        ]

    loaded = False
    for chunk_path in chunk_paths:
        if chunk_path.exists():
            chunk_data = load_json(chunk_path)
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
        console.print("[bold red]未找到切片缓存，请先运行 build_knowledge_base.py[/bold red]")
        sys.exit(1)
    return pipeline


def bench_question(pipeline, question: str, rounds: int):
    """单问题多轮测速，返回 (总耗时列表, 分段timing, search_enabled)"""
    total_times, last_timing, search_enabled = [], None, None
    for _ in range(rounds):
        t0 = time.time()
        result = pipeline.query(question=question, top_k=10, rerank=True)
        total_times.append(time.time() - t0)
        last_timing = result.get("timing", {})
        search_enabled = result.get("search_enabled", None)
    return total_times, last_timing, search_enabled


def main():
    parser = argparse.ArgumentParser(description="联网搜索性能对比")
    parser.add_argument("--project", type=str, default="museum", help="项目 ID")
    parser.add_argument("--rounds", type=int, default=ROUNDS, help="每类问题测试轮数")
    args = parser.parse_args()

    setup_logger(settings.log_level)
    console.print(Panel(
        f"[bold]联网搜索对比[/bold]\n"
        f"LLM_ENABLE_SEARCH = [bold cyan]{settings.llm_enable_search}[/bold cyan]  "
        f"（已开启 {settings.llm_enable_search}）\n"
        f"项目: {args.project} | 每类 {args.rounds} 轮 | 缓存已关闭",
        border_style="yellow",
    ))

    pipeline = load_pipeline(args.project)

    # 预热一轮避免冷启动干扰
    try:
        pipeline.query(question="预热", top_k=10, rerank=True)
    except Exception:
        pass

    table = Table(title="耗时对比（秒）")
    table.add_column("问题类型", style="cyan")
    table.add_column("问题", style="white")
    table.add_column("联网", justify="center")
    table.add_column("总耗时(均值)", justify="right")
    table.add_column("总耗时(各轮)", justify="right")
    table.add_column("llm耗时", justify="right")
    table.add_column("检索耗时", justify="right")

    for label, question in BENCH_QUESTIONS:
        total_times, timing, search_enabled = bench_question(pipeline, question, args.rounds)
        mean = statistics.mean(total_times)
        table.add_row(
            label,
            question[:18],
            "✅" if search_enabled else "—",
            f"{mean:.2f}s",
            "/".join(f"{t:.2f}" for t in total_times),
            f"{timing.get('llm', 0) / 1000:.2f}s",
            f"{timing.get('retrieve', 0) / 1000:.2f}s",
        )
    console.print(table)
    console.print("\n[dim]提示: 分别用 LLM_ENABLE_SEARCH=false / true 运行本脚本，对比相同问题的均值差异。[/dim]")


if __name__ == "__main__":
    from rich.panel import Panel
    from loguru import logger
    main()