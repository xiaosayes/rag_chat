<template>
  <div class="chat-panel">
    <img class="back" :src="'img/back.png'" @click.stop.prevent="onBack" />
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
    <div class="status-line" v-if="session.statusText.value">{{ session.statusText.value }}</div>
  </div>
</template>

<script lang="ts" setup>
/** 聊天面板（web-021）：1.3/1.4 设计稿——鹿左用户右、波形 loading、MusicBar 重播。 */
import { nextTick, reactive, ref, watch } from "vue";
import MusicBar from "./MusicBar.vue";
import WaveLoading from "./WaveLoading.vue";
import type { ChatItem, useVoiceSession } from "../voice/useVoiceSession";

const props = defineProps<{ session: ReturnType<typeof useVoiceSession> }>();
const emit = defineEmits(["back"]);
const scrollEl = ref<HTMLDivElement>();

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
  height: 100%;
  .back {
    position: absolute;
    right: 42px;
    top: 19px;
    height: 104px;
    cursor: pointer;
    z-index: 5;
  }
  .chat-scroll {
    height: calc(100% - 77px);
    overflow-y: auto;
    padding: 38px 58px;
    box-sizing: border-box;
    &::-webkit-scrollbar { display: none; }
  }
  .chat-item {
    display: flex;
    align-items: flex-start;
    margin-bottom: 42px;
    &.chat-me { flex-direction: row-reverse; }
    .avatar img {
      width: 123px;
      height: 123px;
      border-radius: 50%;
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
    bottom: 12px;
    left: 0;
    right: 0;
    text-align: center;
    font-size: 24px;
    color: #897967;
  }
}
</style>
