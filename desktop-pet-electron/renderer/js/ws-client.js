/**
 * ws-client.js
 * WebSocket 客户端：连接 LoveAI 主程序，接收 thinking/speaking/emotion/action/hello 事件。
 * 断线静默重连，不影响桌宠本地运行。
 *
 * 兼容两种消息格式：
 *   旧格式: { "type": "thinking", "data": { ... } }
 *   新格式: { "event": "thinking", "text": "...", "emotion": "happy", "relationship_stage": "暧昧期" }
 *           { "event": "emotion", "data": { "affection": 75 } }
 *           { "event": "hello", "data": { "version": "1.0", "status": "connected" } }
 */

export class WsClient {
  /**
   * @param {object} opts
   * @param {string} opts.url               默认 ws://localhost:9876
   * @param {number} opts.reconnectInterval 毫秒，默认 3000
   * @param {(type: string, data: any) => void} opts.onMessage
   * @param {(connected: boolean) => void} [opts.onStatus]
   */
  constructor({
    url = 'ws://localhost:9876',
    reconnectInterval = 3000,
    onMessage = () => {},
    onStatus = () => {},
  } = {}) {
    this.url = url;
    this.reconnectInterval = reconnectInterval;
    this._onMessage = onMessage;
    this._onStatus = onStatus;
    this._ws = null;
    this._shouldRun = false;
    this._retryTimer = null;
    this._connected = false;
  }

  connect() {
    this._shouldRun = true;
    this._open();
  }

  disconnect() {
    this._shouldRun = false;
    if (this._retryTimer) {
      clearTimeout(this._retryTimer);
      this._retryTimer = null;
    }
    if (this._ws) {
      this._ws.onopen = this._ws.onmessage = this._ws.onclose = this._ws.onerror = null;
      try { this._ws.close(); } catch (e) { /* ignore */ }
      this._ws = null;
    }
    this._connected = false;
  }

  isConnected() {
    return this._connected;
  }

  _open() {
    if (!this._shouldRun) return;
    try {
      this._ws = new WebSocket(this.url);
    } catch (e) {
      this._scheduleRetry();
      return;
    }

    this._ws.onopen = () => {
      console.log('[ws] 已连接 LoveAI 主程序:', this.url);
      this._connected = true;
      this._onStatus(true);
    };

    this._ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (!msg) return;

        // R3.3-76 心跳：收到 ping 立即回 pong（在客户端层自动处理，不打扰业务）
        if (msg.event === 'ping') {
          try { this.send({ event: 'pong', data: { ts: Date.now() } }); } catch (e) { /* ignore */ }
          return;
        }

        // 新格式：顶层有 event 字段
        if (msg.event) {
          // data 字段可能嵌套（如 emotion/hello 事件），也可能没有
          // 把整条消息作为 data 传下去，这样 text/emotion/relationship_stage 都能拿到
          const data = msg.data || {};
          // 把顶层的 text/emotion/relationship_stage 也合并到 data 里
          if (msg.text != null) data.text = msg.text;
          if (msg.emotion != null) data.emotion = msg.emotion;
          if (msg.relationship_stage != null) data.relationship_stage = msg.relationship_stage;
          if (msg.affection != null) data.affection = msg.affection;
          // 语音双向协议新增字段：ai_speak.audio_url / speech_state.state
          if (msg.audio_url != null) data.audio_url = msg.audio_url;
          if (msg.state != null) data.state = msg.state;
          this._onMessage(msg.event, data);
        }
        // 旧格式：顶层有 type 字段
        else if (msg.type) {
          this._onMessage(msg.type, msg.data || {});
        }
      } catch (e) {
        console.warn('[ws] 消息解析失败:', e);
      }
    };

    this._ws.onclose = () => {
      this._connected = false;
      this._onStatus(false);
      if (this._shouldRun) this._scheduleRetry();
    };

    this._ws.onerror = () => {
      // 错误后会触发 close
      try { this._ws.close(); } catch (e) { /* ignore */ }
    };
  }

  _scheduleRetry() {
    if (this._retryTimer) return;
    this._retryTimer = setTimeout(() => {
      this._retryTimer = null;
      this._open();
    }, this.reconnectInterval);
  }

  /** 主动发 JSON 消息 */
  send(obj) {
    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      try { this._ws.send(JSON.stringify(obj)); } catch (e) { /* ignore */ }
    }
  }

  /** R3.3-76 上行：把桌宠侧设置变更（点路径 patch）发回主程序回写 */
  sendConfigUpdate(patch) {
    if (patch && typeof patch === 'object') {
      this.send({ event: 'config_update', data: patch });
    }
  }

  /** R3.3-76 上行：报到时携带桌宠版本号，供主程序版本对齐校验 */
  sendHello(version) {
    this.send({ event: 'hello', data: { version: version || '0.0.0' } });
  }

  /** 发二进制帧（录音 PCM16） */
  sendBinary(buf) {
    if (this._ws && this._ws.readyState === WebSocket.OPEN) {
      try { this._ws.send(buf); } catch (e) { /* ignore */ }
    }
  }
}
