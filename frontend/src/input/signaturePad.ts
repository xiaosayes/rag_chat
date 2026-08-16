/** 手写板封装（web-024）：薄包 signature_pad，便于测试替换。 */
import SignaturePad from "signature_pad";

export interface SignaturePadLike {
  clear(): void;
  isEmpty(): boolean;
  toDataURL(type?: string): string;
  addEventListener(type: string, cb: (...args: any[]) => void): void;
}

export function createSignaturePad(canvas: HTMLCanvasElement): SignaturePadLike {
  // 高清屏缩放：保证笔迹清晰（signature_pad 官方做法）
  const ratio = Math.max(window.devicePixelRatio || 1, 1);
  canvas.width = canvas.offsetWidth * ratio;
  canvas.height = canvas.offsetHeight * ratio;
  canvas.getContext("2d")?.scale(ratio, ratio);
  return new SignaturePad(canvas, {
    penColor: "rgb(74, 63, 48)",
    minWidth: 2,
    maxWidth: 4,
  }) as unknown as SignaturePadLike;
}
