/**
 * dh-view.js — 实时数字人视图（local 模式：渲染进程直连本地驱动 WS）
 *
 * 冻结契约（与并行任务 driver.py 对齐）：
 *  - 驱动 WS：ws://127.0.0.1:9230（占用则退避 9231..9239）
 *  - 发：{"type":"amplitude","value":0..1} / {"type":"hi"}
 *  - 收：{"type":"hello","proto":1} / {"type":"status","state":..,"fps":..,"gpu":..,"msg":..}
 *        / {"type":"frame","fmt":"jpeg|png","data":"<base64>","w":N,"h":N}
 *
 * 设计原则：
 *  - off / cloud / 连不上驱动 → 完全不接管画面，Live2D 原样显示（fail-closed 回落 2D）
 *  - 收到第一帧 frame → 隐藏 #stage（Live2D 画布），显示 dh-canvas
 *  - 断连/驱动退出 → 立刻切回 2D + 状态角标提示
 *  - amplitude 复用 audio-player.js 同一个 RMS 值（app.js 每帧调 sendAmplitude）
 */

export class DigitalHumanView {
  /**
   * @param {object} opts
   * @param {HTMLCanvasElement} opts.canvasEl   数字人帧画布
   * @param {HTMLElement} opts.badgeEl          状态角标
   * @param {HTMLElement} opts.stageEl          Live2D 舞台（接管时隐藏，回落时显示）
   */
  constructor({ canvasEl, badgeEl, stageEl }) {
    this.canvas = canvasEl;
    this.badge = badgeEl;
    this.stage = stageEl;
    this.ctx = this.canvas ? this.canvas.getContext('2d') : null;

    this.mode = 'off';          // off|local|cloud
    this.ws = null;
    this.connected = false;
    this.active = false;         // 已收到帧、正在接管画面
    this.driverState = 'idle';  // 主进程广播的驱动状态
    this.fps = 0;
    this.port = 0;
    this._pendingImages = 0;
  }

  /** 启动时读配置并按需连 WS（mode=local 才连） */
  async init() {
    try {
      const cfg = (window.petAPI && await window.petAPI.dhGetConfig()) || { mode: 'off' };
      this.mode = cfg.mode || 'off';
      console.log('[dh-view] init mode =', this.mode);
    } catch (e) {
      console.warn('[dh-view] 读配置失败，按 off 处理:', e);
      this.mode = 'off';
    }

    // 监听主进程广播的驱动生命周期
    if (window.petAPI && typeof window.petAPI.onDhDriverState === 'function') {
      window.petAPI.onDhDriverState((info) => this.onDriverState(info));
    }

    if (this.mode !== 'local') {
      this._setBadge(this.mode === 'cloud' ? '数字人: 云端模式(说话时生成)' : '', this.mode === 'cloud' ? 'info' : '');
      if (this.mode !== 'cloud') this._hideBadge();
      return;
    }

    // local：驱动由主进程 spawn；这里扫描 9230..9239 找实际端口
    this._setBadge('数字人: 连接驱动中…', 'info');
    this._connectWithPortScan();
  }

  /** 扫描 9230..9239，哪个端口接受 WS 就用哪个；失败则每 3 秒重试，直到连上
   *  （驱动加载模型要几十秒，渲染端先起来时驱动还没 listen，必须重试不能只扫一次） */
  _connectWithPortScan() {
    const tryScan = () => {
      if (this.mode !== 'local' || this.connected) return;
      this._tryPort(0, () => {
        // 一轮扫完没连上，3 秒后再来一轮
        this._scanTimer = setTimeout(tryScan, 3000);
      });
    };
    tryScan();
  }

  _tryPort(i, onFailed) {
    if (this.mode !== 'local') return;
    if (i > 939) { onFailed && onFailed(); return; }
    const port = 9230 + i;
    let ws;
    try {
      ws = new WebSocket('ws://127.0.0.1:' + port);
    } catch (e) {
      this._tryPort(i + 1, onFailed);
      return;
    }
    const timer = setTimeout(() => {
      try { ws.close(); } catch (e) {}
    }, 5000); // 驱动每帧渲染 ~800ms（1.2fps），torch 占 GIL 饿死事件循环，握手响应可能排队 1~2s，放宽到 5s
    ws.onopen = () => {
      clearTimeout(timer);
      this.ws = ws;
      this.connected = true;
      this.port = port;
      if (this._scanTimer) { clearTimeout(this._scanTimer); this._scanTimer = null; }
      console.log('[dh-view] WS 已连 port =', port);
      try { ws.send(JSON.stringify({ type: 'hi' })); } catch (e) {}
      this._wireWs(ws);
    };
    ws.onerror = () => {
      clearTimeout(timer);
      try { ws.close(); } catch (e) {}
      if (!this.connected) this._tryPort(i + 1, onFailed);
    };
  }

