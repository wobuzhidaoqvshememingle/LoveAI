/**
 * emotion.js
 * 情绪系统：好感度 affection / 忠诚度 loyalty / 心情 mood / 饱食度 satiety（0~100）。
 * 随时间缓慢衰减；点击角色会小幅提升；可由 WebSocket 外部更新。
 * R3.3-78：好感度（引导线）与忠诚度（反制线）双轨独立存储，桌宠表现需区分二者上涨。
 */

export class EmotionSystem {
  constructor(initial = {}) {
    this._affection = this._clamp(initial.affection ?? 50);
    this._loyalty = this._clamp(initial.loyalty ?? 50); // R3.3-78 忠诚度＝听话程度
    this._mood = this._clamp(initial.mood ?? 60);
    this._satiety = this._clamp(initial.satiety ?? 70);

    this._listeners = [];
    this._decayTimer = setInterval(() => this._decay(), 30000); // 每 30 秒衰减一次
  }

  _clamp(v) {
    return Math.max(0, Math.min(100, v));
  }

  _decay() {
    this._mood = this._clamp(this._mood - 0.1);
    this._satiety = this._clamp(this._satiety - 0.2);
    this._notify();
  }

  /* ---------- getters ---------- */
  get affection() { return this._affection; }
  get loyalty() { return this._loyalty; }
  get mood() { return this._mood; }
  get satiety() { return this._satiety; }

  /** 心情低落判定（用于影响表情） */
  get isLow() {
    return this._mood < 30;
  }

  /* ---------- setters / 动作 ---------- */
  onTap() {
    this._affection = this._clamp(this._affection + 0.5);
    this._mood = this._clamp(this._mood + 0.3);
    this._notify();
  }

  feed(amount = 20) {
    this._satiety = this._clamp(this._satiety + amount);
    this._mood = this._clamp(this._mood + amount * 0.5);
    this._notify();
  }

  /** 外部（WebSocket）直接覆写部分字段 */
  update({ affection, loyalty, mood, satiety } = {}) {
    if (affection != null) this._affection = this._clamp(affection);
    if (loyalty != null) this._loyalty = this._clamp(loyalty); // R3.3-78
    if (mood != null) this._mood = this._clamp(mood);
    if (satiety != null) this._satiety = this._clamp(satiety);
    this._notify();
  }

  onChange(fn) {
    this._listeners.push(fn);
  }

  /** 当前快照（用于 update 前后比对，判定本轮哪项上涨） */
  snapshot() {
    return {
      affection: this._affection,
      loyalty: this._loyalty,
      mood: this._mood,
      satiety: this._satiety,
    };
  }

  /**
   * R3.3-78 纯判定：本轮哪项指标上涨。
   * @param {{affection?:number, loyalty?:number}} prevSnap
   * @param {{affection?:number, loyalty?:number}} nextSnap
   * @returns {{metric: "loyalty"|"affection"|null, delta: number}}
   *   仅 loyalty 上涨 → {metric:"loyalty", delta>0}
   *   仅 affection 上涨 → {metric:"affection", delta>0}
   *   两者同涨 / 都不涨 → {metric:null, delta: 涨幅较大者(都涨时) 或 0}
   */
  detectRise(prevSnap = {}, nextSnap = {}) {
    const dl = (nextSnap.loyalty ?? prevSnap.loyalty ?? 0) - (prevSnap.loyalty ?? 0);
    const da = (nextSnap.affection ?? prevSnap.affection ?? 0) - (prevSnap.affection ?? 0);
    const lUp = dl > 0;
    const aUp = da > 0;
    if (lUp && !aUp) return { metric: 'loyalty', delta: dl };
    if (aUp && !lUp) return { metric: 'affection', delta: da };
    if (lUp && aUp) return { metric: null, delta: Math.max(dl, da) }; // 同涨：无法单判，交给 data.metric 显式标记
    return { metric: null, delta: 0 };
  }

  _notify() {
    const snap = {
      affection: this._affection,
      loyalty: this._loyalty,
      mood: this._mood,
      satiety: this._satiety,
    };
    for (const fn of this._listeners) {
      try { fn(snap); } catch (e) { console.error(e); }
    }
  }

  destroy() {
    clearInterval(this._decayTimer);
  }
}
