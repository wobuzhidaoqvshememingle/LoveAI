/**
 * voice-input.js  (v2 真麦克风版)
 * 点语音球 → getUserMedia 录真麦克风 → Web Audio 取 PCM → 重采样到 16kHz 单声道
 * → Int16 二进制帧通过 WebSocket 发给后端 → 后端 faster-whisper 本地转写。
 *
 * 协议：
 *   开始: sendControl({event:'start_recording'})
 *   过程: sendBinary(Int16 字节)  // 16k 单声道 PCM
 *   结束: sendControl({event:'stop_recording'})
 *
 * 降级：拿不到麦克风时退回 prompt() 键盘输入（不再依赖浏览器 SpeechRecognition）。
 */
export class VoiceInput {
  /**
   * @param {object} opts
   * @param {(text: string, source: 'voice'|'keyboard') => void} opts.onResult
   * @param {(state: 'idle'|'recording') => void} [opts.onStateChange]
   * @param {() => void} [opts.onUnsupported]
   * @param {(obj:object)=>void} opts.sendControl   // 发 JSON 控制帧
   * @param {(buf:ArrayBuffer)=>void} opts.sendBinary // 发二进制 PCM 帧
   */
  constructor({
    onResult = () => {},
    onStateChange = () => {},
    onUnsupported = () => {},
    sendControl = () => {},
    sendBinary = () => {},
    lang = 'zh-CN',
  } = {}) {
    this._onResult = onResult;
    this._onStateChange = onStateChange;
    this._onUnsupported = onUnsupported;
    this._sendControl = sendControl;
    this._sendBinary = sendBinary;
    this._lang = lang;

    this._active = false;
    this._ctx = null;       // AudioContext
    this._stream = null;    // MediaStream
    this._proc = null;      // ScriptProcessorNode
    this._src = null;
  }

  isSupported() { return true; } // 本实现走 getUserMedia，Chromium 一定支持

  start() {
    if (this._active) { this.stop(); return; }
    this._begin().catch((e) => {
      console.warn('[VoiceInput] 打开麦克风失败，降级键盘:', e);
      this._setActive(false);
      this._onUnsupported();
      this._fallbackPrompt();
    });
  }

  async _begin() {
    this._stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    const AC = window.AudioContext || window.webkitAudioContext;
    this._ctx = new AC();
    const src = this._ctx.createMediaStreamSource(this._stream);
    // ScriptProcessor 兼容性最好；bufferSize 4096
    const proc = this._ctx.createScriptProcessor(4096, 1, 1);
    const targetRate = 16000;
    proc.onaudioprocess = (e) => {
      const input = e.inputBuffer.getChannelData(0); // Float32, 原采样率
      const pcm16 = VoiceInput._downsampleToPcm16(input, this._ctx.sampleRate, targetRate);
      try { this._sendBinary(pcm16.buffer); } catch (e) { /* 忽略发送抖动 */ }
    };
    src.connect(proc);
    proc.connect(this._ctx.destination); // 必须接 destination 才会持续回调（即便不发声）
    this._src = src; this._proc = proc;

    this._sendControl({ event: 'start_recording' });
    this._setActive(true);
    console.log('[VoiceInput] 开始录音, mic 采样率=', this._ctx.sampleRate);
  }

  stop() {
    if (!this._active) return;
    // 先停止，再通知后端收尾
    this._cleanupAudio();
    this._setActive(false);
    this._sendControl({ event: 'stop_recording' });
    console.log('[VoiceInput] 停止录音，等待后端转写');
  }

  /* ---------- 内部 ---------- */
  _cleanupAudio() {
    try { this._proc && this._proc.disconnect(); } catch (e) {}
    try { this._src && this._src.disconnect(); } catch (e) {}
    try { this._stream && this._stream.getTracks().forEach(t => t.stop()); } catch (e) {}
    try { this._ctx && this._ctx.close(); } catch (e) {}
    this._proc = this._src = this._stream = this._ctx = null;
  }

  _setActive(on) {
    this._active = on;
    this._onStateChange(on ? 'recording' : 'idle');
  }

  _fallbackPrompt() {
    const text = (window.prompt('（麦克风不可用）请输入你想说的话：') || '').trim();
    if (text) this._onResult(text, 'keyboard');
  }

  /** 把任意采样率 Float32 线性重采样到 16k 并量化成 PCM16（小端 Int16Array 的 buffer）。 */
  static _downsampleToPcm16(input, fromRate, toRate) {
    if (fromRate === toRate) {
      return VoiceInput._floatToPcm16(input);
    }
    const ratio = fromRate / toRate;
    const outLen = Math.floor(input.length / ratio);
    const out = new Float32Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const pos = i * ratio;
      const i0 = Math.floor(pos);
      const i1 = Math.min(i0 + 1, input.length - 1);
      const frac = pos - i0;
      out[i] = input[i0] * (1 - frac) + input[i1] * frac;
    }
    return VoiceInput._floatToPcm16(out);
  }

  static _floatToPcm16(f32) {
    const out = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
      let s = Math.max(-1, Math.min(1, f32[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    return out;
  }
}
