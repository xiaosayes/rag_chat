// web-018：PCM 工具 + PcmPlayer（AudioContext 全 mock）
import { describe, expect, it, vi } from "vitest";
import { captureTo16kPcm, downsampleTo16k, float32ToS16le, s16leToFloat32 } from "../src/audio/pcm";
import { PcmPlayer } from "../src/audio/player";

class FakeSource {
  buffer: any;
  startedAt = -1;
  stopped = false;
  onended: (() => void) | null = null;
  constructor(private ctx: FakeAC, public dur: number) {}
  connect() {}
  start(t: number) {
    this.startedAt = t;
    this.ctx.sources.push(this);
  }
  stop() {
    this.stopped = true;
  }
  fireEnded() {
    this.onended?.();
  }
}

class FakeAC {
  currentTime = 10;
  sources: FakeSource[] = [];
  destination = {};
  resume = vi.fn().mockResolvedValue(undefined);
  suspend = vi.fn().mockResolvedValue(undefined);
  createBuffer(_ch: number, len: number, rate: number) {
    return { getChannelData: () => new Float32Array(len), _rate: rate, _len: len };
  }
  createBufferSource() {
    // 时长由 last createBuffer 决定不好拿；测试只关心排播时刻
    return new FakeSource(this, 0);
  }
}

function pcm(frames: number): ArrayBuffer {
  return new Int16Array(frames).buffer;
}

describe("pcm 纯函数", () => {
  it("s16le→float32 幅值映射", () => {
    const s16 = new Int16Array([0, 16384, -32768, 32767]);
    const f = s16leToFloat32(s16.buffer);
    expect(f[0]).toBe(0);
    expect(f[1]).toBeCloseTo(0.5);
    expect(f[2]).toBe(-1);
    expect(f[3]).toBeCloseTo(1, 3);
  });

  it("float32→s16le 限幅", () => {
    const out = float32ToS16le(new Float32Array([0, 0.5, -1, 2]));
    expect(out[1]).toBe(16384);
    expect(out[2]).toBe(-32768);
    expect(out[3]).toBe(32767);   // 限幅
  });

  it("48k→16k 抽取 1/3 长度", () => {
    const src = new Float32Array(4800).fill(0.5);
    expect(downsampleTo16k(src, 48000)).toHaveLength(1600);
    expect(downsampleTo16k(src, 16000)).toBe(src);  // 同率直通
  });

  it("captureTo16kPcm 端到端字节数", () => {
    const buf = captureTo16kPcm(new Float32Array(4800), 48000);
    expect(buf.byteLength).toBe(1600 * 2);
  });
});

describe("PcmPlayer", () => {
  it("排播：首帧预缓冲、后续帧接续", () => {
    const ac = new FakeAC();
    const p = new PcmPlayer(24000, 0.25, ac as any);
    p.start();
    p.push(pcm(2400));             // 0.1s
    p.push(pcm(2400));
    expect(ac.sources[0].startedAt).toBeCloseTo(10.25);   // now+prebuffer
    expect(ac.sources[1].startedAt).toBeCloseTo(10.35);   // 接龙
    expect(p.playing).toBe(true);
    expect(p.bufferedAheadS).toBeCloseTo(0.45, 2);
  });

  it("stop：逐源即停、后续 push 丢弃", () => {
    const ac = new FakeAC();
    const p = new PcmPlayer(24000, 0.25, ac as any);
    p.start();
    p.push(pcm(2400));
    p.push(pcm(2400));
    p.stop();
    expect(ac.sources.every((s) => s.stopped)).toBe(true);
    expect(p.playing).toBe(false);
    p.push(pcm(2400));             // stop 后丢弃
    expect(ac.sources).toHaveLength(2);
  });

  it("onEnded：末源播完回调一次", () => {
    const ac = new FakeAC();
    const p = new PcmPlayer(24000, 0.25, ac as any);
    const ended = vi.fn();
    p.onEnded = ended;
    p.start();
    p.push(pcm(2400));
    p.push(pcm(2400));
    ac.sources[0].fireEnded();
    expect(ended).not.toHaveBeenCalled();
    ac.sources[1].fireEnded();
    expect(ended).toHaveBeenCalledTimes(1);
  });
});
