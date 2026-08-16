<template>
  <div v-if="show" class="splash" :class="{ fade: done }">
    <img class="logo" :src="'img/logo.png'" alt="logo" />
    <video class="deer" :src="'video/o.webm'" muted loop autoplay playsinline
           :controls="false"></video>
    <div class="progress-bg">
      <div class="progress" :style="{ width: `${progress}%` }">
        <div class="stripes"></div>
      </div>
    </div>
    <span class="tip">{{ persona }}正在赶来...<span class="num">{{ progress }}%</span></span>
  </div>
</template>

<script lang="ts" setup>
/** 启动页（web-015）：0启动页设计稿——logo + 小鹿视频 + 条纹进度条 + 真实加载进度。 */
import { computed } from "vue";
import { useAppStore } from "../stores/app";

const props = defineProps<{ show: boolean }>();
const store = useAppStore();
const progress = computed(() => Math.floor(store.loadProgress));
const persona = computed(() => store.config?.persona ?? "湘小图");
const done = computed(() => progress.value >= 100);
</script>

<style lang="scss" scoped>
@keyframes moveStripes {
  from { background-position: 0 0; }
  to { background-position: 500000px 0; }
}
.splash {
  position: fixed;
  inset: 0;
  z-index: 999;
  background: url("/img/loading_bg.png") 100% 100% no-repeat;
  background-size: 100% 100%;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  transition: opacity 0.5s;
  &.fade { opacity: 0; pointer-events: none; }
  .logo { width: 17.8125vh; margin-bottom: 4vh; }
  .deer { width: 26vh; margin-bottom: 3vh; }
  .progress-bg {
    width: 34vh;
    height: 2.2vh;
    border-radius: 2vh;
    background: rgba(255, 255, 255, 0.55);
    overflow: hidden;
    .progress {
      height: 100%;
      border-radius: 2vh;
      background: linear-gradient(180deg, #6db3f2 0%, #1a76d2 100%);
      transition: width 0.2s;
      .stripes {
        height: 100%;
        background-image: repeating-linear-gradient(
          -45deg, rgba(255, 255, 255, 0.25) 0 12px, transparent 12px 24px);
        animation: moveStripes 100000s linear infinite;
      }
    }
  }
  .tip {
    margin-top: 1.6vh;
    font-size: 30px;
    color: #4a6a8a;
    letter-spacing: 1px;
    .num { font-weight: bold; }
  }
}
</style>
