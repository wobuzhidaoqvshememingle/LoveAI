/**
 * pet-frames.js  (R3.3-82)
 * 连续帧动画控制器：以 preview_A 写实真人形象为基准的关键帧 + 帧间淡入混合（crossfade）。
 *
 * 三套动画：
 *  - 说话嘴型：6 关键帧（base/闭嘴→微开→半开→全开→半开→微开），由音频振幅 0..1 连续选帧，相邻帧 90ms 淡入；
 *  - 眨眼：开→半→闭→半→开，5 步步进（每步 ~35ms），单次眨眼 ~175ms；
 *  - idle 呼吸：idle 时 base↔breath_up 正弦微混合（最大 28%），叠加外层呼吸缩放。
 *
 * 本类只算"该显示哪两张图、混合比多少"，不绑死渲染方式：
 *  - PIXI 角标模式：两张 Sprite，alpha = mix；
 *  - 沉浸式 DOM：两张堆叠 <img>，opacity = mix。
 */

const DEFAULT_MANIFEST = {
  mouthSequence: ['base', 'mouth_01', 'mouth_02', 'mouth_03', 'mouth_04', 'mouth_03', 'mouth_02', 'mouth_05'],
  blinkSequence: ['base', 'eye_half', 'eye_closed', 'eye_half', 'base'],
  expressions: {
    idle: 'base', happy: 'expr_happy', surprised: 'expr_surprised',
    thinking: 'expr_thinking', sleeping: 'expr_sleeping',
    shy: 'expr_shy', angry: 'expr_angry', sad: 'expr_surprised',
  },
};

const BLINK_STEP_S = 0.035;      // 眨眼每步时长
const FADE_S = 0.09;             // 帧间淡入时长（说话快速选帧时也靠它平滑）
const BREATH_PERIOD_S = 3.2;     // 呼吸周期
const BREATH_BLEND_MAX = 0.28;   // idle 时 base↔breath_up 最大混合比

export class PetFrameAnimator {
  /** @param {string} framesDir 形如 '../assets/models/cybergf/frames/' */
  constructor(framesDir) {
    this.framesDir = framesDir;
    this.manifest = DEFAULT_MANIFEST;
    this.images = {};
    this.loaded = false;

    this.expression = 'idle';
    this.speaking = false;
    this.mouthAmp = 0;

    // 输出（update 后读取）
    this.imgA = null;    // 底图
    this.imgB = null;    // 顶图（可能为 null）
    this.mix = 0;        // 顶图不透明度 0..1
    this.frameName = 'base';

    // 眨眼调度
    this._blinkCountdown = 2.5 + Math.random() * 2.5;
    this._blinkStep = -1;
    this._blinkStepT = 0;
    this._breathT = Math.random() * BREATH_PERIOD_S;

    // 淡入
    this._fadeFromImg = null;   // 淡入起点底图
    this._fadeT = FADE_S;       // >=FADE 表示无淡入
    this._curD = 'base';        // 当前已稳定到的目标帧名
  }

  async load() {
    try {
      const resp = await fetch(this.framesDir + 'manifest.json');
      if (resp.ok) this.manifest = Object.assign({}, DEFAULT_MANIFEST, await resp.json());
    } catch (e) { /* 用内置默认 */ }

    const names = new Set([
      ...this.manifest.mouthSequence,
      ...this.manifest.blinkSequence,
      ...Object.values(this.manifest.expressions),
      'breath_up',
    ]);
    await Promise.all([...names].map(name => new Promise(resolve => {
      const img = new Image();
      img.onload = () => { this.images[name] = img; resolve(); };
      img.onerror = () => { console.warn('[frames] 帧加载失败:', name); resolve(); };
      img.src = this.framesDir + name + '.png';
    })));
    this.loaded = true;
    return this;
  }

  _img(name) { return this.images[name] || null; }

  setExpression(expr) {
    if (this.manifest.expressions[expr]) this.expression = expr;
  }

  setSpeaking(b) {
    this.speaking = !!b;
    if (!this.speaking) this.mouthAmp = 0;
  }

  /** 连续嘴部振幅 0..1 */
  setMouthAmp(v) {
    this.mouthAmp = Math.max(0, Math.min(1, v || 0));
  }

  _exprFrame() { return this.manifest.expressions[this.expression] || 'base'; }

  update(dt) {
    if (!this.loaded) return this;

    // 眨眼倒计时
    if (this._blinkStep < 0) {
      this._blinkCountdown -= dt;
      if (this._blinkCountdown <= 0) {
        this._blinkStep = 0;
        this._blinkStepT = 0;
      }
    } else {
      this._blinkStepT += dt;
      if (this._blinkStepT >= BLINK_STEP_S) {
        this._blinkStepT = 0;
        this._blinkStep++;
        if (this._blinkStep >= this.manifest.blinkSequence.length) {
          this._blinkStep = -1;
          this._blinkCountdown = 2.5 + Math.random() * 2.5;
        }
      }
    }

    // 1) 决定目标帧 D
    let D;
    if (this.speaking && this.mouthAmp > 0.02) {
      const seq = this.manifest.mouthSequence;
      const idx = Math.round(this.mouthAmp * (seq.length - 1));
      D = seq[Math.max(0, Math.min(seq.length - 1, idx))];
    } else if (this._blinkStep >= 0 && this._blinkStep < this.manifest.blinkSequence.length) {
      D = this.manifest.blinkSequence[this._blinkStep];
    } else {
      D = this._exprFrame();
    }

    // 2) idle 呼吸：base↔breath_up 正弦微混合（仅 idle、不说话、不眨眼）
    let breathMix = 0;
    if (!this.speaking && this._blinkStep < 0 && this.expression === 'idle') {
      this._breathT += dt;
      const phase = (this._breathT % BREATH_PERIOD_S) / BREATH_PERIOD_S;
      breathMix = BREATH_BLEND_MAX * (0.5 - 0.5 * Math.cos(2 * Math.PI * phase));
      if (breathMix < 0.03) breathMix = 0;
    }

    // 3) 淡入推进：D 变化时从当前可见图淡入
    if (D !== this._curD) {
      this._fadeFromImg = this.imgA || this._img(this._curD);
      this._fadeT = 0;
      this._curD = D;
    }
    this._fadeT += dt;
    const k = Math.min(1, this._fadeT / FADE_S);

    const targetImg = this._img(D);
    if (breathMix > 0) {
      // 呼吸：底=base，顶=breath_up
      this.imgA = this._img('base');
      this.imgB = this._img('breath_up');
      this.mix = breathMix;
      this.frameName = 'breath';
    } else if (this._fadeFromImg && k < 1) {
      this.imgA = this._fadeFromImg;
      this.imgB = targetImg;
      this.mix = k;
      this.frameName = D;
    } else {
      this.imgA = targetImg;
      this.imgB = null;
      this.mix = 0;
      this.frameName = D;
    }
    return this;
  }
}
