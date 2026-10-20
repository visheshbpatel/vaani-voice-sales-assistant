class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.offset = 0;
    this.pending = new Float32Array(0);
  }

  _concat(a, b) {
    const out = new Float32Array(a.length + b.length);
    out.set(a);
    out.set(b, a.length);
    return out;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) {
      return true;
    }
    this.pending = this._concat(this.pending, channel);
    const outCount = Math.floor((this.pending.length - this.offset) / this.ratio);
    if (outCount < 320) {
      return true;
    }
    const pcm = new Int16Array(outCount);
    let pos = this.offset;
    for (let i = 0; i < outCount; i++) {
      const index = Math.floor(pos);
      const frac = pos - index;
      const s0 = this.pending[index] || 0;
      const s1 = this.pending[index + 1] !== undefined ? this.pending[index + 1] : s0;
      let sample = s0 + (s1 - s0) * frac;
      sample = Math.max(-1, Math.min(1, sample));
      pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
      pos += this.ratio;
    }
    const consumed = Math.floor(pos);
    this.pending = this.pending.subarray(consumed);
    this.offset = pos - consumed;
    this.port.postMessage(pcm.buffer, [pcm.buffer]);
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
