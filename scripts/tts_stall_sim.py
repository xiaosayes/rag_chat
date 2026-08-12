"""TTS 播报停顿仿真器 v2（audit-TTS，单会话流式模型）。

模型依据（均为代码/实测实证）：
  - 发布：单会话流式合成，音频连续发布，速率 ~2.6x 实时，首块 t0≈0.6s
    （scripts/measure_tts_firstchunk.py 实测）；可注入中段挂起（看门狗 15s 重建）。
  - 播放器（gradio 6.22 前端 hls.js，StaticAudio-*.js）：
      maxBufferLength（patch 前 1s / patch 后 60s）；
      playlist 重载延迟 zr() = 有更新 ? min(TD, 最后一段时长)（4*TD > 剩余缓冲时）
                                   : TD/2（无更新时）。
  - gradio 服务端 MediaStream.max_duration 每段 +1 单调蠕变（起始 5）→
    TD 随段数膨胀；td_mode="fixed" 模拟 audio_bootstrap.patch_gradio_media_stream_targetduration
    修正后（TD 恒为起始值 5）。

用法：python scripts/tts_stall_sim.py
"""
import heapq
import math


def streaming_publish_times(total_audio: float, batch_dur: float = 0.5,
                            rate: float = 2.6, t0: float = 0.6,
                            hang_at: float = None, hang_dur: float = 0.0) -> list[float]:
    """每批（batch_dur 秒音频）的发布时刻（流式连续发布 + 可选中段挂起）。"""
    n = max(1, round(total_audio / batch_dur))
    hang_audio = None if hang_at is None else max(0.0, hang_at - t0) * rate
    times = []
    for i in range(n):
        end_audio = (i + 1) * batch_dur
        if hang_audio is not None and end_audio > hang_audio:
            t = hang_at + hang_dur + (end_audio - hang_audio) / rate
        else:
            t = t0 + end_audio / rate
        times.append(t)
    return times


def playback_stalls(publish_times: list[float], durations: list[float],
                    buffer_cap: float, td_mode: str = "fixed", dt: float = 0.05) -> dict:
    """hls.js 近似播放模型：返回停顿统计。

    - 首次 playlist 加载发生在首批发布时刻（前端收到首个流式值即建 hls）；
    - 播放器只装载 playlist 中已知段，缓冲上限 buffer_cap 秒；
    - 重载节奏按 zr()：有更新 → min(TD, 最后已知段时长)（4*TD>剩余缓冲），
      无更新 → TD/2；td_mode: "growing"=gradio 蠕变(5+已发布段数) / "fixed"=恒 5；
    - 缓冲耗尽且仍有未播内容 → 记为停顿（直到下次装载）。
    """
    n = len(durations)
    total_audio = sum(durations)
    cum = [0.0]
    for d in durations:
        cum.append(cum[-1] + d)
    td_fixed = 1.0  # patch 后：≤1s 批次 → TD 恒 1（clamp(ceil(段时长),1,5)）

    t = publish_times[0]
    known = loaded = 0
    playhead = 0.0
    next_poll = t
    stalls = []
    cur_stall = 0.0
    t_end_guard = t + total_audio * 3 + 300

    while playhead < total_audio - 1e-9 and t < t_end_guard:
        published = sum(1 for p in publish_times if p <= t + 1e-9)
        if t >= next_poll - 1e-9:  # playlist 重载
            new_known = published
            updated = new_known > known
            known = max(known, new_known)
            td = (5 + published) if td_mode == "growing" else td_fixed
            last_dur = durations[known - 1] if known else durations[0]
            buffer_left = cum[loaded] - playhead
            if updated:
                delay = last_dur if 4 * td > buffer_left else td
            else:
                delay = td / 2
            next_poll = t + max(delay, 0.5)
        while loaded < known and cum[loaded] - playhead < buffer_cap - 1e-9:
            loaded += 1
        if loaded > 0 and cum[loaded] - playhead > 1e-9:
            playhead += dt
            if cur_stall > 0:
                stalls.append(cur_stall)
                cur_stall = 0.0
        else:
            cur_stall += dt
        t += dt

    if cur_stall > 0 and playhead < total_audio - 1e-9:
        stalls.append(cur_stall)
    return {
        "stall_count": len(stalls),
        "max_stall": max(stalls) if stalls else 0.0,
        "total_stall": sum(stalls),
        "finish_time": t,
    }


def run_streaming_config(total_audio: float = 60.0, batch_dur: float = 0.5,
                         rate: float = 2.6, t0: float = 0.6,
                         hang_at: float = None, hang_dur: float = 0.0,
                         buffer_cap: float = 1.0, td_mode: str = "fixed") -> dict:
    """一组（客户端缓冲, TD 模式）配置下的停顿统计。"""
    pub = streaming_publish_times(total_audio, batch_dur, rate, t0, hang_at, hang_dur)
    return playback_stalls(pub, [batch_dur] * len(pub), buffer_cap, td_mode)


if __name__ == "__main__":
    # 60s 回答，t=20s 处 15s 挂起（看门狗阈值）：
    # ① 未 patch（1s 缓冲 + TD 蠕变）→ 长停顿；② 仅 60s 缓冲 → TD 蠕变仍致长停顿；
    # ③ 60s 缓冲 + TD 修正（完整修复）→ 无停顿
    for cap, td in ((1.0, "growing"), (60.0, "growing"), (60.0, "fixed")):
        r = run_streaming_config(hang_at=20.0, hang_dur=15.0, buffer_cap=cap, td_mode=td)
        print(f"buffer={cap:5.1f}s TD={td:8s} → 停顿 {r['stall_count']} 次, "
              f"最长 {r['max_stall']:.2f}s, 合计 {r['total_stall']:.2f}s")
