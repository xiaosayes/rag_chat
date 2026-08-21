// web-023/024：键盘输入 + 手写板（jsdom 真渲染 simple-keyboard；OCR mock）
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import KeyboardInput from "../src/components/KeyboardInput.vue";
import HandwritingPad from "../src/components/HandwritingPad.vue";

vi.mock("../src/api/client", () => ({
  api: {
    ocr: vi.fn(),
  },
}));
vi.mock("../src/input/signaturePad", () => ({
  createSignaturePad: vi.fn(() => {
    const listeners: Record<string, (() => void)[]> = {};   // 每实例独立
    return {
      clear: vi.fn(),
      isEmpty: vi.fn(() => false),
      toDataURL: vi.fn(() => "data:image/png;base64,eA=="),
      addEventListener: (type: string, cb: () => void) => {
        (listeners[type] ??= []).push(cb);
      },
      __fire: (type: string) => listeners[type]?.forEach((cb) => cb()),
    };
  }),
}));

import { api } from "../src/api/client";
import { createSignaturePad } from "../src/input/signaturePad";

/** 取最近一次 mount 创建的假手写板实例 */
function lastPad() {
  const results = vi.mocked(createSignaturePad).mock.results;
  return results[results.length - 1].value;
}

describe("KeyboardInput", () => {
  it("键盘渲染：QWERTY + 功能键（手写/空格/退格/完成/Aa）", async () => {
    const w = mount(KeyboardInput);
    await w.vm.$nextTick();
    const buttons = w.findAll(".hg-button");
    const labels = buttons.map((b) => b.text());
    expect(labels).toContain("q");                       // 默认小写布局
    expect(labels).toContain("手写");
    expect(labels).toContain("完成");
    expect(labels).toContain("Aa");
    expect(labels).toContain("⌫");
  });

  it("Aa 切换大小写布局", async () => {
    const w = mount(KeyboardInput);
    await w.vm.$nextTick();
    const shift = w.findAll(".hg-button").find((b) => b.text() === "Aa");
    await shift!.trigger("click");
    expect(w.findAll(".hg-button").map((b) => b.text())).toContain("Q");
  });

  it("拼音输入出候选条，点选上屏入输入框", async () => {
    const w = mount(KeyboardInput);
    await w.vm.$nextTick();
    const host = w.find(".simple-keyboard-host");
    for (const ch of ["n", "i"]) {                       // 输入 ni
      const btn = host.findAll(".hg-button").find((b) => b.text() === ch);
      expect(btn, `缺少按键 ${ch}`).toBeTruthy();
      await btn!.trigger("click");
    }
    await new Promise((r) => setTimeout(r, 50));
    // web-070：自绘候选条（.pinyin-cand 取代 simple-keyboard 内置候选框）
    const candidate = host.findAll(".pinyin-cand").find((el) => el.text() === "你");
    expect(candidate, "候选条应含「你」").toBeTruthy();
    await candidate!.trigger("click");
    expect((w.vm as any).value).toContain("你");
  });

  it("连字拼音：tuzi 词候选「兔子」一次上屏（web-070）", async () => {
    const w = mount(KeyboardInput);
    await w.vm.$nextTick();
    const host = w.find(".simple-keyboard-host");
    for (const ch of ["t", "u", "z", "i"]) {
      const btn = host.findAll(".hg-button").find((b) => b.text() === ch);
      await btn!.trigger("click");
    }
    await new Promise((r) => setTimeout(r, 50));
    const wordCand = host.findAll(".pinyin-cand-word")
      .find((el) => el.text() === "兔子");
    expect(wordCand, "词候选条应含「兔子」").toBeTruthy();
    await wordCand!.trigger("click");
    expect((w.vm as any).value).toBe("兔子");             // 整词上屏，buffer 清空
  });

  it("连打余字母继续组词：tuzi 选「兔」后 zi 出「子」（web-070）", async () => {
    const w = mount(KeyboardInput);
    await w.vm.$nextTick();
    const host = w.find(".simple-keyboard-host");
    for (const ch of ["t", "u", "z", "i"]) {
      const btn = host.findAll(".hg-button").find((b) => b.text() === ch);
      await btn!.trigger("click");
    }
    await new Promise((r) => setTimeout(r, 50));
    const tu = host.findAll(".pinyin-cand-char").find((el) => el.text() === "兔");
    expect(tu, "单字候选应含「兔」").toBeTruthy();
    await tu!.trigger("click");                          // eat=2 → buffer 剩 zi
    await new Promise((r) => setTimeout(r, 50));
    const zi = host.findAll(".pinyin-cand").find((el) => el.text() === "子");
    expect(zi, "buffer 余 zi 应出「子」").toBeTruthy();
    await zi!.trigger("click");
    expect((w.vm as any).value).toBe("兔子");
  });

  it("发送：非空才 emit 并清空", async () => {
    const w = mount(KeyboardInput);
    await w.vm.$nextTick();
    await w.find(".send").trigger("click");
    expect(w.emitted("send")).toBeUndefined();        // 空值不发送
    (w.vm as any).value = "测试问题";
    await w.find(".send").trigger("click");
    expect(w.emitted("send")).toEqual([["测试问题"]]);
    expect((w.vm as any).value).toBe("");
  });

  it("✕清空后无陈旧候选条残留（web-070 评审修复）", async () => {
    const w = mount(KeyboardInput);
    await w.vm.$nextTick();
    const host = w.find(".simple-keyboard-host");
    for (const ch of ["n", "i"]) {
      const btn = host.findAll(".hg-button").find((b) => b.text() === ch);
      await btn!.trigger("click");
    }
    await new Promise((r) => setTimeout(r, 50));
    expect(host.findAll(".pinyin-cand").length).toBeGreaterThan(0);   // 有候选
    await w.find(".clear").trigger("click");                          // ✕ = clear+重建
    await w.vm.$nextTick();
    const bars = w.findAll(".pinyin-candidates");
    expect(bars).toHaveLength(1);                                     // 无累积
    expect(bars[0].findAll(".pinyin-cand")).toHaveLength(0);         // 无陈旧候选
  });

  it("完成收起后重开键盘不空白（web-070 评审修复）", async () => {
    const w = mount(KeyboardInput);
    await w.vm.$nextTick();
    const finish = w.findAll(".hg-button").find((b) => b.text() === "完成");
    await finish!.trigger("click");
    await w.vm.$nextTick();
    expect(w.find(".keyboard-panel").exists()).toBe(false);           // 已收起
    await w.find(".field").trigger("click");                          // 点输入框重开
    await w.vm.$nextTick();
    await new Promise((r) => setTimeout(r, 50));
    expect(w.find(".keyboard-panel").exists()).toBe(true);
    expect(w.findAll(".hg-button").length).toBeGreaterThan(20);       // 键盘重建非空白
  });

  it("切手写：键盘收起 + HandwritingPad 出现", async () => {
    const w = mount(KeyboardInput);
    await w.vm.$nextTick();
    const writeBtn = w.findAll(".hg-button").find((b) => b.text() === "手写");
    await writeBtn!.trigger("click");
    expect(w.find(".handwriting-pad").exists()).toBe(true);
    expect(w.find(".keyboard-panel").exists()).toBe(false);
  });
});

