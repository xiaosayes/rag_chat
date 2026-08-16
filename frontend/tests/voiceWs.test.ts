// web-019：VoiceWsClient（FakeWebSocket + 假定时器）
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VoiceWsClient, type WsLike } from "../src/voice/VoiceWsClient";

class FakeWs implements WsLike {
  static instances: FakeWs[] = [];
  readyState = 0;
  binaryType = "";
  sent: (string | ArrayBuffer)[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: any }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: ((e: any) => void) | null = null;
  url: string;
  constructor(url: string) {
    this.url = url;
    FakeWs.instances.push(this);
  }
  send(d: string | ArrayBuffer) {
    this.sent.push(d);
  }
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
  // 测试驱动
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  recv(obj: any) {
    this.onmessage?.({ data: JSON.stringify(obj) });
  }
  recvBinary(buf: ArrayBuffer) {
    this.onmessage?.({ data: buf });
  }
  drop() {          // 异常断线（非显式 close）
    this.readyState = 3;
    this.onclose?.();
  }
}

let client: VoiceWsClient;
let events: { opened: boolean[]; closed: number; evs: any[]; audio: number };

beforeEach(() => {
  FakeWs.instances = [];
  events = { opened: [], closed: 0, evs: [], audio: 0 };
  vi.useFakeTimers();
  client = new VoiceWsClient(
    {
      onOpen: (v) => events.opened.push(v),
      onClose: () => events.closed++,
      onEvent: (ev) => events.evs.push(ev),
      onAudio: () => events.audio++,
    },
    { baseUrl: "ws://server:7861", wsFactory: (url) => new FakeWs(url), pingIntervalS: 30 },
  );
});

afterEach(() => {
  client.close();
  vi.useRealTimers();
});

describe("VoiceWsClient", () => {
  it("连接即发 hello；hello 回包带 voice 标志走 onOpen", () => {
    client.connect();
    const ws = FakeWs.instances[0];
    expect(ws.url).toBe("ws://server:7861/ws/voice");
    ws.open();
    expect(JSON.parse(ws.sent[0] as string)).toEqual({ type: "hello" });
    ws.recv({ type: "hello", ok: true, voice: true, version: "0.1.0" });
    expect(events.opened).toEqual([true]);
  });

  it("ask/barge_in/ping 帧格式", () => {
    client.connect();
    const ws = FakeWs.instances[0];
    ws.open();
    client.ask("家博会几点开门");
    client.bargeIn();
    expect(JSON.parse(ws.sent[1] as string)).toEqual({ type: "ask", text: "家博会几点开门" });
    expect(JSON.parse(ws.sent[2] as string)).toEqual({ type: "barge_in" });
    vi.advanceTimersByTime(31000);
    expect(JSON.parse(ws.sent[3] as string)).toEqual({ type: "ping" });
  });

  it("binary 下行走 onAudio；JSON 事件走 onEvent", () => {
    client.connect();
    const ws = FakeWs.instances[0];
    ws.open();
    ws.recv({ type: "answer_start", turn: 1 });
    ws.recvBinary(new Int16Array(240).buffer);
    expect(events.evs).toEqual([{ type: "answer_start", turn: 1 }]);
    expect(events.audio).toBe(1);
  });

  it("断线指数退避重连（1s→2s→5s 封顶；未 open 的失败连尝试才升级）", () => {
    client.connect();
    FakeWs.instances[0].drop();            // 未 open 即失败 → attempt 不归零
    expect(events.closed).toBe(1);
    vi.advanceTimersByTime(1000);          // 1s 后重连
    expect(FakeWs.instances).toHaveLength(2);
    FakeWs.instances[1].drop();
    vi.advanceTimersByTime(2000);          // 2s
    expect(FakeWs.instances).toHaveLength(3);
    FakeWs.instances[2].drop();
    vi.advanceTimersByTime(4999);
    expect(FakeWs.instances).toHaveLength(3);
    vi.advanceTimersByTime(1);             // 5s 封顶
    expect(FakeWs.instances).toHaveLength(4);
  });

  it("成功 open 后断线：退避归零（1s 快重连）", () => {
    client.connect();
    FakeWs.instances[0].open();
    FakeWs.instances[0].drop();
    vi.advanceTimersByTime(1000);
    expect(FakeWs.instances).toHaveLength(2);
    FakeWs.instances[1].open();
    FakeWs.instances[1].drop();
    vi.advanceTimersByTime(1000);          // 再次成功 → 仍 1s
    expect(FakeWs.instances).toHaveLength(3);
  });

  it("显式 close 不重连", () => {
    client.connect();
    FakeWs.instances[0].open();
    client.close();
    vi.advanceTimersByTime(10000);
    expect(FakeWs.instances).toHaveLength(1);
  });

  it("token 走 query 参数", () => {
    const c2 = new VoiceWsClient({}, {
      baseUrl: "ws://server:7861", token: "s3cret",
      wsFactory: (url) => new FakeWs(url),
    });
    c2.connect();
    expect(FakeWs.instances[FakeWs.instances.length - 1].url)
      .toBe("ws://server:7861/ws/voice?token=s3cret");
    c2.close();
  });
});
