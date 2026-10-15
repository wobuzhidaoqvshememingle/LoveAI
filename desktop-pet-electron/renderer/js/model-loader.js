/**
 * model-loader.js
 * 封装 Live2D 模型加载、居中适配、表情/口型/呼吸驱动。
 * 加载失败自动降级为 PlaceholderCharacter / ImageCharacter。
 *
 * 关键修复（解决"只露几根头发"问题）：
 *  - 不再硬编码 scale=0.15 / position=(150,410)
 *  - 加载完成后读取模型真实 bounds，按舞台高度自适应缩放
 *  - 水平居中、底部对齐，保证从头到脚完整可见
 *  - 沉浸式模式可调用 setViewport(w,h) 重新适配到全屏
 */

import { PlaceholderCharacter } from './placeholder.js';
import { ImageCharacter } from './image-character.js';

// Haru 表情映射（Cubism 官方样例 F01~F08）
// F01 中性 / F02 开心 / F03 悲伤 / F04 惊讶 / F05 生气 / F06 害羞 / F07 严肃 / F08 困倦
const EXPRESSION_MAP = {
  idle:      'F01',
  happy:     'F02',
  sad:       'F03',
  surprised: 'F04',
  angry:     'F05',
  shy:       'F06',
  thinking:  'F07',
  sleeping:  'F08',
};

export class CharacterModel {
  constructor(stage) {
    this.stage = stage;            // PIXI.Container（舞台）
    this.view = null;              // 当前显示对象
    this._isLive2D = false;
    this._isImage = false;
    this._modelConfig = null;

    // 视口尺寸（由 app.js 注入）
    this.viewportW = 400;
    this.viewportH = 600;

    // 基础几何（fit 后写入）
    this._baseScale = 1;
    this._baseX = 0;   // 模型锚点在舞台上的 x
    this._baseY = 0;   // 模型锚点在舞台上的 y

    // 动画偏移
    this._breathPhase = 0;
    this._offsetX = 0;
    this._offsetY = 0;
    this._rotation = 0;

    // Q弹脉冲（点击/开心时的缩放弹跳，1.0 = 无）
    this._pulse = 1.0;
    this._pulseV = 0;
  }

  /** 设置舞台视口尺寸（px），并立即重新适配 */
  setViewport(w, h, opts = {}) {
    this.viewportW = w;
    this.viewportH = h;
    this._layoutOpts = opts;   // { alignX: 'center'|'left'|'right', bottomMargin }
    this._fit();
  }

  /**
   * 尝试加载模型
   * @param {object} charConfig  characters.json 里的一项
   * @returns {Promise<boolean>} 是否成功使用 Live2D
   */
  async load(charConfig) {
    this._modelConfig = charConfig;

    // 图片角色（AI生成立绘；R3.3-82：cybergf 挂连续帧目录）
    if (charConfig.imagePath) {
      try {
        // 若同目录下存在 frames/ 连续帧（含 manifest.json），启用帧动画模式
        const dir = charConfig.imagePath.replace(/\/[^/]*$/, '/');
        const framesDir = dir + 'frames/';
        const imgChar = new ImageCharacter({ imagePath: charConfig.imagePath, framesDir });
        await imgChar.load();
        this._attach(imgChar);
        this._isLive2D = false;
        this._isImage = true;
        console.log('[model-loader] 图片角色加载成功:', charConfig.imagePath);
        this._fit();
        return true;
      } catch (e) {
        console.warn('[model-loader] 图片加载失败，降级占位:', e.message || e);
        return this._usePlaceholder();
      }
    }

    // 无模型路径 → 直接占位
    if (!charConfig.modelPath) {
      return this._usePlaceholder();
    }

    // 检查 pixi-live2d-display 是否挂载
    const Live2DModel = this._getLive2DClass();
    if (!Live2DModel) {
      console.warn('[model-loader] pixi-live2d-display 未加载，使用占位角色');
      return this._usePlaceholder();
    }

    try {
      // 通过 fetch 探测模型文件是否存在
      const resp = await fetch(charConfig.modelPath);
      if (!resp.ok) throw new Error('模型文件不存在: ' + charConfig.modelPath);

      // 加载 Live2D
      const model = await Live2DModel.from(charConfig.modelPath, { autoInteract: false });

      // 等一帧让 bounds 稳定
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

      this._attach(model);
      this._isLive2D = true;

      // 记录模型原始（未缩放）尺寸，作为 fit 的基准
      // pixi-live2d-display 在 internalModel 上暴露 originalWidth/originalHeight
      this._natW = (model.internalModel && model.internalModel.originalWidth) || model.width;
      this._natH = (model.internalModel && model.internalModel.originalHeight) || model.height;
      if (!this._natW || !this._natH) {
        // 兜底：当前 model.width 是 scale=1 时的尺寸
        this._natW = model.width;
        this._natH = model.height;
      }
      console.log('[model-loader] 模型自然尺寸:', this._natW, 'x', this._natH);

      // 应用配置里的用户缩放（相对倍率，1.0 = 不额外缩放）
      let cfgScale = charConfig.scale && charConfig.scale > 0 ? charConfig.scale : 1.0;
      // 历史遗留：0.15 曾是硬编码"绝对缩放"，归一化为 1.0
      if (Math.abs(cfgScale - 0.15) < 0.001) cfgScale = 1.0;
      this._userScale = cfgScale;

      this._fit();
      console.log('[model-loader] Live2D 模型加载成功:', charConfig.modelPath);
      return true;
    } catch (e) {
      console.warn('[model-loader] Live2D 加载失败，降级到占位角色:', e && e.message || e);
      return this._usePlaceholder();
    }
  }

