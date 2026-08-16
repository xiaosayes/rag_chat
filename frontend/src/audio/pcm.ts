/** PCM 工具纯函数（web-018）：全离线可测。 */

/** s16le → Float32[-1,1] */
export function s16leToFloat32(buf: ArrayBuffer): Float32Array {
  const src = new Int16Array(buf);
  const out = new Float32Array(src.length);
  for (let i = 0; i < src.length; i++) out[i] = src[i] / 32768;
  return out;
}

/** Float32 → s16le bytes（采集上行用；限幅防爆音） */
export function float32ToS16le(src: Float32Array): Int16Array {
  const out = new Int16Array(src.length);
  for (let i = 0; i < src.length; i++) {
    const v = Math.max(-1, Math.min(1, src[i]));
    out[i] = Math.round(v < 0 ? v * 32768 : v * 32767);
  }
  return out;
}

/** 任意采样率 → 16kHz 抽取（均值窗口抗混叠的简化版：线性插值取点） */
export function downsampleTo16k(src: Float32Array, srcRate: number): Float32Array {
  if (srcRate === 16000) return src;
  const ratio = srcRate / 16000;
  const outLen = Math.floor(src.length / ratio);
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const pos = i * ratio;
    const idx = Math.floor(pos);
    const frac = pos - idx;
    const a = src[idx] ?? 0;
    const b = src[idx + 1] ?? a;
    out[i] = a + (b - a) * frac;
  }
  return out;
}

/** 采集链路组合：Float32 任意采样率 → 16k s16le bytes */
export function captureTo16kPcm(src: Float32Array, srcRate: number): ArrayBuffer {
  const down = downsampleTo16k(src, srcRate);
  const s16 = float32ToS16le(down);
  return s16.buffer;
}
