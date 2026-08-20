/** 绘本会话（web-060）：story_* 事件 → 页码/插图/阶段；自动推进=播尽(onEnded)+speak_end 双序护栏。 */
import { reactive, ref } from "vue";
import type { Ref } from "vue";
import type { VoiceWsClient } from "./VoiceWsClient";
import type { PcmPlayer } from "../audio/player";

export type StoryPhase = "idle" | "preparing" | "playing" | "finished";

export function useStorySession(deps: {
  client: VoiceWsClient;
  player: PcmPlayer;
  onPhaseChange?: (p: StoryPhase) => void;
}) {
  const phase: Ref<StoryPhase> = ref("idle");
  const title = ref("");
  const page = ref(1);
  const total = ref(0);
  const pages: Ref<{ n: number; text: string }[]> = ref([]);
  const images: Record<number, string> = reactive({});
  const preparingTheme = ref("");
  const errorText = ref("");
  let speakEndPage = 0;          // 最后一个非 cancel 的 speak_end 页码
  // web-060 实施修正：drained 初值必须为 false，且 story_speak_start 时复位——
  // 初值 true 会让 speak_end 单独满足护栏提前翻页，goTo 的 player.stop() 截掉当页尾部音频。
  let drained = false;           // 播放器已排空（无在途音频）
  // web-060 补强（fix round 1）：本页是否见过 speak_start——无 TTS 降级路径
  // （服务端 tts=None）无 speak_start 也无 PCM，speak_end 即视为已排尽，防永卡第 1 页。
  let speakStarted = false;

  function setPhase(p: StoryPhase) {
    phase.value = p;
    deps.onPhaseChange?.(p);
  }

  function advance() {
    if (phase.value !== "playing") return;
    if (page.value < total.value) {
      goTo(page.value + 1);
    } else {
      deps.client.storyFinish();
    }
  }

  function maybeAdvance() {
    // 双序护栏：speak_end(page) 与播尽两个条件都齐才推进（乱序各触发一次检查）
    if (speakEndPage === page.value && drained) advance();
  }

  function handleEvent(ev: any) {
    switch (ev.type) {
      case "story_preparing":
        preparingTheme.value = ev.theme ?? "";
        errorText.value = "";
        setPhase("preparing");
        break;
      case "story_begin":
        title.value = ev.title ?? "";
        total.value = ev.total ?? 0;
        pages.value = ev.pages ?? [];
        page.value = 1;
        speakEndPage = 0;
        speakStarted = false;
        drained = false;                       // 新故事：音频未开播，未排尽
        Object.keys(images).forEach((k) => delete images[Number(k)]);
        setPhase("playing");
        break;
      case "story_page_img":
        images[ev.n] = ev.url;
        break;
      case "story_speak_start":
        speakStarted = true;
        drained = false;                       // 新页音频开播 → 未排尽（护栏关键复位）
        break;
      case "story_speak_end":
        if (ev.cancelled) break;
        if (!speakStarted) drained = true;     // 无 TTS：本页无音频，视为已排尽
        speakEndPage = ev.n;
        maybeAdvance();
        break;
      case "story_end":
        setPhase(ev.reason === "done" ? "finished" : "idle");
        break;
      case "story_error":
        errorText.value = ev.message ?? "故事生成失败";
        setPhase("idle");
        break;
    }
  }

  function goTo(n: number) {
    if (phase.value !== "playing" && phase.value !== "finished") return;
    const target = Math.min(Math.max(1, n), total.value || 1);
    if (target === page.value) return;
    page.value = target;                          // 乐观翻页
    speakEndPage = 0;
    speakStarted = false;                         // 新页 speak_start 未至
    deps.player.stop();                           // 本地立即静音（web-047 对齐）
    drained = true;                               // stop 后队列已空=已排尽
    deps.client.storyPage(target);
  }

  deps.player.onEnded = () => {
    drained = true;
    maybeAdvance();
  };

  // 提升为作用域函数——brief 原稿在对象字面量里引用自身属性名导致 ReferenceError（实测修正）
  function reset() {
    setPhase("idle");
    page.value = 1;
    speakEndPage = 0;
    speakStarted = false;                         // 复位语义完整（fix round 1 Minor-2）
    drained = false;
  }

  return {
    phase, title, page, total, pages, images, preparingTheme, errorText,
    handleEvent, goTo,
    next: () => goTo(page.value + 1),
    prev: () => goTo(page.value - 1),
    back: () => { deps.player.stop(); deps.client.storyCancel(); reset(); },
    reset,
  };
}
