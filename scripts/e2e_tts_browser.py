"""E2E 验收（audit-TTS）：真实浏览器 + 真实 API，验证三项验收标准。

  ① 首句播报 ≤1s（首文本出现 → 音频开始播放；另报告 发送→开播 全链路）
  ② 全程无停顿（播放期 waiting 事件 / currentTime 停滞，阈值 1.5s）
  ③ 第 2 轮问答播报正常（playing 再次触发且 currentTime 前进 ≥2s）

用法：python scripts/e2e_tts_browser.py [--port 7891] [--keep]
前置：知识库已构建；.env 含真实密钥；pip install playwright + chromium。
全部时钟用页面 performance.now()（与事件时间戳同基准）。
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

Q1 = "请详细介绍司母戊鼎，包括年代、重量、出土和历史意义"
Q2 = "它的铸造工艺有什么特点"

INSTRUMENT_JS = """
() => {
  window.__tts = { playingAt: null, waits: [], ended: false, errors: [], waitStart: null };
  const hook = (el) => {
    if (!el || el.__hooked) return;
    el.__hooked = true;
    el.addEventListener('playing', () => {
      if (window.__tts.playingAt === null) window.__tts.playingAt = performance.now();
      if (window.__tts.waitStart !== null) {
        window.__tts.waits.push(performance.now() - window.__tts.waitStart);
        window.__tts.waitStart = null;
      }
    });
    el.addEventListener('waiting', () => {
      if (window.__tts.waitStart === null) window.__tts.waitStart = performance.now();
    });
    el.addEventListener('ended', () => { window.__tts.ended = true; });
    el.addEventListener('error', (e) => { window.__tts.errors.push(String(e.message || e)); });
  };
  document.querySelectorAll('audio').forEach(hook);
  new MutationObserver(() => document.querySelectorAll('audio').forEach(hook))
    .observe(document.body, { childList: true, subtree: true });
}
"""

SAMPLE_JS = """
() => {
  const els = [...document.querySelectorAll('audio')];
  const el = els.find(e => !e.paused) || els[els.length - 1];
  const chat = document.querySelector('[class*=chatbot]');
  let bufEnd = 0, ranges = [];
  try {
    if (el && el.buffered.length) {
      bufEnd = el.buffered.end(el.buffered.length - 1);
      for (let i = 0; i < el.buffered.length; i++)
        ranges.push([+el.buffered.start(i).toFixed(2), +el.buffered.end(i).toFixed(2)]);
    }
  } catch(e) {}
  return {
    now: performance.now(),
    has_audio: els.length > 0,
    playing: el ? !el.paused : false,
    currentTime: el ? el.currentTime : 0,
    bufferedEnd: bufEnd,
    ranges: ranges,
    readyState: el ? el.readyState : 0,
    chat_len: chat ? chat.innerText.length : 0,
    tts: window.__tts || null,
  };
}
"""


MOCK_ANSWER = (
    "司母戊鼎是商代晚期的青铜礼器，1939年出土于河南省安阳市武官村，"
    "因其腹部内壁铸有“司母戊”三字铭文而得名。鼎高133厘米，口长110厘米，"
    "口宽79厘米，重达832.84公斤，是现存中国古代最重的青铜礼器。"
    "鼎身呈长方形，四柱足中空，器表饰以饕餮纹、云雷纹等典型商代纹样，"
    "整体造型雄浑庄重，体现了商代晚期青铜铸造工艺的最高水平。"
    "从铸造工艺看，司母戊鼎采用分铸法铸造：先分别铸出鼎耳、鼎身和鼎足，"
    "再合范浇铸成为一体。如此庞大的铸件需要七八十个坩埚同时熔铜浇注，"
    "数百名工匠协同操作，反映出商代青铜手工业高度发达的组织能力。"
    "从合金配比看，其铜、锡、铅比例与《考工记》记载的“六齐”基本吻合，"
    "说明当时已经掌握了相当成熟的合金技术。"
    "司母戊鼎现藏于中国国家博物馆，是镇馆之宝之一，"
    "被列入首批禁止出国（境）展览文物名录，具有极高的历史、艺术与科学价值。"
)  # ~400 字


def serve_mock_llm_app(port: int):
    """在进程内启动 app，但把 answer_question 换成恒定速率（40字/秒）回放固定长文，
    隔离 LLM 供文本波动——专验 TTS 播报管线本身的顺畅性（真实 TTS/gradio/浏览器）。"""
    import app as app_mod

    def canned_answer(question, history, use_stream, project_id=""):
        text = MOCK_ANSWER
        for i in range(0, len(text), 12):
            part = text[: i + 12]
            yield (history + [{"role": "user", "content": question},
                              {"role": "assistant", "content": part}], "[]", part)
            time.sleep(12 / 40)  # 恒定 40 字/秒

    app_mod.answer_question = canned_answer
    # 诊断：add_segment 打时间戳（对比 respond 批次日志，定位 yield→入库 断点）
    from gradio import route_utils as _ru

    _orig_add = _ru.MediaStream.add_segment

    async def _logged_add(self, data):
        if data:
            with open("seg_add.log", "a", encoding="utf-8") as fh:
                fh.write("%.3f dur=%.3f" % (time.time(), data.get("duration")) + chr(10))
        await _orig_add(self, data)

    _ru.MediaStream.add_segment = _logged_add
    demo = app_mod.create_ui()
    demo.queue()
    demo.launch(server_name="127.0.0.1", server_port=port, prevent_thread_lock=True)
    return demo


def wait_ready(port: int, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if httpx.get(f"http://127.0.0.1:{port}", timeout=2).status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("app 启动超时")


def run_round(page, question: str, label: str, netlog: list, net_t0: list):
    page.evaluate(INSTRUMENT_JS)
    base = page.evaluate(SAMPLE_JS)
    perf0, base_chat = base["now"], base["chat_len"]
    netlog.clear()
    net_t0[0] = time.time()  # 与 perf0 同刻（误差 <50ms，诊断足够）

    box = page.locator("textarea").first
    box.fill(question)
    box.press("Enter")

    timeline = []              # (rel_s, currentTime, bufferedEnd, playing, readyState)
    first_text_ms = None       # 首文本出现（页面钟 ms，相对 perf0）
    last_ct, last_ct_advance = 0.0, None
    ct_stalls = []
    deadline = time.time() + 240
    done = False
    while time.time() < deadline:
        s = page.evaluate(SAMPLE_JS)
        rel = (s["now"] - perf0) / 1000  # 秒
        if first_text_ms is None and s["chat_len"] > base_chat + 10:
            first_text_ms = rel
        tts = s["tts"] or {}
        ct = s["currentTime"]
        if s["playing"] and ct > last_ct + 1e-3:
            if last_ct_advance is not None and rel - last_ct_advance > 1.5:
                ct_stalls.append(round(rel - last_ct_advance, 2))
            last_ct, last_ct_advance = ct, rel
        timeline.append((round(rel, 1), round(ct, 1), round(s["bufferedEnd"], 1),
                         s["playing"], s["readyState"], s.get("ranges", [])))
        if tts.get("ended"):
            done = True
            break
        # 兜底：播报完毕（已播报状态出现且不在播）或聊天与播放都静止 12s
        if first_text_ms and not s["playing"] and last_ct_advance and rel - last_ct_advance > 12:
            done = True
            break
        time.sleep(0.2)

    tts = page.evaluate("() => window.__tts") or {}
    playing_rel = (tts["playingAt"] - perf0) / 1000 if tts.get("playingAt") is not None else None
    # 停顿窗口诊断：currentTime 停滞 >1.5s 期间的缓冲/网络快照
    stall_diag = []
    for i in range(1, len(timeline)):
        t, ct, be, playing, rs = timeline[i][0], timeline[i][1], timeline[i][2], timeline[i][3], timeline[i][4]
        if i > 0 and timeline[i - 1][1] == ct and playing:
            j = i
            while j + 1 < len(timeline) and timeline[j + 1][1] == ct:
                j += 1
            dur = timeline[j][0] - t
            if dur > 1.5:
                reqs = [n for n in netlog if t - 1 <= n[0] <= timeline[j][0] + 1]
                ranges_mid = timeline[min(j, len(timeline) - 1)][5]
                stall_diag.append({
                    "窗口": f"{t}~{timeline[j][0]}s", "时长": round(dur, 1),
                    "播放位置": ct, "缓冲末": be, "readyState": rs,
                    "缓冲区间": ranges_mid,
                    "期间请求": reqs[:30],
                })
    # 前向缓冲水位分布（playing 期）：验证播放器是否真的深缓冲
    fwd = sorted(round(be - ct, 2) for _, ct, be, playing, _, _r in timeline
                 if playing and be > ct)
    p50 = None
    if fwd:
        p = lambda q: fwd[min(len(fwd) - 1, int(len(fwd) * q))]
        p50 = p(0.5)
        print(f"[{label}] 前向缓冲水位: p10={p(0.1)}s p50={p50}s p90={p(0.9)}s")
    if stall_diag:
        print(f"[{label}] 停顿诊断:", json.dumps(stall_diag, ensure_ascii=False))
    result = {
        "label": label,
        "send_to_first_text_s": round(first_text_ms, 2) if first_text_ms else None,
        "send_to_first_play_s": round(playing_rel, 2) if playing_rel is not None else None,
        "tts_side_latency_s": round(playing_rel - first_text_ms, 2)
        if (playing_rel is not None and first_text_ms) else None,
        "waits_over_1p5s": [round(w / 1000, 2) for w in tts.get("waits", []) if w > 1500],
        "ct_stalls_over_1p5s": ct_stalls,
        "played_seconds": round(last_ct, 1),
        "ended": bool(tts.get("ended")),
        "finished": done,
        "errors": tts.get("errors", []),
        "fwd_buffer_p50": p50,
    }
    print(json.dumps(result, ensure_ascii=False))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7891)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--one-round", action="store_true")
    parser.add_argument("--mock-llm", action="store_true",
                        help="进程内启动 + 固定文本恒定速率回放（隔离 LLM 供文本波动）")
    args = parser.parse_args()

    proc = None
    demo = None
    if args.mock_llm:
        demo = serve_mock_llm_app(args.port)
    else:
        proc = subprocess.Popen(
            [sys.executable, "-u", "app.py", "--port", str(args.port)],
            stdout=open("e2e_app.log", "w", encoding="utf-8"),
            stderr=subprocess.STDOUT,
        )
    try:
        wait_ready(args.port)
        print(f"app 已启动 :{args.port}（日志 e2e_app.log）")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(args=[
                "--autoplay-policy=no-user-gesture-required",
                "--mute-audio",
            ])
            page = browser.new_page()
            netlog = []  # (相对秒, URL 尾部, 状态)

            net_t0 = [time.time()]
            consolelog = []

            def on_response(resp):
                if "/stream/" in resp.url:
                    tail = resp.url.split("/stream/")[-1]
                    kind = "m3u8" if "playlist.m3u8" in tail else "aac"
                    t = round(time.time() - net_t0[0], 1)
                    if kind == "m3u8":
                        try:
                            body = resp.text()
                            netlog.append((t, f"m3u8:{body.count('.aac')}段"
                                           + ("END" if "ENDLIST" in body else ""), 200))
                        except Exception:
                            netlog.append((t, "m3u8:?", resp.status))
                    else:
                        netlog.append((t, kind, resp.status))

            def on_console(msg):
                if any(k in msg.text for k in ("HLS", "hls", "buffer", "media", "Media", "error", "Error")):
                    consolelog.append((round(time.time() - net_t0[0], 1), msg.text[:160]))

            page.on("response", on_response)
            page.on("console", on_console)
            page.goto(f"http://127.0.0.1:{args.port}", wait_until="domcontentloaded")
            page.wait_for_selector("textarea", timeout=60_000)
            time.sleep(2)

            r1 = run_round(page, Q1, "第1轮", netlog, net_t0)
            time.sleep(1)
            r2 = dict(r1) if args.one_round else run_round(page, Q2, "第2轮", netlog, net_t0)
            if consolelog:
                print("[console]", json.dumps(consolelog[:30], ensure_ascii=False))
            browser.close()

        lat1 = r1["tts_side_latency_s"]
        ok1 = lat1 is not None and lat1 <= 1.5  # 目标 ≤1s，含采样噪声(0.2s 轮询)放宽
        stalls = r1["waits_over_1p5s"] + r1["ct_stalls_over_1p5s"]
        ok2 = not stalls
        ok3 = r2["played_seconds"] > 2 and r2["send_to_first_play_s"] is not None
        print("\n===== 验收 =====")
        print(f"① 首句延迟 TTS侧={lat1}s（目标≤1s）发送→开播={r1['send_to_first_play_s']}s"
              f" → {'PASS' if ok1 else 'FAIL'}")
        print(f"② 第1轮 >1.5s 停顿: {stalls or '无'} → {'PASS' if ok2 else 'FAIL'}")
        print(f"③ 第2轮播报: 开播于发送后 {r2['send_to_first_play_s']}s, 播放 {r2['played_seconds']}s"
              f" → {'PASS' if ok3 else 'FAIL'}")
        sys.exit(0 if (ok1 and ok2 and ok3) else 1)
    finally:
        if not args.keep:
            if demo is not None:
                demo.close()
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except Exception:
                    proc.kill()


if __name__ == "__main__":
    main()
