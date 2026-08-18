<template>
  <div class="home">
    <img class="logo" :src="'img/logo.png'" alt="logo" />
    <DeerAvatar ref="deer" />
    <SysMenu />
    <div class="panel">
      <img class="panel-bg" :src="'img/v1/content_bg.png'" />
      <div class="panel-inner">
        <VoiceBar v-if="inputMode === 'voice'" :recording="session.recording.value"
                  :interruptible="session.mode.value === 'broadcast'"
                  @mic="onMic" @toggle-keyboard="inputMode = 'keyboard'" />
        <KeyboardInput v-else @toggle-voice="inputMode = 'voice'" @send="onTypedSend" />
        <div class="divider"><i></i><span class="leaf"></span><i></i></div>
        <PresetPanel v-if="mode === 'home' && inputMode === 'voice'" @select="onPreset" />
        <ChatPanel v-if="mode === 'chat'" :session="session" @back="onBack" />
      </div>
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
import { useAutoChat } from "../voice/useAutoChat";
import { useHandsfree } from "../voice/useHandsfree";
import { useIdleTimer } from "../voice/useIdleTimer";
import { useVoiceSession } from "../voice/useVoiceSession";
import { useAppStore } from "../stores/app";

const store = useAppStore();
const deer = ref<InstanceType<typeof DeerAvatar>>();
const mode = ref<"home" | "chat">("home");
const inputMode = ref<"voice" | "keyboard">("voice");

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
  onAction: (name) => deer.value?.playAccent(name),   // web-039 主题点缀动作
  onActivity: () => idle.reset(),
  onClose: () => { session.statusText.value = "连接已断开，重连中…"; },
});
// web-042：语音提问自动跳聊天态（await_broadcast/broadcast 沿触发，手动返回不回弹）
useAutoChat(session.mode, mode);

// 免提闭环（web-025）：模型就绪自动开麦常开推流；失败降级手动
const handsfree = useHandsfree({
  session,
  modelReady: () => store.modelReady,
  onError: (msg) => { session.statusText.value = msg; },
});

function onMic() {
  // 播报中：胶囊=打断钮（1.4 设计稿可打断语义）
  if (session.mode.value === "broadcast") {
    session.barge();
    return;
  }
  if (handsfree.capturing.value) {
    handsfree.stop();
    return;
  }
  void handsfree.start();
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
  background: url("../../public/img/v1/bg.png") 100% 100% no-repeat;
  background-size: 100% 100%;
  overflow: hidden;
  .logo {
    position: absolute;
    left: 46px;
    top: 46px;
    height: 115px;
    z-index: 2;
  }
  .panel {
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    height: 985px;
    overflow: hidden;        /* web-038：固定窗口，内容不外溢 */
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
      box-sizing: border-box;  /* web-038：padding 计入 985px 内，子项 flex 精确分配 */
      padding-top: 61px;
      display: flex;
      flex-direction: column;
      align-items: center;
    }
    .divider {
      flex: none;   /* web-038 */
      display: flex;
      align-items: center;
      margin: 27px 0 12px;
      width: 60%;
      i {
        flex: 1;
        height: 1px;
        background: rgba(109, 90, 66, 0.35);
      }
      .leaf {   // 设计稿分隔线叶饰（纯 CSS，去掉碎图引用 web-031）
        width: 21px;
        height: 21px;
        margin: 0 23px;
        background: #b7a88f;
        border-radius: 0 60% 0 60%;
        transform: rotate(45deg);
      }
    }
  }
}
</style>
