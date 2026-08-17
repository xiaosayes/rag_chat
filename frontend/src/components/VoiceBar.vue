<template>
  <div class="voice-bar">
    <img class="keyboard-toggle" :src="'img/keyboard.png'" @click.stop.prevent="$emit('toggle-keyboard')" />
    <div class="mic-capsule" :class="{ recording }" @click.stop.prevent="$emit('mic')">
      <img class="mic-icon" :src="'img/micro.png'" />
      <span>{{ label }}</span>
    </div>
  </div>
</template>

<script lang="ts" setup>
/** 语音胶囊条（web-017）：1.1/1.2 设计稿——键盘切换钮 + 麦克风胶囊。
 *  文案双态轮播（参考 Content.vue：5s 一切）；录音中显示录入态。 */
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useAppStore } from "../stores/app";

const props = withDefaults(defineProps<{
  recording?: boolean;
  interruptible?: boolean;   // 播报中：胶囊变打断按钮
}>(), { recording: false, interruptible: false });
defineEmits(["mic", "toggle-keyboard"]);
const store = useAppStore();

const wakeTip = computed(() => {
  const w = store.config?.wake_words?.[0] ?? "你好湘小图";
  // 展示形态加逗号：你好，湘小图
  return `唤醒我，请说“${w.replace(/^你好/, "你好，")}”`;
});
const labels = computed(() => ["点击与我语音聊天吧~", wakeTip.value]);
const labelIndex = ref(0);
let timer = 0;
onMounted(() => {
  timer = window.setInterval(() => {
    labelIndex.value = (labelIndex.value + 1) % labels.value.length;
  }, 5000);
});
onBeforeUnmount(() => clearInterval(timer));
const label = computed(() => {
  if (props.interruptible) return "说话或点按可打断";   // 1.4 播报中可打断
  return props.recording ? "正在录入语音..." : labels.value[labelIndex.value];
});
</script>

<style lang="scss" scoped>
.voice-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  .keyboard-toggle {
    height: 130px;
    cursor: pointer;
  }
  .mic-capsule {
    width: 690px;
    height: 130px;
    background: url("../assets/input_micro_bg.png") 100% 100% no-repeat;
    background-size: 100% 100%;
    display: flex;
    justify-content: center;
    align-items: flex-start;
    box-sizing: border-box;
    padding-top: 35px;
    cursor: pointer;
    &.recording span { color: #b25d3a; }
    .mic-icon {
      height: 46px;
      margin-right: 16px;
    }
    span {
      font-family: "Source Han Serif CN", serif;
      font-size: 36px;
      font-weight: bold;
      color: #6d5a42;
    }
  }
}
</style>
