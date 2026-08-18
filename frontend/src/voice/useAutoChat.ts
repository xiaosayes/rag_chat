/** 语音提问自动跳转聊天态（web-042）：语音提交（await_broadcast）/播报开始（broadcast）
 *  时把首页模式切到 chat，展示问答气泡；手动返回 home 不受回弹（仅在会话模式变化沿触发）。 */
import { watch, type Ref } from "vue";
import type { UiMode } from "./useVoiceSession";

export function useAutoChat(sessionMode: Ref<UiMode>, viewMode: Ref<string>) {
  watch(sessionMode, (m) => {
    if ((m === "await_broadcast" || m === "broadcast") && viewMode.value !== "chat") {
      viewMode.value = "chat";
    }
  });
}
