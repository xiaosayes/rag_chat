"""
多轮追问场景测试：连续发送追问，验证上下文保持
场景：路线→餐饮→休息；展位→展馆→交通；报名→证件→门票；布展→材料→截止
"""
import sys, io, json, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from loguru import logger
logger.remove()
from src.config import settings
from src.rag_pipeline import RAGPipeline

LOG_FILE = Path(__file__).resolve().parent.parent / "logs" / "qa_multiturn.log"

SCENARIOS = [
    {
        "name": "MT1-导览路线追问",
        "turns": [
            "我想去家博会看家具，从哪开始逛比较好？",
            "那逛累了吃饭的地方在哪？",
            "休息区呢？",
            "洗手间呢？",
        ],
    },
    {
        "name": "MT2-展位信息追问",
        "turns": [
            "我家展位号是5.2，这是什么意思？",
            "那这个展馆在广州会展中心的哪个区？",
            "怎么过去最快？",
        ],
    },
    {
        "name": "MT3-报名入场追问",
        "turns": [
            "我想报名参观家博会，怎么报名？",
            "那入场需要什么证件？",
            "门票多少钱？",
        ],
    },
    {
        "name": "MT4-参展流程追问",
        "turns": [
            "我是参展商，布展时间怎么安排？",
            "参展要提交哪些材料？",
            "截止到什么时候？",
            "材料交晚了会怎样？",
        ],
    },
]

def main():
    pipeline = RAGPipeline(local_mode=True, memory_mode=settings.qdrant_memory_mode, project_id="jiabohui")
    for sc in SCENARIOS:
        print(f"\n########## 场景: {sc['name']} ##########")
        session = {"scenario": sc["name"], "turns": [], "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        history = []
        for i, q in enumerate(sc["turns"], 1):
            print(f"\n--- 第{i}轮: {q}")
            result = pipeline.query(question=q, conversation_history=history if history else None)
            answer = result.get("answer", "")
            qtype = result.get("query_type", "?")
            print(f"[{qtype}] {answer[:200]}")
            session["turns"].append({
                "turn": i, "question": q, "query_type": qtype, "answer": answer,
                "history_len": len(history),
            })
            history.append({"role": "user", "content": q})
            history.append({"role": "assistant", "content": answer})
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(session, ensure_ascii=False) + "\n")

if __name__ == "__main__":
    main()