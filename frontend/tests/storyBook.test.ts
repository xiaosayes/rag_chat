// frontend/tests/storyBook.test.ts
// web-061：绘本组件——页式渲染/占位→图/翻页边界/结束态/preparing/返回
import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { reactive, ref } from "vue";
import StoryBook from "../src/components/StoryBook.vue";
import storyBookRaw from "../src/components/StoryBook.vue?raw";

function fakeStory() {
  return {
    phase: ref("playing"),
    title: ref("霸王别姬"),
    page: ref(1),
    total: ref(3),
    pages: ref([{ n: 1, text: "第一页文" }, { n: 2, text: "第二页文" }, { n: 3, text: "第三页文" }]),
    images: reactive({}) as Record<number, string>,
    preparingTheme: ref(""),
    errorText: ref(""),
    next: vi.fn(), prev: vi.fn(), back: vi.fn(),
  };
}

describe("web-061 StoryBook", () => {
  it("renders current page text and indicator", () => {
    const w = mount(StoryBook, { props: { story: fakeStory() } });
    expect(w.text()).toContain("第一页文");
    expect(w.text()).toContain("1 / 3");
    expect(w.text()).toContain("霸王别姬");
  });

  it("placeholder before image, img after story_page_img", async () => {
    const st = fakeStory();
    const w = mount(StoryBook, { props: { story: st } });
    expect(w.find(".story-img").exists()).toBe(false);
    expect(w.find(".story-img-placeholder").exists()).toBe(true);
    st.images[1] = "/api/story/s1/img/1";
    await w.vm.$nextTick();
    expect(w.find(".story-img").exists()).toBe(true);
    expect(w.find("img.story-img").attributes("src")).toBe("/api/story/s1/img/1");
  });

  it("flip buttons call next/prev and disable at bounds", async () => {
    const st = fakeStory();
    const w = mount(StoryBook, { props: { story: st } });
    expect((w.find(".btn-prev").element as HTMLButtonElement).disabled).toBe(true);
    await w.find(".btn-next").trigger("click");
    expect(st.next).toHaveBeenCalled();
    st.page.value = 3;
    await w.vm.$nextTick();
    expect((w.find(".btn-next").element as HTMLButtonElement).disabled).toBe(true);
  });

  it("back button emits and calls story.back", async () => {
    const st = fakeStory();
    const w = mount(StoryBook, { props: { story: st } });
    await w.find(".btn-back").trigger("click");
    expect(st.back).toHaveBeenCalled();
    expect(w.emitted("back")).toBeTruthy();
  });

  // web-062 补强（fix round 1 钉住）：故事舞台 z 序防护——
  // .storybook 必须显式 z-index ≥ 60（压过 DeerAvatar z1 / SysMenu z50/60）且有不透明底，
  // 否则同叠层上下文内 z-auto 按 DOM 序绘制，小鹿/SysMenu 会透出在绘本之上。
  // （vitest 未启用 css:true，jsdom 算不出 scoped 样式 → 用 ?raw 钉源文件样式块）
  it("stage z-order guard: .storybook z-index >= 60 with opaque background", () => {
    const m = storyBookRaw.match(/\.storybook\s*\{([\s\S]*?)\n\}/);
    expect(m, ".storybook 根样式块存在").toBeTruthy();
    const block = m![1];
    const z = block.match(/z-index:\s*(\d+)/);
    expect(z, ".storybook 必须显式 z-index（z-auto 会被小鹿 z1/SysMenu z60 压过）").toBeTruthy();
    expect(Number(z![1])).toBeGreaterThanOrEqual(60);
    expect(block).toMatch(/background:/);
  });

  it("preparing and finished overlays", async () => {
    const st = fakeStory();
    st.phase = ref("preparing");
    st.preparingTheme.value = "嫦娥奔月";
    const w = mount(StoryBook, { props: { story: st } });
    expect(w.text()).toContain("嫦娥奔月");
    st.phase.value = "finished";
    await w.vm.$nextTick();
    expect(w.text()).toContain("故事讲完啦");
  });
});