  /** 根据视口尺寸自适应模型大小与位置（核心修复） */
  _fit() {
    if (!this.view) return;

    if (this._isLive2D) {
      // 自然尺寸（未缩放），避免重复 fit 时把"已缩放后的 width"当成原始尺寸
      const natW = this._natW || this.view.width;
      const natH = this._natH || this.view.height;
      if (!natW || !natH) return;

      // 目标：模型高度占视口高度的 92%（留 4% 上下边距）
      // 若模型过宽（横图），则按宽度 95% 限制
      const targetH = this.viewportH * 0.92;
      const targetW = this.viewportW * 0.95;
      let scale = Math.min(targetH / natH, targetW / natW);

      // 用户设置的额外缩放倍率（相对倍率，1.0 = 原始）
      if (this._userScale && this._userScale > 0) {
        scale *= this._userScale;
      }

      this._baseScale = scale;

      // 锚点：用 (0.5, 1) 底部居中，方便呼吸/晃动
      this.view.anchor.set(0.5, 1);

      // 水平对齐：默认居中；沉浸式可偏右
      const opts = this._layoutOpts || {};
      let cx = this.viewportW / 2;
      if (opts.alignX === 'right') cx = this.viewportW * 0.72;
      else if (opts.alignX === 'left') cx = this.viewportW * 0.28;

      // 底部留 8px 边距
      const bottomMargin = opts.bottomMargin != null ? opts.bottomMargin : 8;
      this._baseX = cx;
      this._baseY = this.viewportH - bottomMargin;

      this._applyTransform();
    } else if (this._isImage) {
      // 图片角色：让它在新视口里居中
      if (this.view._sprite) {
        const tex = this.view._sprite.texture;
        // contain：宽、高同时限制，取较小缩放，保证整个人完整落在窗口内
        // （帧为 3:4、角落窗口为 2:3，只按高度会让宽度超出、贴边时被屏幕裁切）
        const targetH = this.viewportH * 0.92;
        const targetW = this.viewportW * 0.92;
        const scale = Math.min(targetH / tex.height, targetW / tex.width);
        this.view._sprite.scale.set(scale);
        this.view._sprite.position.set(this.viewportW / 2, this.viewportH / 2);
        // 遮罩椭圆位置同步（静态兜底模式）
        if (this.view._maskGraphics) {
          this.view._maskGraphics.clear();
          this.view._maskGraphics.beginFill(0xffffff, 1);
          this.view._maskGraphics.drawEllipse(this.viewportW / 2, this.viewportH / 2,
            tex.width * scale * 0.5, tex.height * scale * 0.5);
          this.view._maskGraphics.endFill();
        }
      }
    } else {
      // 占位团子
      this.view.position.set(this.viewportW / 2, this.viewportH * 0.7);
    }
  }

  /** 把 scale / position / rotation / offset 合成到 view 上 */
  _applyTransform() {
    if (!this.view) return;
    // 呼吸浮动（y 方向 ±3px）
    const breathY = Math.sin(this._breathPhase) * 3;
    this.view.scale.set(this._baseScale * this._pulse);
    this.view.rotation = this._rotation;
    this.view.position.set(
      this._baseX + this._offsetX,
      this._baseY + breathY + this._offsetY
    );
  }

  _getLive2DClass() {
    if (window.PIXI && window.PIXI.live2d && window.PIXI.live2d.Live2DModel) {
      return window.PIXI.live2d.Live2DModel;
    }
    if (window.PIVOLY && window.PIVOLY.live2d && window.PIVOLY.live2d.Live2DModel) {
      return window.PIVOLY.live2d.Live2DModel;
    }
    if (window.Live2DModel) return window.Live2DModel;
    return null;
  }

  _usePlaceholder() {
    if (this.view) this.stage.removeChild(this.view);
    this.view = new PlaceholderCharacter();
    this.stage.addChild(this.view);
    this._isLive2D = false;
    this._isImage = false;
    this._fit();
    return false;
  }

  _attach(live2dModel) {
    if (this.view) this.stage.removeChild(this.view);
    this.view = live2dModel;
    this.stage.addChild(this.view);
  }

  /* ---------- 统一 API ---------- */

