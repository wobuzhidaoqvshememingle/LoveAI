/**
 * image-character.js  (R3.3-82 升级)
 * 立绘角色：优先走"连续帧动画"（PetFrameAnimator，关键帧+交叉淡化），
 * 帧目录缺失时自动降级为旧的单张静态立绘。
 *
 * 对外接口与 PlaceholderCharacter / Live2DModel 一致：
 *   load() / setExpression / setMouthOpen / setLookTarget / update / playMotion / setScale
 */

import { PetFrameAnimator } from './pet-frames.js';

export class ImageCharacter extends PIXI.Container {
  /**
   * @param {object} opts
   * @param {string} opts.imagePath  兜底静态立绘（帧模式不可用时用）
   * @param {string} [opts.framesDir] 连续帧目录（含 manifest.json）
   */
  constructor(opts) {
    super();
    if (typeof opts === 'string') opts = { imagePath: opts };
    this.imagePath = opts.imagePath;
    this.framesDir = opts.framesDir || null;

    this._sprite = null;        // 静态模式底图
    this._spriteA = null;       // 帧模式底图
    this._spriteB = null;       // 帧模式顶图
    this._animator = null;
    this._frameMode = false;

    this._expression = 'idle';
    this._mouthOpen = 0;
    this._breathPhase = 0;
    this._lookX = 0; this._lookY = 0;
    this._targetLookX = 0; this._targetLookY = 0;
    this._texCache = {};
  }

  async load() {
    // 1) 尝试连续帧模式
    if (this.framesDir) {
      try {
        this._animator = new PetFrameAnimator(this.framesDir);
        await this._animator.load();
        if (this._animator.images && Object.keys(this._animator.images).length > 2) {
          this._frameMode = true;
          this._buildFrameSprites();
          console.log('[image-character] 连续帧模式启用:', this.framesDir);
          return this;
        }
      } catch (e) {
        console.warn('[image-character] 连续帧初始化失败，降级单图:', e && e.message || e);
      }
    }
    // 2) 兜底：单张静态图
    await this._loadStatic();
    return this;
  }

  _buildFrameSprites() {
    // 两张同尺寸精灵堆叠，交叉淡化
    const firstImg = this._animator.imgA || this._animator.images['base'];
    this._spriteA = new PIXI.Sprite(this._tex(firstImg));
    this._spriteB = new PIXI.Sprite(this._tex(firstImg));
    this._spriteA.anchor.set(0.5, 0.5);
    this._spriteB.anchor.set(0.5, 0.5);
    this._spriteB.alpha = 0;
    this.addChild(this._spriteB);
    this.addChild(this._spriteA);
    this._sprite = this._spriteA; // 兼容 _fit() 里对 _sprite.texture 的读取
  }

  _tex(img) {
    if (!img) return PIXI.Texture.EMPTY;
    const key = img.src || Math.random();
    if (!this._texCache[key]) this._texCache[key] = PIXI.Texture.from(img);
    return this._texCache[key];
  }

  async _loadStatic() {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    await new Promise((resolve, reject) => {
      img.onload = resolve;
      img.onerror = () => reject(new Error('图片加载失败: ' + this.imagePath));
      img.src = this.imagePath;
    });
    this._sprite = new PIXI.Sprite(PIXI.Texture.from(img));
    this._sprite.anchor.set(0.5, 0.5);
    this.addChild(this._sprite);
  }

  /* ---------- 统一接口 ---------- */
  setLookTarget(x, y) {
    this._targetLookX = Math.max(-1, Math.min(1, x));
    this._targetLookY = Math.max(-1, Math.min(1, y));
  }

  setExpression(expr) {
    this._expression = expr;
    if (this._animator) this._animator.setExpression(expr);
  }

  /** 嘴部开合 0..1（说话驱动）。 */
  setMouthOpen(v) {
    this._mouthOpen = Math.max(0, Math.min(1, v || 0));
    if (this._animator) {
      this._animator.setSpeaking(this._mouthOpen > 0.05);
      this._animator.setMouthAmp(this._mouthOpen);
    }
  }

  playMotion(name) { this.setExpression(name); }

  /** 用户缩放倍率（model-loader.setScale 传入）。 */
  setScale(s) { /* 缩放由外层 _fit 统一处理 */ }

  update(dt) {
    // 眼睛跟随（整体微移暗示）
    this._lookX += (this._targetLookX - this._lookX) * Math.min(1, dt * 4);
    this._lookY += (this._targetLookY - this._lookY) * Math.min(1, dt * 4);

    if (this._frameMode && this._animator) {
      this._animator.update(dt);
      const a = this._animator.imgA;
      const b = this._animator.imgB;
      this._spriteA.texture = this._tex(a);
      this._spriteB.texture = b ? this._tex(b) : PIXI.Texture.EMPTY;
      this._spriteB.alpha = b ? this._animator.mix : 0;
      // 两帧几何对齐（锚点一致即对齐）
      this._spriteB.position.copyFrom(this._spriteA.position);
      this._spriteB.scale.copyFrom(this._spriteA.scale);
      // 跟随微移作用在容器上，保留 _fit 设好的精灵居中 position
      if (this._expression !== 'sleeping') this.x = this._lookX * 3;
    } else if (this._sprite) {
      // 静态兜底：不做逐帧呼吸缩放（外层 model-loader 已有整体呼吸浮动）
      // 跟随微移作用在容器上，保留精灵居中 position
      if (this._expression !== 'sleeping') this.x = this._lookX * 3;
    }
  }
}
