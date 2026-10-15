// CDP 实测脚本：连接 localhost:9225，评估 Live2D 模型真渲染 + 驱动能力
// 用法: node live2d_cdp_verify.mjs
const BASE = 'http://127.0.0.1:9225';

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function getPageTarget() {
  for (let i = 0; i < 15; i++) {
    try {
      const r = await fetch(BASE + '/json');
      const list = await r.json();
      const page = list.find(t => t.type === 'page' && t.url && t.url.includes('index.html'));
      if (page) return page;
    } catch (e) { /* retry */ }
    await sleep(800);
  }
  throw new Error('找不到 renderer page target');
}

function cdpCall(ws, id, method, params) {
  return new Promise((resolve, reject) => {
    const onMsg = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id === id) {
        ws.removeEventListener('message', onMsg);
        if (msg.error) reject(new Error(method + ' -> ' + JSON.stringify(msg.error)));
        else resolve(msg.result);
      }
    };
    ws.addEventListener('message', onMsg);
    ws.send(JSON.stringify({ id, method, params: params || {} }));
  });
}

async function main() {
  const page = await getPageTarget();
  console.log('[cdp] page target url:', page.url);
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  let mid = 1;
  const evalJs = async (expr) => {
    const r = await cdpCall(ws, mid++, 'Runtime.evaluate', {
      expression: expr, returnByValue: true, awaitPromise: true,
    });
    if (r.exceptionDetails) throw new Error('eval exception: ' + JSON.stringify(r.exceptionDetails.exception && r.exceptionDetails.exception.description || r.exceptionDetails.text));
    return r.result.value;
  };

  const out = [];
  const log = (s) => { console.log(s); out.push(s); };

  // 1. 等模型加载稳定
  await sleep(2500);

  // 2. 基础状态
  const state = await evalJs(`(() => {
    const c = window.__character;
    if (!c) return JSON.stringify({error:'__character undefined'});
    const v = c.view;
    return JSON.stringify({
      isLive2D: c._isLive2D,
      isImage: c._isImage,
      viewCtor: v ? (v.constructor && v.constructor.name) : null,
      modelConfig: c._modelConfig ? c._modelConfig.id + ' | ' + (c._modelConfig.modelPath||c._modelConfig.imagePath) : null,
      natW: c._natW, natH: c._natH,
      scale: c._baseScale,
      viewW: v ? v.width : null,
      viewH: v ? v.height : null,
      hasInternalModel: !!(v && v.internalModel),
      pixiLive2dLoaded: !!(window.PIXI && window.PIXI.live2d && window.PIXI.live2d.Live2DModel),
    });
  })()`);
  log('[state] ' + state);
  const st = JSON.parse(state);

  // 3. 读回 moc3 参数（呼吸/眨眼/口型 实测）
  const readParams = async (label) => {
    return await evalJs(`(() => {
      const c = window.__character;
      const core = c && c.view && c.view.internalModel && c.view.internalModel.coreModel;
      if (!core) return JSON.stringify({error:'no coreModel'});
      const get = (id) => { try { return core.getParameterValueById(id); } catch(e){ return 'ERR:'+id; } };
      return JSON.stringify({
        t: Date.now(),
        breathY: c._breathPhase,
        ParamMouthOpenY: get('ParamMouthOpenY'),
        ParamEyeLOpen: get('ParamEyeLOpen'),
        ParamEyeROpen: get('ParamEyeROpen'),
        ParamAngleX: get('ParamAngleX'),
        ParamAngleY: get('ParamAngleY'),
        ParamBodyAngleX: get('ParamBodyAngleX'),
      });
    })()`);
  };

  log('[params@idle] ' + await readParams());

  // 4. 呼吸起伏：间隔读两次 breathPhase / 身体角
  await sleep(1200);
  log('[params@+1.2s] ' + await readParams());

  // 5. 口型驱动：setMouthOpen(0.9)
  await evalJs(`window.__character.setMouthOpen(0.9); 'ok'`);
  await sleep(150);
  log('[params@mouth=0.9] ' + await readParams());
  await evalJs(`window.__character.setMouthOpen(0.0); 'ok'`);
  await sleep(150);
  log('[params@mouth=0] ' + await readParams());

  // 6. 眼睛跟随：focus(0.8, 0.5)
  await evalJs(`window.__character.setLookTarget(0.8, 0.5); 'ok'`);
  await sleep(250);
  log('[params@look(0.8,0.5)] ' + await readParams());
  await evalJs(`window.__character.setLookTarget(-0.8, -0.5); 'ok'`);
  await sleep(250);
  log('[params@look(-0.8,-0.5)] ' + await readParams());
  await evalJs(`window.__character.setLookTarget(0,0); 'ok'`);

  // 7. 表情切换
  for (const ex of ['happy','surprised','sad','sleeping','idle']) {
    await evalJs(`window.__character.setExpression('${ex}'); 'ok'`);
    await sleep(200);
  }
  log('[expr] switched happy/surprised/sad/sleeping/idle OK');

  // 8. 截图（base64）
  const shot = await cdpCall(ws, mid++, 'Page.captureScreenshot', { format: 'png' });
  const b64 = shot.data;
  const fs = await import('node:fs');
  fs.writeFileSync(new URL('./live2d_shot_haru.png', import.meta.url), Buffer.from(b64, 'base64'));
  log('[shot] saved live2d_shot_haru.png bytes=' + Buffer.from(b64,'base64').length);

  ws.close();
  fs.writeFileSync(new URL('./live2d_cdp_result.json', import.meta.url), JSON.stringify({ state: st, logs: out }, null, 2));
  console.log('[done]');
}

main().catch(e => { console.error('[fatal]', e); process.exit(1); });
