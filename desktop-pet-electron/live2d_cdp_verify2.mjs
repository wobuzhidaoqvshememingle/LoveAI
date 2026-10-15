// 第二轮 CDP 实测：同帧同步读回，排除主循环覆盖
const BASE = 'http://127.0.0.1:9225';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function getPageTarget() {
  for (let i = 0; i < 15; i++) {
    try {
      const r = await fetch(BASE + '/json');
      const list = await r.json();
      const page = list.find(t => t.type === 'page' && t.url && t.url.includes('index.html'));
      if (page) return page;
    } catch (e) {}
    await sleep(800);
  }
  throw new Error('no page');
}
function cdpCall(ws, id, method, params) {
  return new Promise((resolve, reject) => {
    const onMsg = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id === id) { ws.removeEventListener('message', onMsg);
        if (msg.error) reject(new Error(JSON.stringify(msg.error))); else resolve(msg.result); }
    };
    ws.addEventListener('message', onMsg);
    ws.send(JSON.stringify({ id, method, params: params || {} }));
  });
}
async function main() {
  const page = await getPageTarget();
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let mid = 1;
  const evalJs = async (expr) => {
    const r = await cdpCall(ws, mid++, 'Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails.exception && r.exceptionDetails.exception.description || r.exceptionDetails.text));
    return r.result.value;
  };
  const out = [];
  const log = (s) => { console.log(s); out.push(s); };

  // A. 同帧：setMouthOpen(0.9) 后立刻读 core 参数
  const mouthSync = await evalJs(`(() => {
    const c = window.__character;
    const core = c.view.internalModel.coreModel;
    c.setMouthOpen(0.9);
    const v = core.getParameterValueById('ParamMouthOpenY');
    // 恢复
    c.setMouthOpen(0);
    return JSON.stringify({ set:0.9, readBack:v });
  })()`);
  log('[mouth-sync] ' + mouthSync);

  // B. 同帧：focus 后立刻读眼球参数
  const lookSync = await evalJs(`(() => {
    const c = window.__character;
    const core = c.view.internalModel.coreModel;
    c.setLookTarget(1, 0.5);
    // 触发一次内部 update 让 focus 写入参数
    try { c.view.internalModel.update(1/60); } catch(e){}
    const g = (id) => { try { return core.getParameterValueById(id); } catch(e){ return 'ERR'; } };
    return JSON.stringify({
      eyeBallX: g('ParamEyeBallX'), eyeBallY: g('ParamEyeBallY'),
      angleX: g('ParamAngleX'), angleY: g('ParamAngleY'),
    });
  })()`);
  log('[look-sync +internalUpdate] ' + lookSync);

  // C. 同帧：手动眨眼 setEyeOpen(0.1)
  const eyeSync = await evalJs(`(() => {
    const c = window.__character;
    const core = c.view.internalModel.coreModel;
    c.setEyeOpen(0.1);
    try { c.view.internalModel.update(1/60); } catch(e){}
    const lo = core.getParameterValueById('ParamEyeLOpen');
    const ro = core.getParameterValueById('ParamEyeROpen');
    c.setEyeOpen(1);
    return JSON.stringify({ set:0.1, readBackL:lo, readBackR:ro });
  })()`);
  log('[eye-sync] ' + eyeSync);

  // D. 呼吸：连续采样 breathPhase + 身体角，看是否在推进
  const breathSeq = [];
  for (let i=0;i<4;i++){
    const s = await evalJs(`(() => {
      const c = window.__character;
      const core = c.view.internalModel.coreModel;
      return JSON.stringify({ breath:c._breathPhase, body: core.getParameterValueById('ParamBodyAngleX') });
    })()`);
    breathSeq.push(s);
    await sleep(400);
  }
  log('[breath-seq] ' + JSON.stringify(breathSeq));

  // E. 表情：setExpression('happy') 后读 expressionManager 当前表情
  const exprCheck = await evalJs(`(() => {
    const c = window.__character;
    c.setExpression('happy');
    const em = c.view.internalModel && c.view.internalModel.motionManager && c.view.internalModel.motionManager.expressionManager;
    return JSON.stringify({
      hasEm: !!em,
      exprNames: em ? em.expressionIDs || em._expressionIDs || 'n/a' : 'n/a',
    });
  })()`);
  log('[expr-happy] ' + exprCheck);

  // F. 再截一张图（happy 状态）
  const shot = await cdpCall(ws, mid++, 'Page.captureScreenshot', { format: 'png' });
  const fs = await import('node:fs');
  fs.writeFileSync(new URL('./live2d_shot_happy.png', import.meta.url), Buffer.from(shot.data, 'base64'));
  log('[shot] live2d_shot_happy.png bytes=' + Buffer.from(shot.data,'base64').length);

  ws.close();
  fs.writeFileSync(new URL('./live2d_cdp_round2.json', import.meta.url), JSON.stringify(out, null, 2));
  console.log('[done]');
}
main().catch(e => { console.error('[fatal]', e.message); process.exit(1); });
