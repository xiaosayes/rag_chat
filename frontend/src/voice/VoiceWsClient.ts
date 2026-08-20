/** 语音 WS 客户端（web-019）：对接 kiosk_server /ws/voice 单通道契约。
 *  自动 hello、心跳 ping、断线指数退避重连（1s→2s→5s 封顶）、显式 close 不重连。 */

export interface VoiceEvents {
  onOpen?: (voice: boolean) => void;
  onClose?: () => void;
  onEvent?: (ev: any) => void;
  onAudio?: (pcm: ArrayBuffer) => void;
}

export interface WsLike {
  readyState: number;
  binaryType: string;
  onopen: (() => void) | null;
  onmessage: ((e: { data: any }) => void) | null;
  onclose: (() => void) | null;
  onerror: ((e: any) => void) | null;
  send(data: string | ArrayBuffer): void;
  close(): void;
}

export type WsFactory = (url: string) => WsLike;

const defaultFactory: WsFactory = (url) => {
  const ws = new WebSocket(url);
  ws.binaryType = "arraybuffer";
  return ws as unknown as WsLike;
};

export class VoiceWsClient {
  private ws: WsLike | null = null;
  private intentionalClose = false;
  private reconnectAttempt = 0;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private events: VoiceEvents,
    private opts: {
      baseUrl?: string;       // 默认从 VITE_API_URL 推导（http→ws）
      token?: string;
      pingIntervalS?: number;
      wsFactory?: WsFactory;
    } = {},
  ) {}

  private get url(): string {
    let base = this.opts.baseUrl;
    if (!base) {
      const api = (import.meta.env.VITE_API_URL as string) || "";
      base = api ? api.replace(/^http/, "ws")
        : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}`;
    }
    const token = this.opts.token ? `?token=${encodeURIComponent(this.opts.token)}` : "";
    return `${base}/ws/voice${token}`;
  }

  get connected(): boolean {
    return this.ws?.readyState === 1;
  }

  connect(): void {
    // 幂等：已有连接（连接中/已开）不重复建连（web-025 免提自动开麦后手动路径可重入）
    if (this.ws && this.ws.readyState < 2) return;
    this.intentionalClose = false;
    const factory = this.opts.wsFactory ?? defaultFactory;
    const ws = factory(this.url);
    this.ws = ws;
    ws.onopen = () => {
      this.reconnectAttempt = 0;
      this.send({ type: "hello" });
      this.pingTimer = setInterval(() => this.send({ type: "ping" }),
        (this.opts.pingIntervalS ?? 30) * 1000);
    };
    ws.onmessage = (e) => {
      if (typeof e.data === "string") {
        const ev = JSON.parse(e.data);
        if (ev.type === "hello") {
          this.events.onOpen?.(!!ev.voice);
          return;
        }
        this.events.onEvent?.(ev);
      } else {
        this.events.onAudio?.(e.data as ArrayBuffer);
      }
    };
    ws.onclose = () => {
      this.clearTimers();
      this.events.onClose?.();
      if (!this.intentionalClose) this.scheduleReconnect();
    };
    ws.onerror = () => undefined;   // close 事件随后即达，统一走重连
  }

  send(obj: Record<string, unknown>): void {
    if (this.connected) this.ws!.send(JSON.stringify(obj));
  }

  sendAudio(pcm: ArrayBuffer): void {
    if (this.connected) this.ws!.send(pcm);
  }

  ask(text: string): void {
    this.send({ type: "ask", text });
  }

  bargeIn(): void {
    this.send({ type: "barge_in" });
  }

  storyPage(n: number): void {
    this.send({ type: "story_page", n });
  }

  storyFinish(): void {
    this.send({ type: "story_finish" });
  }

  storyCancel(): void {
    this.send({ type: "story_cancel" });
  }

  close(): void {
    this.intentionalClose = true;
    this.clearTimers();
    this.ws?.close();
    this.ws = null;
  }

  private clearTimers(): void {
    if (this.pingTimer) clearInterval(this.pingTimer);
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.pingTimer = this.reconnectTimer = null;
  }

  private scheduleReconnect(): void {
    // 指数退避：1s → 2s → 5s 封顶
    const delays = [1000, 2000, 5000];
    const delay = delays[Math.min(this.reconnectAttempt, delays.length - 1)];
    this.reconnectAttempt++;
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }
}
