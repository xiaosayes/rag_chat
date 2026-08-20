// frontend/tests/storyBook.test.ts
// web-061：绘本组件——页式渲染/占位→图/翻页边界/结束态/preparing/返回
import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { reactive, ref } from "vue";
import StoryBook from "../src/components/StoryBook.vue";

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
