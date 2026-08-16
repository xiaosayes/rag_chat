/** 麦克风采集（web-022）：getUserMedia(AEC 三件套) → AudioWorklet → 16k s16le。
 *  防自触发双保险之一（浏览器 AEC 以本页输出为参考，audit-ASR 既有语义）；
 *  另一保险在服务端：播报期间只跑 VAD 不送 ASR（FSM 内核）。 */
import { captureTo16kPcm } from "./pcm";

export interface CaptureHandle {
  stop: () => void;
}

export async function startCapture(
  onPcm: (buf: ArrayBuffer) => void,
  workletUrl = "worklet/capture-worklet.js",
): Promise<CaptureHandle> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      channelCount: 1,
    },
  });
  const AC: typeof AudioContext =
    (window as any).AudioContext || (window as any).webkitAudioContext;
  const ctx = new AC();
  await ctx.audioWorklet.addModule(workletUrl);
  const src = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "pcm-capture");
  const srcRate = ctx.sampleRate;
  node.port.onmessage = (e: MessageEvent<Float32Array>) => {
    onPcm(captureTo16kPcm(e.data, srcRate));
  };
  src.connect(node);
  // 不连 destination（不监听自身），避免回授
  return {
    stop: () => {
      node.port.onmessage = null;
      node.disconnect();
      src.disconnect();
      stream.getTracks().forEach((t) => t.stop());
      void ctx.close();
    },
  };
}
