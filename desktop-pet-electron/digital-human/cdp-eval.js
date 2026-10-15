/**
 * cdp-eval.js — 通过 CDP 在运行中的桌宠页面里执行一段 JS 并打印结果
 * 用法: node cdp-eval.js "<js表达式>"  或  node cdp-eval.js @file.js
 */
let expr = process.argv[2] || '1+1';
if (expr.startsWith('@')) {
  expr = require('fs').readFileSync(expr.slice(1), 'utf-8');
}

(async () => {
  const list = await fetch('http://127.0.0.1:9225/json').then((r) => r.json());
  const page = list.find((p) => p.type === 'page' && p.webSocketDebuggerUrl);
  if (!page) throw new Error('no CDP page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0; const pending = new Map();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); }
  };
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  const send = (method, params = {}) => new Promise((resolve) => {
    const mid = ++id; pending.set(mid, resolve);
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  console.log(JSON.stringify(r.result, null, 2));
  ws.close(); process.exit(0);
})().catch((e) => { console.error(e.message); process.exit(1); });
