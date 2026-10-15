/**
 * bubble.js
 * 对话气泡：在角色头顶显示文字，自动消失。
 * 新消息来时旧消息立即被替换。
 */

export class Bubble {
  constructor(el) {
    this.el = el;
    this.textEl = el.querySelector('.bubble-text');
    this._hideTimer = null;
    this._defaultDuration = 3500;
  }

  /**
   * 显示文字
   * @param {string} text
   * @param {number} duration  毫秒，默认 3500
   */
  show(text, duration = this._defaultDuration) {
    if (this._hideTimer) {
      clearTimeout(this._hideTimer);
      this._hideTimer = null;
    }

    this.el.classList.remove('hidden', 'fading');
    // 触发重排，保证动画重新播放
    void this.el.offsetWidth;
    this.textEl.textContent = text;

    // 自动消失
    this._hideTimer = setTimeout(() => this.hide(), duration);
  }

  hide() {
    if (this._hideTimer) {
      clearTimeout(this._hideTimer);
      this._hideTimer = null;
    }
    this.el.classList.add('fading');
    setTimeout(() => {
      this.el.classList.add('hidden');
      this.el.classList.remove('fading');
    }, 300);
  }
}
