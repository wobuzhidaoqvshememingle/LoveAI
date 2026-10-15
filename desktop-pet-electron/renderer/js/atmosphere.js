/**
 * atmosphere.js
 * 赛博氛围层：紫色背光 + 漂浮粒子。
 * 添加在角色下层（z-order），不影响角色交互。
 * 尺寸自适应：通过 resize(w,h) 更新粒子活动范围与背光中心。
 */

export class Atmosphere {
  constructor(stage) {
    this.stage = stage;
    this.container = new PIXI.Container();
    this.particles = [];
    this.w = 400;
    this.h = 600;
    this._buildGlow();
    this._buildParticles();
    stage.addChild(this.container);
  }

  /** 更新视口尺寸（app.js 在窗口变化时调用） */
  resize(w, h) {
    this.w = w;
    this.h = h;
    // 背光中心：视口水平居中、垂直偏下 70%
    this.glow.x = w / 2 - 150;
    this.glow.y = h * 0.55 - 250;
  }

  /** 紫色/粉色径向渐变背光 */
  _buildGlow() {
    const glow = new PIXI.Graphics();
    const cx = 150, cy = 250, r = 160;
    for (let i = 10; i >= 1; i--) {
      const t = i / 10;
      const alpha = 0.025 * t;
      glow.beginFill(0x9b59ff, alpha);
      glow.drawCircle(cx, cy, r * t);
      glow.endFill();
    }
    for (let i = 8; i >= 1; i--) {
      const t = i / 8;
      glow.beginFill(0xff6b9d, 0.015 * t);
      glow.drawCircle(cx + 20, cy - 10, r * 0.7 * t);
      glow.endFill();
    }
    this.glow = glow;
    this.container.addChild(glow);
  }

  /** 漂浮粒子（数字尘埃） */
  _buildParticles() {
    const count = 24;
    for (let i = 0; i < count; i++) {
      const p = new PIXI.Graphics();
      const size = 1 + Math.random() * 2.5;
      const colors = [0xaa88ff, 0xff88cc, 0x88ccff, 0xdda0ff];
      const c = colors[Math.floor(Math.random() * colors.length)];
      p.beginFill(c, 0.3 + Math.random() * 0.4);
      p.drawCircle(0, 0, size);
      p.endFill();

      p.x = Math.random() * this.w;
      p.y = Math.random() * this.h;
      p._vy = -(10 + Math.random() * 25);
      p._vx = (Math.random() - 0.5) * 8;
      p._alpha = 0.3 + Math.random() * 0.4;
      p._pulseSpeed = 1 + Math.random() * 2;
      p._phase = Math.random() * Math.PI * 2;
      p.alpha = p._alpha;

      this.particles.push(p);
      this.container.addChild(p);
    }
  }

  /** 每帧更新 */
  update(dt) {
    for (const p of this.particles) {
      p.y += p._vy * dt;
      p.x += p._vx * dt;
      p._phase += dt * p._pulseSpeed;
      p.alpha = p._alpha * (0.5 + 0.5 * Math.sin(p._phase));

      if (p.y < -10) {
        p.y = this.h + 10;
        p.x = Math.random() * this.w;
      }
      if (p.x < -5) p._vx = Math.abs(p._vx);
      if (p.x > this.w + 5) p._vx = -Math.abs(p._vx);
    }

    const t = performance.now() * 0.0008;
    this.glow.scale.set(1 + Math.sin(t) * 0.03, 1 + Math.cos(t * 0.7) * 0.03);
  }
}
