/** PCM 采集 AudioWorklet（web-022）：攒 ~0.1s 帧回主线程（降采样在主线程做，便于单测）。 */
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = [];
    this._len = 0;
    this._frameSamples = Math.floor(sampleRate * 0.1); // 0.1s/帧
  }

  process(inputs) {
    const input = inputs[0];
    if (input && input[0] && input[0].length) {
      const ch = input[0];
      this._buf.push(new Float32Array(ch));
      this._len += ch.length;
      if (this._len >= this._frameSamples) {
        const merged = new Float32Array(this._len);
        let off = 0;
        for (const seg of this._buf) {
          merged.set(seg, off);
          off += seg.length;
        }
        this._buf = [];
        this._len = 0;
        this.port.postMessage(merged, [merged.buffer]);
      }
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
