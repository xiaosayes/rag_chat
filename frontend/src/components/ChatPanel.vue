<template>
  <div class="chat-panel">
    <div class="chat-header">
      <img class="back" :src="'img/back.png'" @click.stop.prevent="onBack" />
    </div>
    <div class="chat-scroll" ref="scrollEl">
      <div v-for="(item, k) in session.chatHistory" :key="k"
           :class="`chat-item chat-${item.type}`">
        <template v-if="item.type === 'deer'">
          <div class="avatar"><img :src="'img/v1/avatar.png'" /></div>
          <div class="bubble">
            <WaveLoading v-if="item.status === 0" />
            <template v-else>
              <MusicBar v-if="item.pcm?.length" :durationS="item.durationS ?? 0"
                        :currentS="replayState.k === k ? replayState.s : 0"
                        :playing="replayState.k === k && replayState.playing"
                        @toggle="onReplayToggle(k, item)"
                        @seek="(s: number) => onSeek(k, item, s)" />
              <p class="text">{{ item.text }}</p>
            </template>
          </div>
        </template>
        <template v-else>
          <div class="bubble me"><p class="text">{{ item.text }}</p></div>
          <div class="avatar"><img :src="'img/v1/avatar_me.png'" /></div>
        </template>
      </div>
    </div>
    <div class="status-line" v-if="displayStatus">{{ displayStatus }}</div>
  </div>
</template>

<script lang="ts" setup>
/** 聊天面板（web-021）：1.3/1.4 设计稿——鹿左用户右、波形 loading、MusicBar 重播。 */
import { computed, nextTick, reactive, ref, watch } from "vue";
import MusicBar from "./MusicBar.vue";
import WaveLoading from "./WaveLoading.vue";
import type { ChatItem, useVoiceSession } from "../voice/useVoiceSession";

const props = defineProps<{ session: ReturnType<typeof useVoiceSession> }>();
const emit = defineEmits(["back"]);
const scrollEl = ref<HTMLDivElement>();

// web-037：状态行去开头小图标（冻结 FSM 状态文本含前导 emoji，客户端剥离）
const displayStatus = computed(() =>
  (props.session.statusText.value ?? "").replace(/^[^\u4e00-\u9fa5\w]+\s*/u, ""),
);

const replayState = reactive({ k: -1, s: 0, playing: false });
let replayTimer = 0;

function onReplayToggle(k: number, item: ChatItem) {
  if (replayState.k === k && replayState.playing) {
    props.session.barge();           // 重播中再点 = 停（走播放器 stop）
    stopReplayTracker();
    return;
  }
  startReplay(k, item, 0);
}

function onSeek(k: number, item: ChatItem, targetS: number) {
  startReplay(k, item, targetS);     // web-026：拖拽/点击 seek（整帧粒度）
}

function startReplay(k: number, item: ChatItem, fromS: number) {
  const dur = props.session.replay(item, fromS);
  if (dur <= 0) return;
  replayState.k = k;
  replayState.s = fromS;
  replayState.playing = true;
  const t0 = Date.now();
  clearInterval(replayTimer);
  replayTimer = window.setInterval(() => {
    replayState.s = fromS + (Date.now() - t0) / 1000;
    if (replayState.s >= fromS + dur) stopReplayTracker();
  }, 200);
}

function stopReplayTracker() {
  replayState.playing = false;
  clearInterval(replayTimer);
}

function onBack() {
  stopReplayTracker();
  props.session.resetChat();
  emit("back");
}

watch(
  () => props.session.chatHistory.map((i) => i.text.length).join(","),
  async () => {
    await nextTick();
    const el = scrollEl.value;
    if (el) el.scrollTop = el.scrollHeight;
  },
);
</script>

<style lang="scss" scoped>
.chat-panel {
  position: relative;
  width: 100%;
  flex: 1;                   /* web-038：面板剩余空间全部给聊天区（固定窗口） */
  min-height: 0;             /* flex 子项可收缩，内部滚动生效的前提 */
  display: flex;
  flex-direction: column;
  .chat-header {             /* web-038：返回钮独立头行（文档流内），不再遮挡气泡 */
    flex: none;
    display: flex;
    justify-content: flex-end;
    align-items: center;
    height: 116px;           /* web-042：返回钮加大更醒目 */
    padding: 0 30px;
    box-sizing: border-box;
  }
  .back {
    height: 104px;           /* 80→104px（web-042） */
    cursor: pointer;
    filter: drop-shadow(0 3px 6px rgba(74, 63, 48, 0.35));  /* 与羊皮纸底色分离 */
  }
  .chat-scroll {
    flex: 1;
    min-height: 0;
    overflow-y: auto;        /* 内容长时内部上下滑动浏览 */
    margin-bottom: 84px;     /* web-042：视口收进屏幕内，下方留状态行区 */
    padding: 10px 58px 48px; /* 末条气泡停在渐隐带之上 */
    box-sizing: border-box;
    /* web-042：底部渐隐——长内容流式中段不再硬切顶到屏幕边缘（美化） */
    mask-image: linear-gradient(to bottom, #000 calc(100% - 44px), transparent 100%);
    -webkit-mask-image: linear-gradient(to bottom, #000 calc(100% - 44px), transparent 100%);
    &::-webkit-scrollbar { display: none; }
  }
  .chat-item {
    display: flex;
    align-items: flex-start;
    margin-bottom: 42px;
    &.chat-me { flex-direction: row-reverse; }
    .avatar img {
      width: 115px;      /* web-037：对齐参考 6.013vh + height:auto 保自然宽高比，
                            去掉强压方框+圆裁（avatar_me 295×157 被压失真） */
      height: auto;
    }
    .bubble {
      max-width: 62%;
      margin: 0 31px;
      background: rgba(255, 250, 235, 0.92);
      border-radius: 31px;
      padding: 23px 35px;
      &.me { background: rgba(255, 255, 255, 0.95); }
      .text {
        font-family: "Source Han Serif CN", serif;
        font-size: 30px;
        color: #4a3f30;
        line-height: 1.65;
        margin: 0;
        word-break: break-all;
      }
    }
  }
  .status-line {
    position: absolute;
    bottom: 30px;        /* web-037：上移不压底框线；字号 24→26 增大一号 */
    left: 0;
    right: 0;
    text-align: center;
    font-size: 26px;
    color: #897967;
  }
}
</style>
