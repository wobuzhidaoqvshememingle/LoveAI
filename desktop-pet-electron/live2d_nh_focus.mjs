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
  // immediate read after focus() call
  const r1 = await ev(`(()=>{
    const c=window.__character;
    const fc=c.view.internalModel.focusController;
    const before={tx:fc.targetX, ty:fc.targetY, x:fc.x, y:fc.y};
    fc.focus(0.9, 0.3);
    const after={tx:fc.targetX, ty:fc.targetY, x:fc.x, y:fc.y};
    return JSON.stringify({before, after});
  })()`);
  console.log('[immediate]',r1);
  // after 1.5s of frames
  const r2 = await ev(`(()=>{
    return new Promise(r=>setTimeout(()=>{
      const c=window.__character;
      const fc=c.view.internalModel.focusController;
      r(JSON.stringify({tx:fc.targetX, ty:fc.targetY, x:fc.x, y:fc.y, angleX:c.view.internalModel.coreModel.getParameterValueById('ParamAngleX'), eyeBallX:c.view.internalModel.coreModel.getParameterValueById('ParamEyeBallX')}));
    },1500));
  })()`);
  console.log('[after 1.5s]',r2);
  ws.close();
}
main().catch(e=>{console.error(e);process.exit(1);});
