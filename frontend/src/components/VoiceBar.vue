<template>
  <div class="voice-bar">
    <img class="keyboard-toggle" :src="'img/keyboard.png'" @click.stop.prevent="$emit('toggle-keyboard')" />
    <div class="mic-capsule" :class="{ speaking }" @click.stop.prevent="$emit('mic')">
      <img class="mic-icon" :src="'img/micro.png'" />
      <span>{{ label }}</span>
    </div>
  </div>
</template>

<script lang="ts" setup>
/** 语音胶囊条（web-017）：1.1/1.2 设计稿——键盘切换钮 + 麦克风胶囊。
 *  web-035 文案状态机（用户意图）：待机=唤醒提示；唤醒后未发声=我在听；
 *  检测到声音=正在录入语音；播报中=可打断。 */
import { computed } from "vue";
import { useAppStore } from "../stores/app";

const props = withDefaults(defineProps<{
  listening?: boolean;      // 已唤醒、聆听中（尚未检测到声音）
  speaking?: boolean;       // 检测到说话声（asr_partial）
  interruptible?: boolean;  // 播报中：胶囊变打断按钮
}>(), { listening: false, speaking: false, interruptible: false });
defineEmits(["mic", "toggle-keyboard"]);
const store = useAppStore();

const wakeTip = computed(() => {
  const w = store.config?.wake_words?.[0] ?? "你好湘小图";
  // 展示形态加逗号：你好，湘小图
  return `请说“${w.replace(/^你好/, "你好，")}”唤醒`;
});
const label = computed(() => {
  if (props.interruptible) return "说话或点按可打断";   // 1.4 播报中可打断
  if (props.speaking) return "正在录入语音...";         // 检测到声音才显示（web-035）
  if (props.listening) return "我在听，请说出您的问题…"; // 唤醒后未发声
  return wakeTip.value;                                 // 初始/待机
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
    &.speaking span { color: #b25d3a; }
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
