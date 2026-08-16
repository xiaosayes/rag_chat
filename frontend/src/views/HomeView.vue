<template>
  <div class="home">
    <img class="logo" :src="'img/logo.png'" alt="logo" />
    <DeerAvatar ref="deer" />
    <SysMenu />
    <div class="panel">
      <img class="panel-bg" :src="'img/v2/content_bg.png'" />
      <div class="panel-inner">
        <VoiceBar v-if="inputMode === 'voice'" :recording="session.recording.value"
                  @mic="onMic" @toggle-keyboard="inputMode = 'keyboard'" />
        <KeyboardInput v-else @toggle-voice="inputMode = 'voice'" @send="onTypedSend" />
        <div class="divider">
          <img :src="'img/v2/arrow_left.png'" /><i></i><img :src="'img/v2/arrow_right.png'" />
        </div>
        <PresetPanel v-if="mode === 'home' && inputMode === 'voice'" @select="onPreset" />
        <ChatPanel v-if="mode === 'chat'" :session="session" @back="onBack" />
      </div>
    </div>
    <div class="home-status" v-if="mode === 'home' && session.statusText.value">
      {{ session.statusText.value }}
    </div>
    <SplashScreen :show="!store.modelReady" />
  </div>
</template>

<script lang="ts" setup>
/** 首页（web-021）：单页双态（home/chat）；WS 语音会话全链接入。 */
import { onMounted, ref } from "vue";
import ChatPanel from "../components/ChatPanel.vue";
import DeerAvatar from "../components/DeerAvatar.vue";
import PresetPanel from "../components/PresetPanel.vue";
import SplashScreen from "../components/SplashScreen.vue";
import SysMenu from "../components/SysMenu.vue";
import KeyboardInput from "../components/KeyboardInput.vue";
import VoiceBar from "../components/VoiceBar.vue";
import { startCapture, type CaptureHandle } from "../audio/capture";
import { useIdleTimer } from "../voice/useIdleTimer";
import { useVoiceSession } from "../voice/useVoiceSession";
import { useAppStore } from "../stores/app";

const store = useAppStore();
const deer = ref<InstanceType<typeof DeerAvatar>>();
const mode = ref<"home" | "chat">("home");
const inputMode = ref<"voice" | "keyboard">("voice");
const capturing = ref(false);
let capture: CaptureHandle | null = null;

const idle = useIdleTimer({
  homeAfterS: () => store.homeAfterS,
  refreshAfterS: () => store.refreshAfterS,
  onHome: () => {
    if (mode.value === "chat") {
      session.resetChat();
      mode.value = "home";
    }
  },
});

const session = useVoiceSession({
  onTalkChange: (talking) => {
    if (talking) deer.value?.playTalk();
    else deer.value?.playStandby();
  },
  onActivity: () => idle.reset(),
  onClose: () => { session.statusText.value = "连接已断开，重连中…"; },
});

function onMic() {
  if (!session.voiceReady.value) session.connect();
  if (capturing.value) {
    capture?.stop();
    capture = null;
    capturing.value = false;
    session.setRecording(false);
    return;
  }
  // 常开推流（FSM 计时靠帧驱动——M3 内核契约）
  startCapture((pcm) => session.client.sendAudio(pcm))
    .then((h) => {
      capture = h;
      capturing.value = true;
      session.setRecording(true);
      mode.value = "chat";
    })
    .catch(() => {
      session.statusText.value = "麦克风不可用，请检查设备";
    });
}

function onPreset(q: string) {
  if (!session.voiceReady.value) session.connect();
  mode.value = "chat";
  session.askText(q);
}

function onTypedSend(q: string) {
  if (!session.voiceReady.value) session.connect();
  mode.value = "chat";
  session.askText(q);
}

function onBack() {
  mode.value = "home";
}

onMounted(() => {
  document.addEventListener("contextmenu", (e) => e.preventDefault());
  document.addEventListener("touchmove", (e) => {
    if (e.touches.length > 1) e.preventDefault();
  }, { passive: false });
  session.connect();   // 提前建连（语音助手待机收音由 mic 开启驱动）
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
      height: 100%;
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
  }
  .home-status {
    position: absolute;
    bottom: 52.5vh;
    left: 0;
    right: 0;
    text-align: center;
    font-size: 26px;
    color: #fff;
    text-shadow: 0 1px 4px rgba(0, 0, 0, 0.4);
    z-index: 3;
  }
}
</style>
