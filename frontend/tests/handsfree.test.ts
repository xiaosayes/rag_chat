// web-025/026：免提闭环 + MusicBar seek（假 capture/ws + 假定时器）
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { defineComponent, h, ref } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useHandsfree } from "../src/voice/useHandsfree";
import { useVoiceSession } from "../src/voice/useVoiceSession";
import VoiceBar from "../src/components/VoiceBar.vue";

function makeSession() {
  return useVoiceSession({
    client: { connect: vi.fn(), ask: vi.fn(), bargeIn: vi.fn(), close: vi.fn(),
              sendAudio: vi.fn() } as any,
    player: { start: vi.fn(), push: vi.fn(), stop: vi.fn() } as any,
  });
}

function mountHandsfree(modelReady: () => boolean, captureFn?: any) {
  const session = makeSession();
  let api: any;
  mount(defineComponent({
    setup() {
      api = useHandsfree({
        session,
        modelReady,
        startCaptureFn: captureFn,
      });
      return () => h("div");
    },
  }));
  return { session, api: api! };
}

beforeEach(() => setActivePinia(createPinia()));

describe("useHandsfree", () => {
  it("模型就绪 → 自动建连 + 自动开麦常开推流", async () => {
    const ready = ref(false);
    const sent: ArrayBuffer[] = [];
    const captureFn = vi.fn(async (cb: (b: ArrayBuffer) => void) => {
      cb(new Int16Array(320).buffer);       // 模拟一帧
      return { stop: vi.fn() };
    });
    const { session, api } = mountHandsfree(() => ready.value, captureFn);
    expect(captureFn).not.toHaveBeenCalled();
    ready.value = true;
    await new Promise((r) => setTimeout(r, 10));
    expect(captureFn).toHaveBeenCalledTimes(1);
    expect(session.client.connect).toHaveBeenCalled();
    expect(api.capturing.value).toBe(true);
    expect(session.recording.value).toBe(true);
  });

  it("开麦失败 → micFailed + 可重试", async () => {
    const ready = ref(true);
    const captureFn = vi.fn()
      .mockRejectedValueOnce(new Error("denied"))
      .mockResolvedValueOnce({ stop: vi.fn() });
    const { api } = mountHandsfree(() => ready.value, captureFn);
    await new Promise((r) => setTimeout(r, 10));
    expect(api.micFailed.value).toBe(true);
    expect(api.capturing.value).toBe(false);
    await api.start();                       // 手动重试成功
    expect(api.capturing.value).toBe(true);
  });
});

describe("VoiceBar 打断态", () => {
  it("播报中胶囊显示「说话或点按可打断」", async () => {
    const w = mount(VoiceBar, { props: { interruptible: true } });
    expect(w.text()).toContain("说话或点按可打断");
    await w.find(".mic-capsule").trigger("click");
    expect(w.emitted("mic")).toHaveLength(1);
  });
});

describe("replay 偏移（seek）", () => {
  it("fromS 按整帧丢弃前缀并返回剩余时长", () => {
    const session = makeSession();
    session.onEvent({ type: "answer_start", turn: 1 });
    session.onEvent({ type: "audio_start", turn: 1 });
    // 5 帧 × 0.1s
    for (let i = 0; i < 5; i++) session.onAudio(new Int16Array(2400).buffer);
    session.onEvent({ type: "audio_end", turn: 1 });
    session.onEvent({ type: "answer_end", turn: 1, full_text: "答。", cancelled: false });
    const item = session.chatHistory[0];
    const player = (session as any).player;
    player.push.mockClear();
    const remain = session.replay(item, 0.25);   // 丢弃 2 帧（0.2s）
    expect(player.push).toHaveBeenCalledTimes(3);
    expect(remain).toBeCloseTo(0.3, 3);
  });
});
