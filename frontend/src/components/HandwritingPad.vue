<template>
  <div class="handwriting-pad">
    <div class="canvas-wrap">
      <canvas ref="canvasEl" class="canvas"></canvas>
      <div v-if="!hasStroke" class="placeholder">
        <img :src="'img/v1/writing.png'" />
        <span>手写区域</span>
      </div>
      <div v-if="recognizing" class="recognizing">识别中…</div>
      <div v-if="error" class="error-tip">{{ error }}</div>
    </div>
    <div class="actions">
      <span class="btn" @click.stop.prevent="$emit('close')">拼音</span>
      <span class="btn" @click.stop.prevent="emitCommit(' ')">空格</span>
      <span class="btn" @click.stop.prevent="backspace">退格</span>
      <span class="btn" @click.stop.prevent="finish">完成</span>
    </div>
  </div>
</template>

<script lang="ts" setup>
/** 手写输入板（web-024）：1.5.2 设计稿——画布 + 拼音/空格/退格/完成。
 *  停笔 2s 自动 OCR（/api/ocr 百炼 qwen-vl-ocr，密钥仅服务端），识别字经 commit 追加。 */
import { onBeforeUnmount, onMounted, ref } from "vue";
import { api } from "../api/client";
import { createSignaturePad, type SignaturePadLike } from "../input/signaturePad";

const emit = defineEmits(["close", "commit", "backspace"]);
const canvasEl = ref<HTMLCanvasElement>();
const hasStroke = ref(false);
const recognizing = ref(false);
const error = ref("");
let pad: SignaturePadLike | null = null;
let idleTimer = 0;

const OCR_IDLE_MS = 2000;   // 停笔 2s 触发（参考实现既定值）

onMounted(() => {
  pad = createSignaturePad(canvasEl.value!);
  pad.addEventListener("beginStroke", () => {
    hasStroke.value = true;
    error.value = "";
    clearTimeout(idleTimer);
  });
  pad.addEventListener("endStroke", () => {
    clearTimeout(idleTimer);
    idleTimer = window.setTimeout(recognize, OCR_IDLE_MS);
  });
});

onBeforeUnmount(() => clearTimeout(idleTimer));

async function recognize() {
  if (!pad || pad.isEmpty() || recognizing.value) return;
  recognizing.value = true;
  try {
    const { text } = await api.ocr(pad.toDataURL("image/png"));
    if (text) emitCommit(text);
    pad.clear();
    hasStroke.value = false;
  } catch {
    error.value = "识别失败，请重写";
  } finally {
    recognizing.value = false;
  }
}

function emitCommit(text: string) {
  emit("commit", text);
}

function backspace() {
  emit("backspace");        // 输入框回退一字（父组件持有文本）
}

function finish() {
  pad?.clear();
  hasStroke.value = false;
  emit("close");
}

defineExpose({ recognize });
</script>

<style lang="scss" scoped>
.handwriting-pad {
  margin-top: 1.6vh;
  background: rgba(255, 250, 235, 0.95);
  border-radius: 1.6vh;
  padding: 1.6vh;
  width: 62vh;
  .canvas-wrap {
    position: relative;
    height: 24vh;
    background: #fff;
    border-radius: 1vh;
    .canvas {
      width: 100%;
      height: 100%;
      border-radius: 1vh;
      touch-action: none;
    }
    .placeholder {
      position: absolute;
      inset: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      pointer-events: none;
      color: #b7a88f;
      img { height: 4vh; margin-bottom: 0.8vh; opacity: 0.7; }
      span { font-size: 28px; }
    }
    .recognizing, .error-tip {
      position: absolute;
      right: 1vh;
      top: 1vh;
      font-size: 24px;
      color: #897967;
    }
    .error-tip { color: #b25d3a; }
  }
  .actions {
    display: flex;
    justify-content: space-between;
    margin-top: 1.4vh;
    .btn {
      font-family: "Source Han Serif CN", serif;
      font-size: 30px;
      color: #6d5a42;
      background: rgba(230, 218, 196, 0.8);
      border-radius: 1vh;
      padding: 1vh 4vh;
      cursor: pointer;
      &:active { opacity: 0.6; }
    }
  }
}
</style>
