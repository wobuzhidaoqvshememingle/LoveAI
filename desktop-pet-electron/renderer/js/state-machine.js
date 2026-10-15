/**
 * state-machine.js
 * 桌宠状态机：管理当前状态、状态切换、自动回落到 IDLE。
 * 状态列表：IDLE / THINKING / SPEAKING / HAPPY / SURPRISED / SLEEPING / DRAGGING
 */

export const PetState = {
  IDLE: 'IDLE',
  THINKING: 'THINKING',
  SPEAKING: 'SPEAKING',
  HAPPY: 'HAPPY',
  SURPRISED: 'SURPRISED',
  SLEEPING: 'SLEEPING',
  DRAGGING: 'DRAGGING',
};

// 每个状态持续多久后自动回到 IDLE（毫秒）；null 表示不自动回落
const STATE_DURATION = {
  [PetState.IDLE]: null,
  [PetState.THINKING]: 3000,
  [PetState.SPEAKING]: 3500,
  [PetState.HAPPY]: 2000,
  [PetState.SURPRISED]: 1500,
  [PetState.SLEEPING]: null,
  [PetState.DRAGGING]: null,
};

export class StateMachine {
  constructor() {
    this._state = PetState.IDLE;
    this._listeners = [];          // 状态变化监听器 (oldState, newState, params)
    this._timeoutId = null;
    this._params = {};
  }

  /** 切换状态 */
  setState(newState, params = {}) {
    if (!PetState[newState]) {
      console.warn('[StateMachine] 未知状态:', newState);
      return;
    }
    if (newState === this._state && !params.force) return;

    const old = this._state;
    this._state = newState;
    this._params = params;

    // 清除旧的自动回落定时器
    if (this._timeoutId) {
      clearTimeout(this._timeoutId);
      this._timeoutId = null;
    }

    // 安排自动回落
    const dur = STATE_DURATION[newState];
    if (dur != null) {
      this._timeoutId = setTimeout(() => {
        this._state = PetState.IDLE;
        this._notify(old, PetState.IDLE, {});
      }, dur);
    }

    this._notify(old, newState, params);
  }

  getState() {
    return this._state;
  }

  getParams() {
    return this._params;
  }

  /** 订阅状态变化 */
  onChange(fn) {
    this._listeners.push(fn);
  }

  _notify(oldS, newS, params) {
    for (const fn of this._listeners) {
      try { fn(oldS, newS, params); } catch (e) { console.error(e); }
    }
  }
}
