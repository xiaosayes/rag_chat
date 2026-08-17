<template>
  <div class="viewport">
    <div class="stage" :style="stageStyle">
      <router-view />
    </div>
  </div>
</template>

<script lang="ts" setup>
/** 舞台适配（web-034）：1080×1920 设计坐标系定版，整台等比缩放适配任意窗口。
 *  一体机 1080×1920 下 1:1 像素级精确；PC/大屏自动 letterbox 居中（比例永不变形）。 */
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useAppStore } from "./stores/app";

const DESIGN_W = 1080;
const DESIGN_H = 1920;
const scale = ref(1);

function fit() {
  scale.value = Math.min(window.innerWidth / DESIGN_W, window.innerHeight / DESIGN_H);
}

onMounted(() => {
  fit();
  window.addEventListener("resize", fit);
});
onBeforeUnmount(() => window.removeEventListener("resize", fit));

const stageStyle = computed(() => ({
  width: `${DESIGN_W}px`,
  height: `${DESIGN_H}px`,
  transform: `translate(-50%, -50%) scale(${scale.value})`,
}));

const store = useAppStore();
onMounted(() => store.bootstrap());
</script>

<style lang="scss">
@font-face {
  font-family: "Source Han Serif CN";
  src: url("/fonts/SourceHanSerifCN-Medium.otf");
  font-display: swap;
}
@font-face {
  font-family: "Source Han Serif CN";
  src: url("/fonts/SourceHanSerifCN-Bold.otf");
  font-weight: bold;
  font-display: swap;
}
html, body, #app {
  width: 100%;
  height: 100%;
  border: 0;
  margin: 0;
  padding: 0;
  overflow: hidden;
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
  background: #1a2b20;   /* letterbox 边（与森林主题相融） */
}
img { -webkit-user-drag: none; }
.viewport {
  position: fixed;
  inset: 0;
}
.stage {
  position: absolute;
  left: 50%;
  top: 50%;
  transform-origin: center center;
  overflow: hidden;
}
</style>
