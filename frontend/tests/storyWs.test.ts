// frontend/tests/storyWs.test.ts
// web-059：VoiceWsClient 故事消息发送 + story_* 事件泛型透传
import { describe, expect, it, vi } from "vitest";
import { VoiceWsClient } from "../src/voice/VoiceWsClient";

class FakeWs {
  static last: FakeWs;
  sent: any[] = [];
  readyState = 1;
  onopen: any; onmessage: any; onclose: any;
  constructor(public url: string) { FakeWs.last = this; }
  send(d: any) { this.sent.push(typeof d === "string" ? JSON.parse(d) : d); }
  close() {}
}
(globalThis as any).WebSocket = FakeWs;

function make() {
  const events: any[] = [];
  const c = new VoiceWsClient({ onEvent: (e) => events.push(e) });
  c.connect();
  FakeWs.last.onopen?.({});
  FakeWs.last.onmessage?.({ data: JSON.stringify({ type: "hello", ok: true, voice: false }) });
  return { c, events };
}

describe("web-059 story ws", () => {
  it("sends story messages", () => {
    const { c } = make();
    c.storyPage(3); c.storyFinish(); c.storyCancel();
    const types = FakeWs.last.sent.map((m) => `${m.type}${m.n ?? ""}`);
    expect(types).toContain("story_page3");
    expect(types).toContain("story_finish");
    expect(types).toContain("story_cancel");
  });

  it("passes story events through untouched", () => {
    const { events } = make();
    const ev = { type: "story_begin", story_id: "x", total: 8, pages: [] };
    FakeWs.last.onmessage?.({ data: JSON.stringify(ev) });
    expect(events).toContainEqual(ev);
  });
});
