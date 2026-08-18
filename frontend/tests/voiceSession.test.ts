// web-020：useVoiceSession 事件编排（假 client/player）
import { describe, expect, it, vi } from "vitest";
import { useVoiceSession, type ChatItem } from "../src/voice/useVoiceSession";

function makeDeps() {
  const sent: any[] = [];
  const player = {
    start: vi.fn(), push: vi.fn(), stop: vi.fn(),
  };
  const client = {
    connect: vi.fn(), ask: vi.fn((t: string) => sent.push({ ask: t })),
    bargeIn: vi.fn(() => sent.push({ barge: 1 })), close: vi.fn(),
  };
  const talk: boolean[] = [];
  const session = useVoiceSession({
    client: client as any,
    player: player as any,
    onTalkChange: (t) => talk.push(t),
  });
  return { session, sent, player, client, talk };
}

const PCM = (n: number) => new Int16Array(n).buffer;

describe("useVoiceSession", () => {
  it("主题点缀动作（web-039）：greet 挥手；首 chunk 命中主题发一次", () => {
    const fired: string[] = [];
    const session = useVoiceSession({
      client: { connect: vi.fn(), ask: vi.fn(), bargeIn: vi.fn(), close: vi.fn() } as any,
      player: { start: vi.fn(), push: vi.fn(), stop: vi.fn() } as any,
      onAction: (n) => fired.push(n),
    });
    session.onEvent({ type: "greet" });
    expect(fired).toEqual(["zuoshouhuishou"]);                 // 唤醒应答挥手
    session.askText("谢谢你的解答");
    session.onEvent({ type: "answer_start", turn: 2 });
    session.onEvent({ type: "answer_chunk", turn: 2, text: "不客气，" });
    expect(fired).toHaveLength(2);                             // 问题含「谢谢」→ 比心
    expect(fired[1]).toBe("shuangshoubixin");
    session.onEvent({ type: "answer_chunk", turn: 2, text: "随时问我。" });
    expect(fired).toHaveLength(2);                             // 一轮只发一次
    session.onEvent({ type: "answer_end", turn: 2, full_text: "", cancelled: false });
    session.askText("家博会几点开门");
    session.onEvent({ type: "answer_start", turn: 3 });
    session.onEvent({ type: "answer_chunk", turn: 3, text: "九点开门。" });
    expect(fired).toHaveLength(2);                             // 无命中不发（随机池兜底）
  });

  it("speaking 标志（web-035）：partial 置位，answer_start/聆听态复位", () => {
    const { session } = makeDeps();
    expect(session.speaking.value).toBe(false);
    session.onEvent({ type: "asr_partial", text: "你好" });
    expect(session.speaking.value).toBe(true);
    session.onEvent({ type: "answer_start", turn: 1 });
    expect(session.speaking.value).toBe(false);
    session.onEvent({ type: "asr_partial", text: "在吗" });
    expect(session.speaking.value).toBe(true);
    session.onEvent({ type: "state", mode: "listen", status_text: "" });
    expect(session.speaking.value).toBe(false);
  });

  it("askText：用户气泡 + ask 帧", () => {
    const { session, client } = makeDeps();
    session.askText("  家博会几点开门？ ");
    expect(session.chatHistory[0]).toMatchObject({ type: "me", status: 1 });
    expect(client.ask).toHaveBeenCalledWith("家博会几点开门？");
    session.askText("   ");
    expect(session.chatHistory).toHaveLength(1);   // 空串不发送
  });

  it("回答全链：loading→流式→音频缓存→定稿", () => {
    const { session, player, talk } = makeDeps();
    session.askText("q");
    session.onEvent({ type: "answer_start", turn: 1 });
    const deer = session.chatHistory[1];
    expect(deer.status).toBe(0);                    // 波形 loading
    session.onEvent({ type: "answer_chunk", turn: 1, text: "你好。" });
    session.onEvent({ type: "answer_chunk", turn: 1, text: "世界。" });
    expect(deer.status).toBe(1);
    expect(deer.text).toBe("你好。世界。");
    session.onEvent({ type: "audio_start", turn: 1, format: "pcm_s16le_24k" });
    session.onAudio(PCM(2400));
    session.onAudio(PCM(2400));
    expect(player.push).toHaveBeenCalledTimes(2);
    expect(talk).toContain(true);
    session.onEvent({ type: "audio_end", turn: 1 });
    expect(deer.pcm).toHaveLength(2);               // 端侧缓存落位
    expect(deer.durationS).toBeCloseTo(0.2, 3);
    session.onEvent({ type: "answer_end", turn: 1, full_text: "你好。世界。", cancelled: false });
    expect(deer.text).toBe("你好。世界。");
    expect(talk[talk.length - 1]).toBe(false);
  });

  it("greeting 音频不入轮缓存", () => {
    const { session } = makeDeps();
    session.onEvent({ type: "greet" });
    session.onEvent({ type: "audio_start", turn: 0, greeting: true });
    session.onAudio(PCM(2400));
    session.onEvent({ type: "audio_end", turn: 0, greeting: true });
    expect(session.chatHistory).toHaveLength(0);
  });

  it("asr_partial 更新用户气泡（边说边上屏）", () => {
    const { session } = makeDeps();
    session.onEvent({ type: "asr_partial", text: "家不" });
    expect(session.chatHistory[0].text).toBe("家不");
    session.onEvent({ type: "asr_partial", text: "家博会几点" });
    expect(session.chatHistory[0].text).toBe("家博会几点");
    expect(session.chatHistory).toHaveLength(1);
  });

  it("playback_cancel：停播 + 清缓存 + STANDBY", () => {
    const { session, player, talk } = makeDeps();
    session.askText("q");
    session.onEvent({ type: "answer_start", turn: 1 });
    session.onEvent({ type: "audio_start", turn: 1 });
    session.onAudio(PCM(2400));
    session.onEvent({ type: "playback_cancel" });
    expect(player.stop).toHaveBeenCalled();
    expect(talk[talk.length - 1]).toBe(false);
    session.onEvent({ type: "answer_end", turn: 1, full_text: "半句", cancelled: true });
    expect(session.chatHistory[1].pcm).toBeUndefined();
  });

  it("replay：端侧缓存直接排播", () => {
    const { session, player } = makeDeps();
    session.onEvent({ type: "answer_start", turn: 1 });
    session.onEvent({ type: "audio_start", turn: 1 });
    session.onAudio(PCM(2400));
    session.onEvent({ type: "audio_end", turn: 1 });
    session.onEvent({ type: "answer_end", turn: 1, full_text: "答。", cancelled: false });
    const dur = session.replay(session.chatHistory[0] as ChatItem);
    expect(dur).toBeCloseTo(0.1, 3);
    expect(player.stop).toHaveBeenCalled();
    expect(player.push).toHaveBeenCalledTimes(2);   // 1 次实时 + 1 次重播
  });

  it("state/error 状态行", () => {
    const { session } = makeDeps();
    session.onEvent({ type: "state", mode: "listen", status_text: "👂 倾听中" });
    expect(session.mode.value).toBe("listen");
    expect(session.statusText.value).toBe("👂 倾听中");
    session.onEvent({ type: "error", code: "busy", message: "忙" });
    expect(session.statusText.value).toBe("忙");
  });
});

describe("useAutoChat 语音提问自动跳聊天态（web-042）", () => {
  it("await_broadcast/broadcast 触发跳转；standby/listen 不跳", async () => {
    const { ref, nextTick } = await import("vue");
    const { useAutoChat } = await import("../src/voice/useAutoChat");
    const sessionMode = ref("standby");
    const viewMode = ref("home");
    useAutoChat(sessionMode as any, viewMode);
    sessionMode.value = "listen";            // 唤醒聆听：不跳
    await nextTick();
    expect(viewMode.value).toBe("home");
    sessionMode.value = "await_broadcast";   // 语音提交 → 跳聊天
    await nextTick();
    expect(viewMode.value).toBe("chat");
    viewMode.value = "home";                 // 用户手动返回
    sessionMode.value = "broadcast";         // 新一轮播报 → 再跳
    await nextTick();
    expect(viewMode.value).toBe("chat");
  });
});
