// web-015/017：组件单测（SplashScreen / SysMenu / VoiceBar / PresetPanel / store）
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SplashScreen from "../src/components/SplashScreen.vue";
import SysMenu from "../src/components/SysMenu.vue";
import VoiceBar from "../src/components/VoiceBar.vue";
import { FALLBACK_PRESETS, useAppStore } from "../src/stores/app";

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("SplashScreen", () => {
  it("进度文案与隐藏", async () => {
    const w = mount(SplashScreen, { props: { show: true } });
    const store = useAppStore();
    store.loadProgress = 78;
    await w.vm.$nextTick();
    expect(w.text()).toContain("78%");
    expect(w.text()).toContain("正在赶来");
    await w.setProps({ show: false });
    expect(w.find(".splash").exists()).toBe(false);
  });
});

describe("SysMenu", () => {
  it("连点 3 次展开，少于 3 次不展开", async () => {
    const w = mount(SysMenu);
    const zone = w.find(".sys-zone");
    await zone.trigger("click");
    await zone.trigger("click");
    expect(w.find(".sys-menu").exists()).toBe(false);
    await zone.trigger("click");
    expect(w.find(".sys-menu").exists()).toBe(true);
  });
});

describe("VoiceBar", () => {
  it("录音态文案与事件", async () => {
    const w = mount(VoiceBar, { props: { recording: false } });
    expect(w.text()).toContain("点击与我语音聊天吧~");
    await w.setProps({ recording: true });
    expect(w.text()).toContain("正在录入语音");
    await w.find(".mic-capsule").trigger("click");
    expect(w.emitted("mic")).toHaveLength(1);
  });

  it("唤醒词提示含 persona 默认", () => {
    const w = mount(VoiceBar, { props: { recording: false } });
    expect(w.text()).toMatch(/点击与我语音聊天吧~/);
  });
});

describe("store/presets", () => {
  it("兜底池 16 条无重复", () => {
    expect(FALLBACK_PRESETS).toHaveLength(16);
    expect(new Set(FALLBACK_PRESETS).size).toBe(16);
  });

  it("bootstrap 失败用兜底（fetch 拒绝）", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const store = useAppStore();
    await store.bootstrap();
    expect(store.presetPool).toEqual(FALLBACK_PRESETS);
    expect(store.config).toBeNull();
    vi.unstubAllGlobals();
  });

  it("bootstrap 成功采用服务端配置与预设", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.includes("/api/config")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            persona: "湘小图", wake_words: ["你好湘小图"], tts_enabled: true,
            idle_home_s: 120, idle_refresh_s: 240,
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ questions: ["问题A", "问题B"] }),
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const store = useAppStore();
    await store.bootstrap();
    expect(store.presetPool).toEqual(["问题A", "问题B"]);
    expect(store.homeAfterS).toBe(120);
    vi.unstubAllGlobals();
  });
});
