// reload 恢复默认布局，截图 + 读 focusController 状态验证眼跟随
const BASE='http://127.0.0.1:9225';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function getPageTarget(){for(let i=0;i<15;i++){try{const r=await fetch(BASE+'/json');const l=await r.json();const p=l.find(t=>t.type==='page'&&t.url&&t.url.includes('index.html'));if(p)return p;}catch(e){}await sleep(800);}throw new Error('no page');}
function cdpCall(ws,id,method,params){return new Promise((res,rej)=>{const on=(ev)=>{const m=JSON.parse(ev.data);if(m.id===id){ws.removeEventListener('message',on);m.error?rej(new Error(JSON.stringify(m.error))):res(m.result);}};ws.addEventListener('message',on);ws.send(JSON.stringify({id,method,params:params||{}}));});}
async function main(){
  const page=await getPageTarget();
  const ws=new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res,rej)=>{ws.onopen=res;ws.onerror=rej;});
  let mid=1;
  const ev=async(e)=>{const r=await cdpCall(ws,mid++,'Runtime.evaluate',{expression:e,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails.exception&&r.exceptionDetails.exception.description||r.exceptionDetails.text));return r.result.value;};
  await cdpCall(ws,mid++,'Page.enable');
  await cdpCall(ws,mid++,'Page.reload',{ignoreCache:true});
  await sleep(5000);
  // 默认布局截图
  let shot=await cdpCall(ws,mid++,'Page.captureScreenshot',{format:'png'});
  const fs=await import('node:fs');
  fs.writeFileSync(new URL('./live2d_shot_default.png',import.meta.url),Buffer.from(shot.data,'base64'));
  console.log('default shot saved');
  // 验证 focusController 眼跟随
  const focusTest=await ev(`(async()=>{
    const c=window.__character;
    c.setLookTarget(0.9,0.3);
    await new Promise(r=>setTimeout(r,300));
    const fc=c.view.internalModel.focusController;
    const r1={tx:fc.targetX,ty:fc.targetY,x:fc.x,y:fc.y};
    c.setLookTarget(-0.9,-0.3);
    await new Promise(r=>setTimeout(r,300));
    const r2={tx:fc.targetX,ty:fc.targetY,x:fc.x,y:fc.y};
    c.setLookTarget(0,0);
    return JSON.stringify({afterRight:r1,afterLeft:r2});
  })()`);
  console.log('focusController:',focusTest);
  // 口型：进 SPEAKING 状态，连续采样口型
  const mouthSeq=[];
  await ev(`window.__character && (window.__character.setMouthOpen && window.__character.setMouthOpen(0.6)); 'x'`);
  for(let i=0;i<6;i++){
    const v=await ev(`(()=>{const c=window.__character;const core=c.view.internalModel.coreModel;return core.getParameterValueById('ParamMouthOpenY');})()`);
    mouthSeq.push(v);
    await sleep(60);
  }
  console.log('mouth samples:',JSON.stringify(mouthSeq));
  // 最终截图
  shot=await cdpCall(ws,mid++,'Page.captureScreenshot',{format:'png'});
  fs.writeFileSync(new URL('./live2d_shot_final.png',import.meta.url),Buffer.from(shot.data,'base64'));
  console.log('final shot saved');
  ws.close();
}
main().catch(e=>{console.error(e);process.exit(1);});
