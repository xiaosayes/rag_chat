// frontend/tests/storyHome.test.ts
// web-062：useVoiceSession 转接 story_* 事件；其余事件行为不变
import { describe, expect, it, vi } from "vitest";
import { useVoiceSession } from "../src/voice/useVoiceSession";

class FakeWs {
  static last: any;
  readyState = 1; onopen: any; onmessage: any; onclose: any;
  sent: any[] = [];
  constructor(public url: string) { FakeWs.last = this; }
  send(d: any) { this.sent.push(d); }
  close() {}
}
(globalThis as any).WebSocket = FakeWs;

// 既有范式（voiceSession.test.ts）：jsdom 无 AudioContext，注入假 player
const fakePlayer = () => ({ start: vi.fn(), push: vi.fn(), stop: vi.fn() }) as any;

describe("web-062 voice session story relay", () => {
  it("relays story_* events to onStoryEvent, not chat bubbles", () => {
    const storyEvs: any[] = [];
    const s = useVoiceSession({ player: fakePlayer(), onStoryEvent: (e) => storyEvs.push(e) });
    s.connect();
    FakeWs.last.onopen?.({});
    FakeWs.last.onmessage?.({ data: JSON.stringify({ type: "hello", ok: true, voice: false }) });
    FakeWs.last.onmessage?.({ data: JSON.stringify({ type: "story_preparing", theme: "x" }) });
    FakeWs.last.onmessage?.({ data: JSON.stringify({ type: "story_begin", total: 2, pages: [] }) });
    expect(storyEvs.map((e) => e.type)).toEqual(["story_preparing", "story_begin"]);
    expect(s.chatHistory.length).toBe(0);          // 不产生聊天气泡
  });

  it("non-story events unchanged (answer flow)", () => {
    const storyEvs: any[] = [];
    const s = useVoiceSession({ player: fakePlayer(), onStoryEvent: (e) => storyEvs.push(e) });
    s.onEvent({ type: "answer_start", turn: 1 });
    s.onEvent({ type: "answer_chunk", turn: 1, text: "答" });
    expect(storyEvs.length).toBe(0);
    expect(s.chatHistory.length).toBe(1);
  });
});
