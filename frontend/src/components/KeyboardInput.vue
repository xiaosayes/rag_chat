<template>
  <div class="keyboard-input">
    <div class="input-row">
      <img class="mic-toggle" :src="'img/v1/key_keyboard.png'" @click.stop.prevent="$emit('toggle-voice')" />
      <div class="field" @click.stop.prevent="keyboardShow = true">
        <span class="value">{{ value }}</span>
        <img v-if="value" class="clear" :src="'img/v1/input_close.png'"
             @click.stop.prevent="clear" />
      </div>
      <img class="send" :src="'img/send.png'" @click.stop.prevent="submit" />
    </div>
    <transition name="zoom">
      <div v-if="keyboardShow" class="keyboard-panel">
        <div ref="kbEl" class="simple-keyboard-host"></div>
      </div>
    </transition>
    <HandwritingPad v-if="handwriting" @close="handwriting = false; keyboardShow = true"
                    @commit="onHandwrite" @backspace="value = value.slice(0, -1)" />
  </div>
</template>

<script lang="ts" setup>
/** 键盘输入区（web-023）：1.5.x 设计稿——输入行（回切语音/输入框/清空/发送）+ 全拼键盘 + 手写切换。 */
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import HandwritingPad from "./HandwritingPad.vue";
import { createPinyinKeyboard, type PinyinKeyboard } from "../input/pinyinKeyboard";

const emit = defineEmits(["send", "toggle-voice"]);
const value = ref("");
const keyboardShow = ref(true);
const handwriting = ref(false);
const kbEl = ref<HTMLElement>();
let kb: PinyinKeyboard | null = null;

function mountKeyboard() {
  if (kb || !kbEl.value) return;
  kb = createPinyinKeyboard(kbEl.value, {
    onInput: (text) => { value.value = text; },
    onWrite: () => { keyboardShow.value = false; handwriting.value = true; },
    onFinished: () => { keyboardShow.value = false; },
  });
}

watch(keyboardShow, async (show) => {
  if (show) {
    await nextTick();
    mountKeyboard();
  } else {
    kb?.destroy();                        // web-070 评审修复：收起即销毁并置空——
    kb = null;                            // v-if 会销毁宿主 DOM，重开必须重建（否则空白面板）
  }
});

onMounted(async () => {
  await nextTick();
  mountKeyboard();           // 初始即展开（watch 不 immediate，首挂手动挂载）
});

onBeforeUnmount(() => kb?.destroy());

function clear() {
  value.value = "";
  kb?.destroy();
  kb = null;
  mountKeyboard();          // 重建以清空 simple-keyboard 内部 input
}

function submit() {
  const q = value.value.trim();
  if (!q) return;
  emit("send", q);
  clear();
  keyboardShow.value = false;
}

function onHandwrite(text: string) {
  value.value += text;      // OCR 识别字追加
}

defineExpose({ value, keyboardShow, handwriting });
</script>

