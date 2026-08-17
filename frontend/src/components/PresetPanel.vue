<template>
  <div class="preset-panel">
    <img class="refresh" :src="'img/v1/refresh.png'" @click.stop.prevent="reshuffle" />
    <transition-group name="list" tag="div" class="preset-list">
      <p v-for="(q, k) in shown" :key="q" :class="`preset-item item-${k}`"
         @click.stop.prevent="$emit('select', q)">{{ q }}</p>
    </transition-group>
    <div class="reshuffle-text" @click.stop.prevent="reshuffle">换<br />一<br />批</div>
  </div>
</template>

<script lang="ts" setup>
/** 预设问题面板（web-017）：1.1 设计稿——8 条交错排布 + 换一批。
 *  数据：store.presetPool（/api/presets 下发，失败用兜底池）；前端随机抽 8 条，
 *  「换一批」= 重抽（零额外请求，M1 既定契约）。 */
import { computed, onMounted, ref } from "vue";
import { useAppStore } from "../stores/app";

defineEmits(["select"]);
const store = useAppStore();
const shown = ref<string[]>([]);

function sample(pool: string[], n: number): string[] {
  const arr = [...pool];
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr.slice(0, Math.min(n, arr.length));
}

function reshuffle() {
  shown.value = sample(store.presetPool, 8);
}

onMounted(async () => {
  if (!store.config) await store.bootstrap();
  reshuffle();
});

defineExpose({ reshuffle, shown: computed(() => shown.value) });
</script>

<style lang="scss" scoped>
.preset-panel {
  position: relative;
  width: 100%;
  .refresh {
    position: absolute;
    right: 77px;
    top: -88px;
    height: 65px;
    cursor: pointer;
    display: none; /* 设计稿换一批为竖排文字钮，refresh 图标留作扩展位 */
  }
  .preset-list {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 38px 230px 0;
  }
  .preset-item {
    font-family: "Source Han Serif CN", serif;
    font-size: 34px;
    color: #6d5a42;
    margin: 26px 0;
    cursor: pointer;
    &:nth-child(odd) { transform: translateX(-58px); opacity: 0.92; }
    &:nth-child(even) { transform: translateX(58px); }
    &:active { opacity: 0.6; }
  }
  .reshuffle-text {
    position: absolute;
    right: 42px;
    top: 154px;
    font-family: "Source Han Serif CN", serif;
    font-size: 30px;
    color: #6d5a42;
    background: rgba(255, 250, 235, 0.85);
    border-radius: 19px;
    padding: 23px 15px;
    text-align: center;
    line-height: 1.5;
    cursor: pointer;
    &:active { opacity: 0.6; }
  }
}
.list-enter-active { transition: all 0.4s ease; }
.list-enter-from { opacity: 0; transform: translateX(-115px); }
</style>