describe("HandwritingPad", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.mocked(api.ocr).mockReset();
  });
  afterEach(() => vi.useRealTimers());

  it("停笔 2s 触发 OCR，识别文本经 commit 追加", async () => {
    vi.mocked(api.ocr).mockResolvedValue({ text: "你" });
    const w = mount(HandwritingPad);
    const pad = lastPad();
    pad.__fire("beginStroke");
    pad.__fire("endStroke");
    vi.advanceTimersByTime(1900);
    expect(api.ocr).not.toHaveBeenCalled();
    vi.advanceTimersByTime(200);
    await flushPromises();                               // 等 OCR 链路微任务
    expect(api.ocr).toHaveBeenCalledTimes(1);
    expect(w.emitted("commit")).toEqual([["你"]]);
  });

  it("OCR 失败提示，不清空画布", async () => {
    vi.mocked(api.ocr).mockRejectedValue(new Error("HTTP 502"));
    const w = mount(HandwritingPad);
    const pad = lastPad();
    pad.__fire("beginStroke");
    pad.__fire("endStroke");
    vi.advanceTimersByTime(2000);
    await flushPromises();
    expect(w.text()).toContain("识别失败");
    expect(pad.clear).not.toHaveBeenCalled();
  });

  it("空格/退格/完成语义", async () => {
    const w = mount(HandwritingPad);
    const btns = w.findAll(".btn");
    await btns[1].trigger("click");                    // 空格
    expect(w.emitted("commit")).toEqual([[" "]]);
    await btns[2].trigger("click");                    // 退格
    expect(w.emitted("backspace")).toHaveLength(1);
    await btns[3].trigger("click");                    // 完成 → close
    expect(w.emitted("close")).toHaveLength(1);
  });
});
