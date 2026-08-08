"""
小虎 QA 评测驱动（单轮 + 多轮）
用法:
  python scripts/qa_harness.py --project jiabohui -q "问题" [--context] [--history "q1|a1;q2|a2"]
  python scripts/qa_harness.py --project jiabohui --file questions.txt   # 每行一个问题，顺序执行
输出: 追加写入 logs/qa_harness_run.log (UTF-8)，同时打印到 stdout
"""
import sys, io, json, argparse, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 强制 UTF-8 输出，避免 Windows GBK 控制台乱码
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from loguru import logger
logger.remove()  # 关闭默认日志，避免干扰

from src.config import settings
from src.rag_pipeline import RAGPipeline

LOG_FILE = Path(__file__).resolve().parent.parent / "logs" / "qa_harness_run.log"


def run_query(pipeline, question, history=None, show_context=False, top_k=10, rerank=True):
    result = pipeline.query(
        question=question,
        top_k=top_k,
        rerank=rerank,
        conversation_history=history if history else None,
    )
    return result


def single(pipeline, question, history=None, show_context=False, top_k=10, rerank=True):
    qid = int(time.time() * 1000) % 1000000
    result = run_query(pipeline, question, history, show_context, top_k, rerank)
    entry = {
        "id": qid,
        "question": question,
        "query_type": result.get("query_type", "?"),
        "answer": result.get("answer", ""),
        "retrieved": [
            {"name": c.get("artifact_name", ""), "type": c.get("chunk_type", ""),
             "score": round(c.get("score", 0), 4), "text": (c.get("text") or "")[:300]}
            for c in (result.get("retrieved_chunks") or [])
        ] if show_context else [],
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(json.dumps(entry, ensure_ascii=False, indent=1))
    return entry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="jiabohui")
    parser.add_argument("-q", "--question")
    parser.add_argument("-f", "--file")
    parser.add_argument("--history", help='形如 "q1|a1;q2|a2"')
    parser.add_argument("-c", "--context", action="store_true")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--no-rerank", action="store_true")
    args = parser.parse_args()

    pipeline = RAGPipeline(local_mode=True, memory_mode=settings.qdrant_memory_mode, project_id=args.project)

    history = None
    if args.history:
        history = []
        for seg in args.history.split(";"):
            q, a = seg.split("|", 1)
            history.append({"role": "user", "content": q})
            history.append({"role": "assistant", "content": a})

    if args.file:
        lines = [l.strip() for l in Path(args.file).read_text(encoding="utf-8").splitlines() if l.strip()]
        for i, q in enumerate(lines, 1):
            print(f"\n===== [{i}/{len(lines)}] {q} =====")
            single(pipeline, q, history, args.context, args.top_k, not args.no_rerank)
    elif args.question:
        single(pipeline, args.question, history, args.context, args.top_k, not args.no_rerank)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()