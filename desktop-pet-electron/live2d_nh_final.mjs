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
  // dispatch mousemove events to keep timer fresh, then setLookTarget
  const r = await ev(`(()=>{
    // fire mousemove to reset mouseLeaveTimer
    window.dispatchEvent(new MouseEvent('mousemove',{clientX:300,clientY:200}));
    const c=window.__character;
    c.setLookTarget(0.9, 0.3);
    return new Promise(r2=>setTimeout(()=>{
      const m=c.view.internalModel;
      window.dispatchEvent(new MouseEvent('mousemove',{clientX:300,clientY:200}));
      r2(JSON.stringify({
        tx:m.focusController.targetX, ty:m.focusController.targetY,
        x:m.focusController.x, y:m.focusController.y,
        angleX:m.coreModel.getParameterValueById('ParamAngleX'),
        eyeBallX:m.coreModel.getParameterValueById('ParamEyeBallX'),
        eyeL:m.coreModel.getParameterValueById('ParamEyeLOpen'),
        mouth:m.coreModel.getParameterValueById('ParamMouthOpenY')
      }));
    },600));
  })()`);
  console.log('[eye-follow test]', r);
  // mouth
  const m = await ev(`(()=>{const c=window.__character;c.setMouthOpen(0.9);const v=c.view.internalModel.coreModel.getParameterValueById('ParamMouthOpenY');c.setMouthOpen(0);return v;})()`);
  console.log('[mouth]', m);
  ws.close();
}
main().catch(e=>{console.error(e);process.exit(1);});