<style lang="scss" scoped>
.keyboard-input {
  flex: none;   /* web-038 */
  width: 100%;
  .input-row {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 31px;
    .mic-toggle { height: 130px; cursor: pointer; }
    .field {
      width: 499px;
      height: 130px;
      background: url("../assets/input_bg.png") 100% 100% no-repeat;
      background-size: 100% 100%;
      display: flex;
      align-items: center;
      padding: 0 38px;
      box-sizing: border-box;
      cursor: pointer;
      .value {
        flex: 1;
        font-family: "Source Han Serif CN", serif;
        font-size: 40px;                          /* web-071：加大加粗提亮 */
        font-weight: 600;
        color: #3a3126;
        overflow: hidden;
        white-space: nowrap;
      }
      .clear { height: 58px; }
    }
    .send { height: 130px; cursor: pointer; }
  }
  .keyboard-panel {
    margin-top: 31px;
    background: rgba(255, 250, 235, 0.95);
    border-radius: 31px;
    padding: 31px;
    width: 100%;                        /* web-071：边界不超出对话框 */
    box-sizing: border-box;
    /* 评审修复 C1：不设 overflow:hidden——横向收玫由 width/box-sizing/min-width 保证，
       且 overflow:hidden 会把「更多▼」浮层（absolute 于面板上方）裁掉 */
  }
  :deep(.simple-keyboard-host) { position: relative; }   /* 候选浮层包含块（评审修复 M2）*/

  /* web-070/071：键盘放大 + 连字拼音候选条（单行+更多浮层） */
  :deep(.pinyin-candidates) {
    display: flex;
    flex-wrap: nowrap;                  /* web-071：只弹一行，不多行挤压键盘 */
    align-items: center;
    gap: 18px;
    height: 104px;
    max-width: 100%;
    overflow: hidden;
    margin-bottom: 22px;
    padding: 6px 4px;
    box-sizing: border-box;
    .pinyin-cand {
      flex: none;
      font-family: "Source Han Serif CN", serif;
      font-size: 48px;
      line-height: 1.2;
      padding: 16px 34px;
      border: 2px solid rgba(74, 63, 48, 0.28);
      border-radius: 20px;
      background: #fff7e6;
      color: #4a3f30;
      cursor: pointer;
    }
    .pinyin-cand-word {                /* 词组候选高亮（连字拼音主推荐位） */
      background: #ffe9b8;
      border-color: rgba(74, 63, 48, 0.45);
      font-weight: 600;
    }
    .pinyin-cand-more {                /* 更多/收起切换 */
      background: #f3e3bd;
      font-size: 40px;
    }
  }
  :deep(.pinyin-cand-overlay) {        /* web-071：更多候选浮层——绝对定位不挤键盘 */
    position: absolute;
    left: 0;
    right: 0;
    bottom: calc(100% + 12px);
    z-index: 30;
    display: flex;
    flex-wrap: wrap;
    gap: 18px;
    max-height: 420px;
    overflow-y: auto;
    padding: 24px;
    box-sizing: border-box;
    background: rgba(255, 250, 235, 0.98);
    border: 2px solid rgba(74, 63, 48, 0.35);
    border-radius: 24px;
    box-shadow: 0 8px 24px rgba(46, 38, 28, 0.35);
    .pinyin-cand {
      flex: none;
      font-family: "Source Han Serif CN", serif;
      font-size: 48px;
      line-height: 1.2;
      padding: 16px 34px;
      border: 2px solid rgba(74, 63, 48, 0.28);
      border-radius: 20px;
      background: #fff7e6;
      color: #4a3f30;
      cursor: pointer;
    }
    .pinyin-cand-word { background: #ffe9b8; font-weight: 600; }
  }
  :deep(.kiosk-keyboard) {
    max-width: 100%;
    .hg-row { gap: 14px; margin-bottom: 14px; }
    .hg-button {
      height: 108px;
      min-width: 0;                     /* web-071：可收缩防横向溢出 */
      border-radius: 18px;
      background: #fffdf6;              /* web-071：更亮更清晰的键面 */
      border: 2px solid rgba(74, 63, 48, 0.35);
      box-shadow: 0 3px 6px rgba(74, 63, 48, 0.18);
      span {
        font-size: 42px;
        font-weight: 600;
        font-family: "Source Han Serif CN", serif;
        color: #3a3126;
      }
      &:active { background: #ffe9b8; }
    }
    /* 功能键区分色：手写/空格/退格/Aa 深羊皮纸；完成 主色 */
    .hg-button[data-skbtn="{write}"],
    .hg-button[data-skbtn="{space}"],
    .hg-button[data-skbtn="{bksp}"],
    .hg-button[data-skbtn="{shift}"] { background: #f3e3bd; }
    .hg-button[data-skbtn="{finished}"] {
      background: #4a7c59;
      border-color: #3a6347;
      span { color: #ffffff; }
    }
  }
}
.zoom-enter-active { animation: zoomIn 0.25s; }
@keyframes zoomIn {
  from { opacity: 0; transform: scale(0.92); }
  to { opacity: 1; transform: scale(1); }
}
</style>
