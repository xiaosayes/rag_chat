<template>
  <div class="music-bar">
    <div class="bar-list" ref="barListEl" @click.stop.prevent="onSeek">
      <div v-for="(bar, i) in bars" :key="i"
           :class="['bar', { finished: i < playedBars }]"
           :style="{ height: bar.height + 'vh', animationDuration: bar.duration + 's' }"></div>
    </div>
    <span class="time">{{ fmt(currentS) }}/{{ fmt(durationS) }}</span>
    <img class="state" :src="playing ? 'img/audio_stop.png' : 'img/audio_play.png'"
         @click.stop.prevent="$emit('toggle')" />
  </div>
</template>

<script lang="ts" setup>
/** 音频进度波形（web-021）：移植参考 MusicBar.vue 外观（1.4 语音回答稿）。
 *  播放进度由父组件驱动（currentS）；点击播放/暂停；拖拽 seek 留 M7。 */
import { computed, onMounted, ref } from "vue";

const props = withDefaults(defineProps<{
  durationS: number;
  currentS: number;
  playing: boolean;
}>(), { durationS: 0, currentS: 0, playing: false });
const emit = defineEmits(["toggle", "seek"]);
const barListEl = ref<HTMLDivElement>();

function onSeek(e: MouseEvent) {
  const el = barListEl.value;
  if (!el || props.durationS <= 0) return;
  const rect = el.getBoundingClientRect();
  const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
  emit("seek", ratio * props.durationS);   // 目标秒数
}

const BAR_COUNT = 32;
const bars = ref<{ height: number; duration: number }[]>([]);
onMounted(() => {
  bars.value = Array.from({ length: BAR_COUNT }, () => ({
    height: Math.random() * 1.2916 + 1,
    duration: Math.random() * 0.6 + 0.7,
  }));
});

const playedBars = computed(() =>
  props.durationS > 0
    ? Math.round((Math.min(props.currentS, props.durationS) / props.durationS) * BAR_COUNT)
    : 0);

function fmt(s: number): string {
  const sec = Math.max(0, Math.round(s));
  return `${String(Math.floor(sec / 60)).padStart(2, "0")}:${String(sec % 60).padStart(2, "0")}`;
}
</script>

<style lang="scss" scoped>
.music-bar {
  display: flex;
  align-items: center;
  gap: 1.2vh;
  padding: 0.8vh 1vh;
  .bar-list {
    display: flex;
    align-items: flex-end;
    min-width: 20vh;
  }
  .bar {
    width: 0.2401vh;
    background-color: #c8bbab;
    margin-right: 0.32vh;
    border-radius: 4px;
    &.finished { background-color: #897967; }
  }
  .time {
    font-size: 24px;
    color: #897967;
    white-space: nowrap;
  }
  .state {
    height: 3.6vh;
    cursor: pointer;
  }
}
</style>
