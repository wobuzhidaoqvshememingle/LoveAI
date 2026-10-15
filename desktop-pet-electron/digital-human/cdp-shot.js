/**
 * cdp-shot.js — 通过 CDP (localhost:9225) 给运行中的桌宠页面截图
 * 用法: node cdp-shot.js <out.png>
 * 依赖 Node 22 内置 WebSocket，无外部包。
 */
const fs = require('fs');

const outPath = process.argv[2] || 'out.png';

(async () => {
  // 1. 拿目标页面的 webSocketDebuggerUrl
  const list = await fetch('http://127.0.0.1:9225/json').then((r) => r.json());
  const page = list.find((p) => p.type === 'page' && p.webSocketDebuggerUrl);
  if (!page) throw new Error('no CDP page target');

  // 2. 连 WS，发 Page.captureScreenshot
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); }
  };
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  const send = (method, params = {}) => new Promise((resolve) => {
    const mid = ++id;
    pending.set(mid, resolve);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });

  await send('Page.enable');
  const res = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(outPath, Buffer.from(res.data, 'base64'));
  console.log('screenshot saved:', outPath);
  ws.close();
  process.exit(0);
})().catch((e) => { console.error('cdp-shot failed:', e.message); process.exit(1); });
