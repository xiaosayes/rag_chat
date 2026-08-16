/** 空闲计时（web-021）：参考实现语义——homeAfterS 无操作回首页、refreshAfterS 自刷新。
 *  用户交互（pointerdown）与服务端活动都复位计时。 */
import { onBeforeUnmount, onMounted } from "vue";

export function useIdleTimer(opts: {
  homeAfterS: () => number;
  refreshAfterS: () => number;
  onHome: () => void;
  onRefresh?: () => void;
}) {
  let homeTimer = 0;
  let refreshTimer = 0;

  function reset() {
    clearTimeout(homeTimer);
    clearTimeout(refreshTimer);
    homeTimer = window.setTimeout(() => opts.onHome(), opts.homeAfterS() * 1000);
    refreshTimer = window.setTimeout(
      () => (opts.onRefresh ?? (() => location.reload()))(),
      opts.refreshAfterS() * 1000,
    );
  }

  onMounted(() => {
    window.addEventListener("pointerdown", reset);
    reset();
  });
  onBeforeUnmount(() => {
    window.removeEventListener("pointerdown", reset);
    clearTimeout(homeTimer);
    clearTimeout(refreshTimer);
  });

  return { reset };
}
