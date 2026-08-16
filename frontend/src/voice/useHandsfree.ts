/** 免提语音闭环（web-025）：模型就绪后自动建连 + 自动开麦常开推流。
 *  一体机 Chrome 以 --use-fake-ui-for-media-stream 免授权弹窗；
 *  开麦失败降级手动（胶囊可点按重试）。 */
import { onBeforeUnmount, ref, watch } from "vue";
import { startCapture, type CaptureHandle } from "../audio/capture";
import type { useVoiceSession } from "./useVoiceSession";

export function useHandsfree(opts: {
  session: ReturnType<typeof useVoiceSession>;
  modelReady: () => boolean;
  startCaptureFn?: typeof startCapture;   // 测试注入
  onError?: (msg: string) => void;
}) {
  const capturing = ref(false);
  const micFailed = ref(false);
  let capture: CaptureHandle | null = null;
  let started = false;

  async function start() {
    if (started) return;
    started = true;
    const captureFn = opts.startCaptureFn ?? startCapture;
    try {
      opts.session.connect();
      capture = await captureFn((pcm) => opts.session.client.sendAudio(pcm));
      capturing.value = true;
      micFailed.value = false;
      opts.session.setRecording(true);
    } catch {
      started = false;          // 允许重试（点按胶囊）
      micFailed.value = true;
      opts.onError?.("麦克风不可用，点按语音胶囊重试");
    }
  }

  function stop() {
    capture?.stop();
    capture = null;
    capturing.value = false;
    opts.session.setRecording(false);
    started = false;
  }

  // 模型就绪即自动开麦（免提）；started 标志防重入
  watch(opts.modelReady, (ready) => {
    if (ready) void start();
  }, { immediate: true });

  onBeforeUnmount(stop);

  return { capturing, micFailed, start, stop };
}
