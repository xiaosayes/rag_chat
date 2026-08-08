"""
直接调用服务器前端问答接口（Gradio 6 Call API）进行真实测试
用法:
  python scripts/gradio_call.py --url http://<server-ip>:7860 -q "问题"
  python scripts/gradio_call.py --url http://<server-ip>:7860 --file questions.txt [-c]
  python scripts/gradio_call.py --url http://<server-ip>:7860 --info    # 探测接口信息
"""
import sys, io, json, argparse, time, re
from pathlib import Path

import urllib.request

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

LOG_FILE = Path(__file__).resolve().parent.parent / "logs" / "gradio_call_run.log"


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "qa-probe"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def http_post_json(url, payload, timeout=60):
    """POST JSON，返回解析后的 JSON 对象（Gradio 6 返回 {"event_id": ...}）"""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "qa-probe",
                 "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def http_get_stream(url, timeout=180):
    """GET 并逐行读取 SSE 流"""
    req = urllib.request.Request(url, headers={"User-Agent": "qa-probe", "Accept": "text/event-stream"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", errors="replace").strip()
            if line.startswith("data:") and len(line) > 5:
                yield line[5:].strip()


def get_api_name(base):
    """从 /gradio_api/info 解析 respond 的 api_name"""
    try:
        status, body = http_get(base + "/gradio_api/info")
        info = json.loads(body)
        eps = info.get("named_endpoints", {})
        for name in eps:
            if "respond" in str(name):
                return name.lstrip("/")
        for name in eps:
            return name.lstrip("/")
        return "respond"
    except Exception as e:
        print(f"[warn] 解析 info 失败({e})，使用默认 api_name=respond")
        return "respond"



def call_question(base, api_name, question, history=None, stream=True, project="jiabohui", show_context=False):
    # Gradio 6 协议：POST 创建事件 → 得 event_id → GET 拉 SSE 流
    # SSE data 为 JSON 数组: [msg回显, history, chunks_json]
    # 回答文本在 history[-1].content[0].text（assistant 累计）
    post_url = f"{base}/gradio_api/call/{api_name}"
    payload = {"data": [question, history or [], stream, project]}
    try:
        res = http_post_json(post_url, payload)
    except Exception as e:
        return f"[POST失败] {e}", None
    event_id = res.get("event_id")
    if not event_id:
        return f"[无event_id] {res}", None
    get_url = f"{post_url}/{event_id}"
    last_answer = ""
    chunks = None
    for ev in http_get_stream(get_url):
        if not ev:
            continue
        try:
            arr = json.loads(ev)
        except Exception:
            continue
        if not isinstance(arr, list) or len(arr) < 2:
            # 兼容 dict 事件（error 等）
            if isinstance(arr, dict) and arr.get("type") == "error":
                last_answer = f"[服务端错误] {arr.get('output')}"
            continue
        hist = arr[1]
        if isinstance(hist, list) and hist:
            last_msg = hist[-1]
            try:
                content = last_msg.get("content") if isinstance(last_msg, dict) else None
                if isinstance(content, list):
                    texts = [c.get("text", "") for c in content if isinstance(c, dict)]
                    if texts:
                        last_answer = "".join(texts)
                elif isinstance(content, str):
                    last_answer = content
            except Exception:
                pass
        if len(arr) >= 3:
            chunks = arr[2]
    return last_answer, chunks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="服务器地址，如 http://192.168.1.10:7860")
    parser.add_argument("-q", "--question")
    parser.add_argument("-f", "--file")
    parser.add_argument("--info", action="store_true", help="仅探测接口信息")
    parser.add_argument("-c", "--context", action="store_true", help="显示检索结果")
    parser.add_argument("--no-stream", action="store_true", help="非流式(对应前端取消勾选)")
    parser.add_argument("--history", help='多轮历史 JSON: [{"role":"user","content":[{"text":"q","type":"text"}]},...]')
    parser.add_argument("--project", default="jiabohui")
    args = parser.parse_args()

    base = args.url.rstrip("/")
    print(f"[连接] {base}")

    # 探测
    try:
        status, body = http_get(base + "/")
        print(f"[在线] 根路径 HTTP {status}, {len(body)} bytes")
    except Exception as e:
        print(f"[失败] 无法连接服务器: {e}")
        sys.exit(1)

    api_name = get_api_name(base)
    print(f"[接口] /gradio_api/call/{api_name}")

    if args.info:
        try:
            status, body = http_get(base + "/gradio_api/info")
            print("[info] 可用 API:", body[:800])
        except Exception as e:
            print("[info] 获取失败:", e)
        return

    questions = []
    if args.file:
        questions = [l.strip() for l in Path(args.file).read_text(encoding="utf-8").splitlines() if l.strip()]
    elif args.question:
        questions = [args.question]
    else:
        parser.print_help()
        return

    for i, q in enumerate(questions, 1):
        print(f"\n===== [{i}/{len(questions)}] {q} =====")
        t0 = time.time()
        try:
            hist = None
            if args.history:
                hist = json.loads(args.history)
            answer, chunks = call_question(
                base, api_name, q,
                history=hist, stream=not args.no_stream,
                project=args.project, show_context=args.context,
            )
            dt = time.time() - t0
            print(f"[耗时 {dt:.1f}s]")
            print(answer[:600] if answer else "[无回答]")
            entry = {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "url": base, "question": q, "answer": answer,
                "chunks": chunks[:5] if isinstance(chunks, list) else chunks,
            }
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[异常] {e}")


if __name__ == "__main__":
    main()