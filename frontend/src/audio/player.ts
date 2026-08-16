/** PCM 流式播放器（web-018）：s16le 24k → WebAudio BufferSource 链式排播。
 *  参考 BufferSource.js 模式重构：欠载预缓冲 0.25s、stop 逐源即停（打断干脆）、
 *  末源播完回调（驱动 STANDBY 回落）。 */
import { s16leToFloat32 } from "./pcm";

export class PcmPlayer {
  private ctx: AudioContext;
  private sources = new Set<AudioBufferSourceNode>();
  private nextStart = 0;
  private stopped = true;
  private scheduleStartAt = 0;   // 本轮排播起点（音频时钟）
  private scheduledTotalS = 0;   // 本轮已排播总时长
  onEnded: (() => void) | null = null;

  constructor(private sampleRate = 24000, private prebufferS = 0.25,
              ctx?: AudioContext) {
    const AC: typeof AudioContext =
      (window as any).AudioContext || (window as any).webkitAudioContext;
    this.ctx = ctx ?? new AC();
  }

  get playing(): boolean {
    return this.sources.size > 0;
  }

  /** 估计在途未播时长（秒，诊断/测试用） */
  get bufferedAheadS(): number {
    return Math.max(0, this.nextStart - this.ctx.currentTime);
  }

  start() {
    this.stopped = false;
    this.scheduleStartAt = 0;
    this.scheduledTotalS = 0;
    void this.ctx.resume();
  }

  /** 当前播放头位置（秒，相对本轮 start）——MusicBar 进度（web-026） */
  get positionS(): number {
    if (!this.scheduleStartAt) return 0;
    return Math.min(
      Math.max(0, this.ctx.currentTime - this.scheduleStartAt),
      this.scheduledTotalS,
    );
  }

  /** 喂入一帧 PCM（s16le）。 */
  push(buf: ArrayBuffer) {
    if (this.stopped) return;
    const samples = s16leToFloat32(buf);
    if (!samples.length) return;
    const audio = this.ctx.createBuffer(1, samples.length, this.sampleRate);
    (audio.getChannelData(0) as Float32Array).set(samples);
    const src = this.ctx.createBufferSource();
    src.buffer = audio;
    src.connect(this.ctx.destination);
    const now = this.ctx.currentTime;
    const startAt = Math.max(this.nextStart, now + this.prebufferS);
    if (!this.scheduleStartAt) this.scheduleStartAt = startAt;
    src.start(startAt);
    this.nextStart = startAt + samples.length / this.sampleRate;
    this.scheduledTotalS += samples.length / this.sampleRate;
    this.sources.add(src);
    src.onended = () => {
      this.sources.delete(src);
      if (this.sources.size === 0 && !this.stopped && this.onEnded) {
        this.onEnded();
      }
    };
  }

  /** 打断/停止：逐源即停并清空（已排播的缓冲立即静音）。 */
  stop() {
    this.stopped = true;
    for (const src of this.sources) {
      try {
        src.onended = null;
        src.stop();
      } catch {
        /* 已播完的源 stop 抛 InvalidStateError，忽略 */
      }
    }
    this.sources.clear();
    this.nextStart = 0;
    this.scheduleStartAt = 0;
    this.scheduledTotalS = 0;
  }
}
