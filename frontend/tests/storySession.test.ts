// frontend/tests/storySession.test.ts
// web-060：绘本会话——事件流转/自动推进（双序）/乐观翻页/取消不回弹
import { describe, expect, it, vi } from "vitest";
import { useStorySession } from "../src/voice/useStorySession";

function make() {
  const sent: any[] = [];
  const client: any = {
    storyPage: (n: number) => sent.push({ type: "story_page", n }),
    storyFinish: () => sent.push({ type: "story_finish" }),
    storyCancel: () => sent.push({ type: "story_cancel" }),
  };
  let ended: (() => void) | null = null;
  const player: any = {
    playing: false,
    stop: vi.fn(),
    set onEnded(fn: any) { ended = fn; },
    get onEnded() { return ended; },
  };
  const phases: string[] = [];
  const s = useStorySession({ client, player, onPhaseChange: (p) => phases.push(p) });
  return { s, sent, player, phases, drain: () => ended?.() };
}

const begin = {
  type: "story_begin", story_id: "s1", title: "霸王别姬", total: 3, cached: false,
  pages: [{ n: 1, text: "一" }, { n: 2, text: "二" }, { n: 3, text: "三" }],
};

describe("web-060 useStorySession", () => {
  it("event flow preparing→playing→img", () => {
    const { s } = make();
    s.handleEvent({ type: "story_preparing", theme: "霸王别姬" });
    expect(s.phase.value).toBe("preparing");
    s.handleEvent(begin);
    expect(s.phase.value).toBe("playing");
    expect(s.page.value).toBe(1);
    s.handleEvent({ type: "story_page_img", n: 2, url: "/api/story/s1/img/2" });
    expect(s.images[2]).toBe("/api/story/s1/img/2");
  });

  it("auto-advance: speak_end then drain", () => {
    const { s, sent, player, drain } = make();
    s.handleEvent(begin);
    s.handleEvent({ type: "story_speak_end", n: 1, cancelled: false });
    player.playing = true;
    drain();                                    // 播尽 → 翻第 2 页
    expect(s.page.value).toBe(2);
    expect(sent).toContainEqual({ type: "story_page", n: 2 });
  });

  it("auto-advance: drain before speak_end (reverse order)", () => {
    const { s, sent, player, drain } = make();
    s.handleEvent(begin);
    player.playing = false;
    drain();                                    // 先排空（guard 未满足，不动）
    expect(s.page.value).toBe(1);
    s.handleEvent({ type: "story_speak_end", n: 1, cancelled: false });
    expect(s.page.value).toBe(2);               // speak_end 到达时已在播尽态 → 立即推进
  });

  it("cancelled speak_end never advances", () => {
    const { s, player, drain } = make();
    s.handleEvent(begin);
    s.handleEvent({ type: "story_speak_end", n: 1, cancelled: true });
    player.playing = false;
    drain();
    expect(s.page.value).toBe(1);
  });

  it("manual flip optimistic + local stop", () => {
    const { s, sent, player } = make();
    s.handleEvent(begin);
    s.next();
    expect(s.page.value).toBe(2);
    expect(player.stop).toHaveBeenCalled();     // 立马静音（对齐 web-047）
    expect(sent).toContainEqual({ type: "story_page", n: 2 });
    s.prev();
    expect(s.page.value).toBe(1);
    s.prev();
    expect(s.page.value).toBe(1);               // 边界钳制
  });

  it("last page drain sends finish; story_end done → finished", () => {
    const { s, sent, player, drain, phases } = make();
    s.handleEvent(begin);
    s.goTo(3);
    s.handleEvent({ type: "story_speak_end", n: 3, cancelled: false });
    player.playing = false;
    drain();
    expect(sent).toContainEqual({ type: "story_finish" });
    s.handleEvent({ type: "story_end", reason: "done" });
    expect(s.phase.value).toBe("finished");
  });

  it("back cancels and resets", () => {
    const { s, sent } = make();
    s.handleEvent(begin);
    s.back();
    expect(sent).toContainEqual({ type: "story_cancel" });
    expect(s.phase.value).toBe("idle");
  });

  it("story_error surfaces message and idles", () => {
    const { s } = make();
    s.handleEvent({ type: "story_preparing", theme: "x" });
    s.handleEvent({ type: "story_error", code: "moderation", message: "换一个试试吧" });
    expect(s.phase.value).toBe("idle");
    expect(s.errorText.value).toContain("换一个");
  });

  // web-060 补强（实施修正钉桩）：speak_end 单独到达（音频仍在播、未排尽）不得推进——
  // 双序护栏两个条件都齐才翻页，否则 goTo 里的 player.stop() 会截掉当页尾部音频。
  it("speak_end alone (still playing) does not advance", () => {
    const { s, sent } = make();
    s.handleEvent(begin);
    s.handleEvent({ type: "story_speak_start", n: 1 });   // 服务端开始播第 1 页
    s.handleEvent({ type: "story_speak_end", n: 1, cancelled: false });
    expect(s.page.value).toBe(1);                          // 未播尽不推进（防截尾）
    expect(sent).toHaveLength(0);
  });
});