  _wireWs(ws) {
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      switch (msg.type) {
        case 'hello':
          console.log('[dh-view] 驱动握手:', msg);
          break;
        case 'status':
          this.fps = msg.fps || 0;
          if (msg.state === 'ready') {
            this._setBadge('数字人: 就绪 ' + (this.fps ? this.fps.toFixed(1) + 'fps' : ''), 'ok');
          } else if (msg.state === 'loading') {
            this._setBadge('数字人: 加载模型中…', 'info');
          } else if (msg.state === 'error') {
            this._setBadge('数字人: 驱动错误 → 2D', 'warn');
            this._fallbackTo2D();
          }
          break;
        case 'frame':
          this._drawFrame(msg);
          break;
        default:
          break;
      }
    };
    ws.onclose = () => {
      this.connected = false;
      this.ws = null;
      console.warn('[dh-view] WS 断开，回落 2D');
      this._fallbackTo2D();
      this._setBadge('数字人: 连接断开 → 2D', 'warn');
    };
  }

  /** 收到一帧：画到 canvas；首帧时接管画面（隐藏 Live2D 舞台） */
  _drawFrame(msg) {
    if (!this.ctx) return;
    const img = new Image();
    img.onload = () => {
      // 自适应画布尺寸
      if (this.canvas.width !== msg.w || this.canvas.height !== msg.h) {
        this.canvas.width = msg.w;
        this.canvas.height = msg.h;
      }
      this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
      this.ctx.drawImage(img, 0, 0);
      // 首帧到达：接管画面
      if (!this.active) {
        this.active = true;
        if (this.stage) this.stage.classList.add('hidden');
        this.canvas.classList.remove('hidden');
        console.log('[dh-view] 首帧到达，接管画面', msg.w + 'x' + msg.h);
      }
    };
    img.src = 'data:image/' + (msg.fmt === 'png' ? 'png' : 'jpeg') + ';base64,' + msg.data;
  }

  /** 主进程广播的驱动状态：error/exited → 回落 2D */
  onDriverState(info) {
    if (!info) return;
    this.driverState = info.state;
    if (info.state === 'error') {
      this._setBadge('数字人: 驱动未就绪 → 2D', 'warn');
      this._fallbackTo2D();
    } else if (info.state === 'exited') {
      this._setBadge('数字人: 驱动退出 → 2D', 'warn');
      this._fallbackTo2D();
    } else if (info.state === 'starting') {
      if (this.mode === 'local') this._setBadge('数字人: 启动驱动中…', 'info');
    } else if (info.state === 'off') {
      this._hideBadge();
    }
  }

  /** 回落 2D：显示 Live2D 舞台，隐藏数字人画布 */
  _fallbackTo2D() {
    this.active = false;
    if (this.stage) this.stage.classList.remove('hidden');
    if (this.canvas) this.canvas.classList.add('hidden');
    if (this.ws) { try { this.ws.close(); } catch (e) {} this.ws = null; }
    this.connected = false;
  }

  /** 把 audio-player 算好的同一振幅值转发给驱动（仅在接管画面时） */
  sendAmplitude(v) {
    if (!this.connected || !this.ws) return;
    if (this.ws.readyState !== WebSocket.OPEN) return;
    try {
      this.ws.send(JSON.stringify({ type: 'amplitude', value: Math.max(0, Math.min(1, v || 0)) }));
    } catch (e) { /* 静默，下一帧再试 */ }
  }

  isActive() { return this.active; }

  /* ---------- 角标 ---------- */
  _setBadge(text, kind) {
    if (!this.badge) return;
    this.badge.textContent = text;
    this.badge.className = 'dh-badge show ' + (kind || '');
  }
  _hideBadge() {
    if (!this.badge) return;
    this.badge.className = 'dh-badge';
  }
}
