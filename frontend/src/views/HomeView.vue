<template>
  <div class="home">
    <img class="logo" :src="'img/logo.png'" alt="logo" />
    <DeerAvatar ref="deer" />
    <SysMenu />
    <div class="panel">
      <img class="panel-bg" :src="'img/v2/content_bg.png'" />
      <div class="panel-inner">
        <VoiceBar :recording="recording" @mic="onMic" @toggle-keyboard="onKeyboard" />
        <div class="divider"><img :src="'img/v2/arrow_left.png'" /><i></i><img :src="'img/v2/arrow_right.png'" /></div>
        <PresetPanel v-if="mode === 'home'" @select="onPreset" />
        <div v-else class="chat-placeholder">
          <p>聊天面板（M5 接入）</p>
          <p class="pending-q" v-if="pendingQuestion">待提问：{{ pendingQuestion }}</p>
          <button class="back" @click="mode = 'home'">返回</button>
        </div>
      </div>
    </div>
    <SplashScreen :show="!store.modelReady" />
  </div>
</template>

<script lang="ts" setup>
/** 首页（web-017）：1.1/1.2 设计稿——logo + 小鹿 + 羊皮纸面板（语音胶囊 + 预设）。
 *  M4 范围：聊天态仅占位（M5 接入 WS 全链）。 */
import { onMounted, ref } from "vue";
import DeerAvatar from "../components/DeerAvatar.vue";
import PresetPanel from "../components/PresetPanel.vue";
import SplashScreen from "../components/SplashScreen.vue";
import SysMenu from "../components/SysMenu.vue";
import VoiceBar from "../components/VoiceBar.vue";
import { useAppStore } from "../stores/app";

const store = useAppStore();
const deer = ref();
const mode = ref<"home" | "chat">("home");
const recording = ref(false);
const pendingQuestion = ref("");

function onMic() {
  recording.value = !recording.value;   // M5 接 WS 语音全链
}
function onKeyboard() {
  /* M6：键盘/手写输入 */
}
function onPreset(q: string) {
  pendingQuestion.value = q;
  mode.value = "chat";
}

onMounted(() => {
  document.addEventListener("contextmenu", (e) => e.preventDefault());
  document.addEventListener("touchmove", (e) => {
    if (e.touches.length > 1) e.preventDefault();
  }, { passive: false });
});
</script>

<style lang="scss" scoped>
.home {
  width: 100%;
  height: 100%;
  position: relative;
  background: url("../../public/img/v2/bg.png") 100% 100% no-repeat;
  background-size: 100% 100%;
  overflow: hidden;
  .logo {
    position: absolute;
    left: 2.4vh;
    top: 2.4vh;
    height: 6vh;
    z-index: 2;
  }
  .panel {
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    height: 51.3vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    .panel-bg {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      z-index: 0;
    }
    .panel-inner {
      position: relative;
      z-index: 1;
      width: 100%;
      padding-top: 3.2vh;
      display: flex;
      flex-direction: column;
      align-items: center;
    }
    .divider {
      display: flex;
      align-items: center;
      margin: 1.4vh 0 0.6vh;
      width: 60%;
      i {
        flex: 1;
        height: 1px;
        background: rgba(109, 90, 66, 0.35);
        margin: 0 1vh;
      }
      img { height: 1.6vh; opacity: 0.7; }
    }
    .chat-placeholder {
      padding: 6vh 4vh;
      font-size: 32px;
      color: #6d5a42;
      text-align: center;
      .back {
        margin-top: 3vh;
        font-size: 30px;
        padding: 1vh 4vh;
        border-radius: 2vh;
        border: 1px solid #c9b890;
      }
    }
  }
}
</style>
