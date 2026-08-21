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
    s.handleEvent({ type: "story_page_img", n: 1, url: "/x" });   // web-064：推进需图已了结
    s.handleEvent({ type: "story_speak_end", n: 1, cancelled: false });
    player.playing = true;
    drain();                                    // 播尽 → 翻第 2 页
    expect(s.page.value).toBe(2);
    expect(sent).toContainEqual({ type: "story_page", n: 2 });
  });

  it("auto-advance: drain before speak_end (reverse order)", () => {
    const { s, sent, player, drain } = make();
    s.handleEvent(begin);
    s.handleEvent({ type: "story_page_img", n: 1, url: "/x" });   // web-064
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
    s.handleEvent({ type: "story_page_img", n: 3, url: "/x" });   // web-064
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

  // web-063 终审 F4：story_error 可见化——停留 preparing 盖层展示拒讲话术，
  // STORY_ERROR_HOLD_MS(2500) 后才转 idle（不静默弹回首页）
  it("story_error holds preparing then idles after hold window", () => {
    vi.useFakeTimers();
    try {
      const { s } = make();
      s.handleEvent({ type: "story_preparing", theme: "x" });
      s.handleEvent({ type: "story_error", code: "moderation", message: "换一个试试吧" });
      expect(s.errorText.value).toContain("换一个");
      expect(s.phase.value).not.toBe("idle");
      vi.advanceTimersByTime(2499);
      expect(s.phase.value).not.toBe("idle");
      vi.advanceTimersByTime(1);
      expect(s.phase.value).toBe("idle");
    } finally {
      vi.useRealTimers();
    }
  });

  it("story_end{error} after story_error also holds (server error pair)", () => {
    vi.useFakeTimers();
    try {
      const { s } = make();
      s.handleEvent({ type: "story_preparing", theme: "x" });
      s.handleEvent({ type: "story_error", code: "script_failed", message: "失败" });
      s.handleEvent({ type: "story_end", reason: "error" });
      expect(s.phase.value).not.toBe("idle");
      vi.advanceTimersByTime(2500);
      expect(s.phase.value).toBe("idle");
    } finally {
      vi.useRealTimers();
    }
  });

  it("stale error timer does not kill a new story (back clears timer)", () => {
    vi.useFakeTimers();
    try {
      const { s } = make();
      s.handleEvent({ type: "story_preparing", theme: "x" });
      s.handleEvent({ type: "story_error", code: "m", message: "x" });
      s.back();
      expect(s.phase.value).toBe("idle");
      s.handleEvent({ type: "story_preparing", theme: "新故事" });
      vi.advanceTimersByTime(5000);
      expect(s.phase.value).toBe("preparing");      // 无迟到 setPhase(idle) 反弹
    } finally {
      vi.useRealTimers();
    }
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

  // web-060 补强（fix round 1 钉桩）：无 TTS 降级路径——服务端 tts=None 时无
  // story_speak_start、无任何 PCM，speak_end 必须视为已排尽并推进（否则永卡第 1 页）。
  it("no-TTS fallback: speak_end without speak_start advances", () => {
    const { s, sent } = make();
    s.handleEvent(begin);
    s.handleEvent({ type: "story_page_img", n: 1, url: "/x" });   // web-064
    s.handleEvent({ type: "story_speak_end", n: 1, cancelled: false });  // 无 speak_start
    expect(s.page.value).toBe(2);
    expect(sent).toContainEqual({ type: "story_page", n: 2 });
  });

  // ---------- web-064：自动翻页等插图就绪（手动翻页不受限） ----------

  it("auto-advance waits for image ready", () => {
    const { s, sent, player, drain } = make();
    s.handleEvent(begin);
    s.handleEvent({ type: "story_speak_end", n: 1, cancelled: false });
    player.playing = false;
    drain();
    expect(s.page.value).toBe(1);                 // 播完+排尽但图未就绪 → 不自动翻页
    expect(sent).toHaveLength(0);
    s.handleEvent({ type: "story_page_img", n: 1, url: "/api/story/s1/img/1" });
    expect(s.page.value).toBe(2);                 // 图就绪补齐条件 → 推进
    expect(sent).toContainEqual({ type: "story_page", n: 2 });
  });

  it("failed image event unblocks auto-advance", () => {
    const { s, sent, player, drain } = make();
    s.handleEvent(begin);
    s.handleEvent({ type: "story_speak_end", n: 1, cancelled: false });
    player.playing = false;
    drain();
    expect(s.page.value).toBe(1);
    s.handleEvent({ type: "story_page_img", n: 1, url: null, failed: true });
    expect(s.page.value).toBe(2);                 // 失败落地=已了结，不卡页
    expect(s.images[1]).toBeUndefined();          // 失败不写 images
    expect(s.imageFailed[1]).toBe(true);
  });

  it("manual flip ignores image readiness", () => {
    const { s, sent } = make();
    s.handleEvent(begin);
    s.next();                                     // 无图也照翻（手动不受等图限制）
    expect(s.page.value).toBe(2);
    expect(sent).toContainEqual({ type: "story_page", n: 2 });
  });
});
