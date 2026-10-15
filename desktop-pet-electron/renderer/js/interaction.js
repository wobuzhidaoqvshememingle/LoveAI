/**
 * interaction.js
 * 鼠标追踪（眼睛跟随）、窗口拖拽、点击交互。
 * 不使用 CSS -webkit-app-region，全部手动 JS 拖拽。
 */

export class Interaction {
  /**
   * @param {object} opts
   * @param {HTMLElement} opts.canvasEl     PixiJS 渲染出来的 canvas
   * @param {CharacterModel} opts.model     角色模型（用于 setLookTarget）
   * @param {StateMachine} opts.stateMachine
   * @param {EmotionSystem} opts.emotion
   * @param {Bubble} opts.bubble
   * @param {object} opts.characterConfig    当前角色配置（含 tapLines）
   * @param {(hit: boolean) => void} [opts.onTap]
   * @param {() => void} [opts.onDragEnd] 拖动结束（位移>=5px）回调，用于把窗口位置上行共享
   */
  constructor({ canvasEl, model, stateMachine, emotion, bubble, characterConfig, onTap = () => {}, onDragEnd = () => {} }) {
    this.canvas = canvasEl;
    this.model = model;
    this.stateMachine = stateMachine;
    this.emotion = emotion;
    this.bubble = bubble;
    this.charCfg = characterConfig;
    this.onTap = onTap;
    this.onDragEnd = onDragEnd;

    this._isDragging = false;
    this._dragStartX = 0;
    this._dragStartY = 0;
    this._dragTotalDx = 0;
    this._dragTotalDy = 0;
    this._winW = window.innerWidth;
    this._winH = window.innerHeight;

    this._bindEvents();
  }

  _bindEvents() {
    // 鼠标移动：眼睛跟随（整个窗口区域）
    window.addEventListener('mousemove', (e) => this._onMouseMove(e));

    // 鼠标按下：可能是拖拽
    this.canvas.addEventListener('mousedown', (e) => this._onMouseDown(e));
    window.addEventListener('mousemove', (e) => this._onDragMove(e));
    window.addEventListener('mouseup', (e) => this._onMouseUp(e));
  }

  _onMouseMove(e) {
    // 归一化到 -1 ~ 1（相对于窗口中心）
    const x = (e.clientX / this._winW) * 2 - 1;
    const y = (e.clientY / this._winH) * 2 - 1;
    this.model.setLookTarget(x, y);
  }

  _onMouseDown(e) {
    if (e.button !== 0) return; // 只响应左键
    this._isDragging = true;
    this._dragStartX = e.clientX;
    this._dragStartY = e.clientY;
    this._dragTotalDx = 0;
    this._dragTotalDy = 0;
    this.stateMachine.setState('DRAGGING');
    e.preventDefault();
  }

  _onDragMove(e) {
    if (!this._isDragging) return;
    const dx = e.clientX - this._dragStartX;
    const dy = e.clientY - this._dragStartY;
    this._dragTotalDx += dx;
    this._dragTotalDy += dy;

    // 调用主进程拖拽窗口
    if (window.petAPI && typeof window.petAPI.dragWindow === 'function') {
      window.petAPI.dragWindow(dx, dy);
    }

    // 重置起点，这样 mousemove 是增量
    this._dragStartX = e.clientX;
    this._dragStartY = e.clientY;
  }

  _onMouseUp(e) {
    if (!this._isDragging) return;
    this._isDragging = false;

    const moved = Math.abs(this._dragTotalDx) + Math.abs(this._dragTotalDy);
    this.stateMachine.setState('IDLE');

    // 位移 >= 5px 视为真实拖动结束：把窗口最新位置上行共享（双端一致）
    if (moved >= 5) {
      try { this.onDragEnd(); } catch (e) { /* 上行失败不影响拖动 */ }
      return;
    }

    // 位移 < 5px 视为点击
    this._handleTap(e);
  }

  _handleTap(/* e */) {
    // 随机动作
    const motions = ['HAPPY', 'SURPRISED', 'IDLE'];
    const next = motions[Math.floor(Math.random() * motions.length)];
    this.stateMachine.setState(next);
    this.model.setExpression(next.toLowerCase());

    // 情绪 +1
    this.emotion.onTap();

    // 随机台词
    const lines = (this.charCfg && this.charCfg.tapLines) || ['嗯？'];
    const line = lines[Math.floor(Math.random() * lines.length)];
    this.bubble.show(line);

    this.onTap(true);
  }

  /** 窗口尺寸变化时调用 */
  resize() {
    this._winW = window.innerWidth;
    this._winH = window.innerHeight;
  }
}
