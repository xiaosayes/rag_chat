<template>
  <div class="waveform">
    <div v-for="(bar, i) in bars" :key="i" class="bar"
         :style="{ height: bar.height + 'px', animationDuration: bar.duration + 's' }"></div>
  </div>
</template>

<script lang="ts" setup>
/** 等待波形（web-021）：移植参考 Bar.vue——15 根随机 bar 循环 bounce（1.3 语音分析稿）。 */
import { onMounted, ref } from "vue";

const props = withDefaults(defineProps<{ count?: number }>(), { count: 15 });
const bars = ref<{ height: number; duration: number }[]>([]);

onMounted(() => {
  bars.value = Array.from({ length: props.count }, () => ({
    height: Math.random() * 24.8 + 19.2,   // px（1080×1920 设计坐标，web-034）
    duration: Math.random() * 0.6 + 0.7,
  }));
});
</script>

<style lang="scss" scoped>
.waveform {
  display: flex;
  align-items: flex-end;
  justify-content: center;
  min-height: 58px;
  padding: 19px 38px;
}
.bar {
  width: 5px;
  background-color: #897967;
  margin-right: 8px;
  border-radius: 4px;
  animation: bounce infinite ease-in-out;
  transform-origin: bottom;
  &:last-child { margin-right: 0; }
}
@keyframes bounce {
  0%, 100% { transform: scaleY(0.4); }
  50% { transform: scaleY(1); }
}
</style>