  /** 眼睛跟随目标 -1~1 */
  setLookTarget(x, y) {
    if (!this.view) return;
    if (this._isLive2D) {
      try {
        // view.focus(x,y) 内部会 toModelPosition 并按 originalWidth/Height 归一化，
        // 它期望的是“屏幕像素坐标”，直接传 -1~1 会被错误换算。
        // 外部传入的是归一化 -1~1，直接写 focusController.target，
        // 由库自身 updateFocus() 平滑叠加到 ParamEyeBallX/Y 与 ParamAngleX/Y。
        const fc = this.view.internalModel && this.view.internalModel.focusController;
        if (fc) fc.focus(x, y);
      } catch (e) { /* 某些模型不支持 */ }
    } else {
      this.view.setLookTarget(x, y);
    }
  }

  /** 表情：idle / happy / surprised / thinking / sleeping / sad / angry / shy */
  setExpression(expr) {
    if (!this.view) return;
    if (this._isLive2D) {
      const expId = EXPRESSION_MAP[expr];
      if (expId) {
        try { this.view.expression(expId); } catch (e) { /* ignore */ }
      }
      // 同时播放对应 idle 动作（轻量）
      if (expr === 'idle' || expr === 'sleeping') {
        // idle 动作由 motionManager 自动循环，不强制打断
      }
    } else {
      this.view.setExpression(expr);
    }
  }

  /** 播放指定动作（来自 WebSocket action 事件） */
  playMotion(motionName) {
    if (!this.view) return;
    if (this._isLive2D) {
      try { this.view.motion('TapBody', Math.floor(Math.random() * 4)); } catch (e) { /* ignore */ }
    } else {
      if (['happy', 'surprised', 'thinking'].includes(motionName)) {
        this.view.setExpression(motionName);
      }
    }
  }

  /** 触发一次Q弹缩放（amount>0 放大冲击，如 0.08） */
  pulse(amount) {
    this._pulse = 1 + amount;
    this._pulseV = 0;
    this._applyTransform();
  }

  /** 弹簧衰减脉冲，由 app.js 每帧调用（dt 秒） */
  updatePulse(dt) {
    if (Math.abs(this._pulse - 1) < 0.0005 && Math.abs(this._pulseV) < 0.0005) {
      this._pulse = 1;
      this._pulseV = 0;
      return;
    }
    const k = 140;   // 弹簧刚度（越大回弹越快）
    const c = 14;    // 阻尼（越大震荡越少）
    const force = (1 - this._pulse) * k - this._pulseV * c;
    this._pulseV += force * dt;
    this._pulse += this._pulseV * dt;
    this._applyTransform();
  }

  /** 设置模型整体额外缩放倍率（设置面板实时调大小，1.0 = 原始） */
  setScale(v) {
    if (!this.view) return;
    // v 是用户 slider 直接给的倍率（0.5 ~ 2.0）
    this._userScale = v;
    this._fit();
  }

  /** 当前缩放 */
  getScale() {
    return this._baseScale;
  }

  /** 旋转（弧度） */
  setRotation(r) {
    this._rotation = r;
    this._applyTransform();
  }

  /** 水平偏移（px，晃动用） */
  setOffsetX(x) {
    this._offsetX = x;
    this._applyTransform();
  }

  /** 嘴巴张合 0~1（说话时外部每帧驱动） */
  setMouthOpen(v) {
    if (!this.view) return;
    if (this._isLive2D) {
      try {
        const core = this.view.internalModel && this.view.internalModel.coreModel;
        if (core) {
          // Haru 的 LipSync 参数是 ParamMouthOpenY（不是 MouthOpen）
          // 兼容两种参数名
          try { core.setParameterValueById('ParamMouthOpenY', v); } catch (e) {}
          try { core.setParameterValueById('MouthOpen', v); } catch (e) {}
        }
      } catch (e) { /* ignore */ }
    } else {
      this.view.setMouthOpen(v);
    }
  }

  /** 手动眨眼（若 motionManager 未自动处理）：0~1 闭合适中 */
  setEyeOpen(v) {
    if (!this.view || !this._isLive2D) return;
    try {
      const core = this.view.internalModel && this.view.internalModel.coreModel;
      if (core) {
        try { core.setParameterValueById('ParamEyeLOpen', v); } catch (e) {}
        try { core.setParameterValueById('ParamEyeROpen', v); } catch (e) {}
      }
    } catch (e) {}
  }

  /** 每帧更新（呼吸相位推进 + 非 Live2D 角色 update） */
  update(dt) {
    this._breathPhase += dt * 2.2;   // 呼吸周期 ~2.8 秒
    if (this._isLive2D) {
      this._applyTransform();
    } else if (this.view) {
      this.view.update(dt);
    }
  }

  destroy() {
    if (this.view) {
      this.stage.removeChild(this.view);
      if (this.view.destroy) this.view.destroy({ children: true });
      this.view = null;
    }
  }
}
