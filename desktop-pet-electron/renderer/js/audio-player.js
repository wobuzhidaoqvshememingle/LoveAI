/**
 * audio-player.js
 * AI 语音播放封装：
 *  - 用 new Audio(url) 播放后端返回的 mp3
 *  - 用 Web Audio API AnalyserNode 实时取振幅（RMS），驱动立绘口型
 *  - 播放结束回调 onEnded
 *
 * 用法：
 *   const ap = new AudioPlayer({ onEnded: () => {...} });
 *   ap.play('http://127.0.0.1:9877/reply_xxx.mp3');
 *   // 在主循环里：
 *   const amp = ap.sample();   // 0..1，未播放时为 0
 */

export class AudioPlayer {
  /**
   * @param {object} opts
   * @param {() => void} [opts.onEnded]
   * @param {() => void} [opts.onError]
   * @param {(amp:number) => void} [opts.onAmp]  可选：每帧采样后回调当前振幅 0..1
   *        （数字人模式下 app.js 用它把同一 RMS 值转发给本地驱动 WS，绝不另起第二套 TTS/音频分析）
   */
  constructor({ onEnded = () => {}, onError = () => {}, onAmp = null } = {}) {
    this._onEnded = onEnded;
    this._onError = onError;
    this._onAmp = onAmp;

    this._audio = new Audio();
    this._audio.preload = 'auto';
    this._audio.crossOrigin = 'anonymous';

    this._ctx = null;
    this._analyser = null;
    this._source = null;
    this._buf = null;
    this._amp = 0;           // 最近一次采样的振幅 0..1
    this._playing = false;

    this._wireEvents();
  }

  _wireEvents() {
    const a = this._audio;
    a.addEventListener('ended', () => this._finish());
    a.addEventListener('pause', () => { if (!a.ended) this._finish(); });
    a.addEventListener('error', (e) => {
      console.warn('[AudioPlayer] 播放失败:', e);
      this._playing = false;
      this._amp = 0;
      this._onError();
      this._onEnded();
    });
  }

  /** 懒初始化 Web Audio 图（每个 audio 元素只能 createMediaElementSource 一次） */
  _ensureGraph() {
    if (this._source) return;
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      this._ctx = new AC();
      this._source = this._ctx.createMediaElementSource(this._audio);
      this._analyser = this._ctx.createAnalyser();
      this._analyser.fftSize = 512;
      this._buf = new Float32Array(this._analyser.fftSize);
      this._source.connect(this._analyser);
      this._analyser.connect(this._ctx.destination);
    } catch (e) {
      console.warn('[AudioPlayer] Web Audio 初始化失败，将只播放不取振幅:', e);
      this._ctx = null;
      this._analyser = null;
    }
  }

  /**
   * 播放一段音频
   * @param {string} url
   */
  play(url) {
    if (!url) {
      // 没有音频 URL 也当作"说完了"
      this._finish();
      return;
    }
    try {
      this._ensureGraph();
      if (this._ctx && this._ctx.state === 'suspended') {
        this._ctx.resume().catch(() => {});
      }
      // 停止上一段
      try { this._audio.pause(); } catch (e) {}
      this._audio.currentTime = 0;
      this._audio.src = url;
      const p = this._audio.play();
      if (p && p.catch) {
        p.catch((err) => {
          console.warn('[AudioPlayer] play() 被拒绝:', err);
          this._onError();
          this._finish();
        });
      }
      this._playing = true;
    } catch (e) {
      console.warn('[AudioPlayer] 播放异常:', e);
      this._finish();
    }
  }

  /** 立即停止 */
  stop() {
    try { this._audio.pause(); } catch (e) {}
    this._finish();
  }

  _finish() {
    this._playing = false;
    this._amp = 0;
    this._onEnded();
  }

  isPlaying() {
    return this._playing && !this._audio.ended;
  }

  /**
   * 取当前振幅（0..1），在主循环里每帧调用一次
   * 若没有 analyser，返回一个伪振幅（仍能让嘴动，但不随声音变化）
   */
  sample() {
    if (!this.isPlaying()) {
      this._amp = 0;
      return 0;
    }
    if (!this._analyser || !this._ctx) {
      // 降级：用一个跳动的正弦模拟
      this._amp = 0.4 + Math.sin(performance.now() / 90) * 0.3;
      return Math.max(0, Math.min(1, this._amp));
    }
    try {
      this._analyser.getFloatTimeDomainData(this._buf);
      let sum = 0;
      for (let i = 0; i < this._buf.length; i++) {
        sum += this._buf[i] * this._buf[i];
      }
      const rms = Math.sqrt(sum / this._buf.length);
      // RMS 通常很小（0~0.3），放大映射到 0..1
      const v = Math.min(1, rms * 4);
      // 平滑
      this._amp = this._amp * 0.5 + v * 0.5;
      if (typeof this._onAmp === 'function') {
        try { this._onAmp(this._amp); } catch (e) { /* 回调异常不影响口型 */ }
      }
      return this._amp;
    } catch (e) {
      return 0;
    }
  }
}
