// web-021：ChatPanel 渲染 + useIdleTimer（假定时器）
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChatPanel from "../src/components/ChatPanel.vue";
import { useIdleTimer } from "../src/voice/useIdleTimer";
import { useVoiceSession } from "../src/voice/useVoiceSession";
import { defineComponent, h, onMounted } from "vue";

function makeSession() {
  return useVoiceSession({
    client: { connect: vi.fn(), ask: vi.fn(), bargeIn: vi.fn(), close: vi.fn() } as any,
    player: { start: vi.fn(), push: vi.fn(), stop: vi.fn() } as any,
  });
}

beforeEach(() => setActivePinia(createPinia()));

describe("ChatPanel", () => {
  it("鹿左用户右气泡 + loading 波形", async () => {
    const session = makeSession();
    session.askText("家博会几点开门？");
    session.onEvent({ type: "answer_start", turn: 1 });
    const w = mount(ChatPanel, { props: { session } });
    await w.vm.$nextTick();
    expect(w.find(".chat-me .text").text()).toBe("家博会几点开门？");
    expect(w.find(".chat-deer .waveform").exists()).toBe(true);   // status 0 → loading
    session.onEvent({ type: "answer_chunk", turn: 1, text: "九点开门。" });
    await w.vm.$nextTick();
    expect(w.find(".chat-deer .text").text()).toBe("九点开门。");
  });

  it("有 PCM 缓存的回答显示 MusicBar，点击触发端侧重播", async () => {
    const session = makeSession();
    session.onEvent({ type: "answer_start", turn: 1 });
    session.onEvent({ type: "answer_chunk", turn: 1, text: "答。" });
    session.onEvent({ type: "audio_start", turn: 1 });
    session.onAudio(new Int16Array(2400).buffer);
    session.onEvent({ type: "audio_end", turn: 1 });
    session.onEvent({ type: "answer_end", turn: 1, full_text: "答。", cancelled: false });
    const w = mount(ChatPanel, { props: { session } });
    await w.vm.$nextTick();
    const bar = w.find(".music-bar");
    expect(bar.exists()).toBe(true);
    expect(bar.text()).toContain("00:00/00:00");
    await w.find(".music-bar .state").trigger("click");
    expect((session as any).player.push).toHaveBeenCalled();      // 端侧重播排播
  });

  it("返回：清空历史 + emit back", async () => {
    const session = makeSession();
    session.askText("q");
    const w = mount(ChatPanel, { props: { session } });
    await w.find(".back").trigger("click");
    expect(session.chatHistory).toHaveLength(0);
    expect(w.emitted("back")).toHaveLength(1);
  });
});

describe("useIdleTimer", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("homeAfterS 触发 onHome；pointerdown 复位", () => {
    const onHome = vi.fn();
    const onRefresh = vi.fn();
    const Host = defineComponent({
      setup() {
        useIdleTimer({ homeAfterS: () => 150, refreshAfterS: () => 300, onHome, onRefresh });
        return () => h("div");
      },
    });
    mount(Host);
    vi.advanceTimersByTime(149000);
    window.dispatchEvent(new Event("pointerdown"));   // 复位
    vi.advanceTimersByTime(149000);
    expect(onHome).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1000);
    expect(onHome).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(300000);
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });
});
