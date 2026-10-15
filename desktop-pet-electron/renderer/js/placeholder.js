/**
 * placeholder.js
 * 占位角色：当 Live2D 模型不可用时，用 PixiJS Graphics 画一个可爱的小团子。
 * 必须能独立驱动所有状态：idle / thinking / speaking / happy / surprised / sleeping。
 */

export class PlaceholderCharacter extends PIXI.Container {
  constructor() {
    super();

    // ---------- 外观参数 ----------
    this._bodyColor = 0xffb6c1;       // 粉色身体
    this._cheekColor = 0xff8fa3;      // 腮红
    this._eyeColor = 0x333333;
    this._mouthOpen = 0;              // 0 闭合 ~ 1 张开
    this._blinkTimer = 0;
    this._blinkInterval = 2.5;        // 秒
    this._isBlinking = 0;             // 0~1 眨眼相位
    this._breathPhase = 0;
    this._lookX = 0;                  // 眼睛跟随目标 -1~1
    this._lookY = 0;
    this._targetLookX = 0;
    this._targetLookY = 0;
    this._expression = 'idle';        // idle/happy/surprised/thinking/sleeping

    this._buildBody();
    this._buildEyes();
    this._buildCheeks();
    this._buildMouth();

    // 整体缩放（在 300x420 舞台里居中偏下）
    this.scale.set(0.9);
    this.position.set(150, 260);
  }

  /* ---------- 绘制身体 ---------- */
  _buildBody() {
    this._body = new PIXI.Graphics();
    this._body
      .beginFill(this._bodyColor)
      .drawEllipse(0, 0, 90, 100)
      .endFill();
    this.addChild(this._body);
  }

  /* ---------- 眼睛（白 + 瞳孔） ---------- */
  _buildEyes() {
    this._eyeLeft = new PIXI.Container();
    this._eyeRight = new PIXI.Container();

    // 眼白
    const drawEye = (container, x) => {
      const white = new PIXI.Graphics();
      white.beginFill(0xffffff).drawEllipse(0, 0, 18, 22).endFill();
      container.addChild(white);

      // 瞳孔
      const pupil = new PIXI.Graphics();
      pupil.beginFill(this._eyeColor).drawCircle(0, 0, 9).endFill();
      container.addChild(pupil);
      this['_pupil' + (x < 0 ? 'L' : 'R')] = pupil;

      // 高光
      const hl = new PIXI.Graphics();
      hl.beginFill(0xffffff).drawCircle(-3, -4, 3).endFill();
      pupil.addChild(hl);
    };

    drawEye(this._eyeLeft, -1);
    drawEye(this._eyeRight, 1);

    this._eyeLeft.position.set(-32, -20);
    this._eyeRight.position.set(32, -20);
    this.addChild(this._eyeLeft, this._eyeRight);
  }

  /* ---------- 腮红 ---------- */
  _buildCheeks() {
    this._cheekL = new PIXI.Graphics();
    this._cheekL.beginFill(this._cheekColor, 0.7).drawEllipse(-55, 15, 12, 7).endFill();
    this._cheekR = new PIXI.Graphics();
    this._cheekR.beginFill(this._cheekColor, 0.7).drawEllipse(55, 15, 12, 7).endFill();
    this.addChild(this._cheekL, this._cheekR);
  }

  /* ---------- 嘴巴 ---------- */
  _buildMouth() {
    this._mouth = new PIXI.Graphics();
    this.addChild(this._mouth);
    this._redrawMouth();
  }

  _redrawMouth() {
    this._mouth.clear();
    const o = this._mouthOpen;
    if (this._expression === 'happy') {
      // 微笑弧
      this._mouth.lineStyle(3, 0xaa5566, 1);
      this._mouth.arc(0, 20, 14, Math.PI * 0.15, Math.PI * 0.85);
    } else if (this._expression === 'surprised') {
      this._mouth.beginFill(0xaa5566).drawEllipse(0, 22, 8, 10 + o * 8).endFill();
    } else if (this._expression === 'sleeping') {
      this._mouth.lineStyle(2, 0xaa5566, 1);
      this._mouth.moveTo(-8, 22).lineTo(8, 22);
    } else if (o > 0.1) {
      // 说话时嘴巴张合
      this._mouth.beginFill(0xaa5566).drawEllipse(0, 22, 8, 4 + o * 10).endFill();
    } else {
      // 默认小嘴
      this._mouth.lineStyle(2.5, 0xaa5566, 1);
      this._mouth.arc(0, 18, 8, Math.PI * 0.2, Math.PI * 0.8);
    }
  }

  /* ---------- 公共 API ---------- */

  /** 设置眼睛跟随目标（-1 ~ 1） */
  setLookTarget(x, y) {
    this._targetLookX = Math.max(-1, Math.min(1, x));
    this._targetLookY = Math.max(-1, Math.min(1, y));
  }

  /** 设置表达式：idle / happy / surprised / thinking / sleeping */
  setExpression(expr) {
    if (this._expression === expr) return;
    this._expression = expr;
    this._redrawMouth();
  }

  /** 设置嘴巴张开度（0~1），用于说话 */
  setMouthOpen(v) {
    this._mouthOpen = Math.max(0, Math.min(1, v));
    this._redrawMouth();
  }

  /** 每帧更新（dt 单位秒） */
  update(dt) {
    // 呼吸：身体轻微缩放
    this._breathPhase += dt * 2.2;
    const breath = 1 + Math.sin(this._breathPhase) * 0.02;
    this._body.scale.set(1, breath);

    // 眨眼
    this._blinkTimer += dt;
    if (this._blinkTimer > this._blinkInterval) {
      this._blinkTimer = 0;
      this._blinkInterval = 2.0 + Math.random() * 3.0;
      this._isBlinking = 0.001; // 启动眨眼
    }
    if (this._isBlinking > 0) {
      this._isBlinking += dt * 8;
      // 0->1->0 三角波
      const s = Math.sin(this._isBlinking * Math.PI);
      const eyeScaleY = Math.max(0.05, 1 - s);
      this._eyeLeft.scale.y = eyeScaleY;
      this._eyeRight.scale.y = eyeScaleY;
      if (this._isBlinking >= 1) {
        this._isBlinking = 0;
        this._eyeLeft.scale.y = 1;
        this._eyeRight.scale.y = 1;
      }
    }

    // 眼睛平滑跟随
    this._lookX += (this._targetLookX - this._lookX) * Math.min(1, dt * 6);
    this._lookY += (this._targetLookY - this._lookY) * Math.min(1, dt * 6);

    // thinking 时眼睛向上看
    const lookOffsetY = this._expression === 'thinking' ? -0.4 : 0;
    const px = this._lookX * 5;
    const py = this._lookY * 3 + lookOffsetY * 8;
    this._pupilL.position.set(px, py);
    this._pupilR.position.set(px, py);
  }
}
