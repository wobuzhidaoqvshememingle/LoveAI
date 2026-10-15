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
  // dump all parameter IDs and their values
  const params = await ev(`(()=>{
    const c=window.__character;
    const m=c.view.internalModel;
    const core=m.coreModel;
    const out={};
    try{ out.count=core.getParameterCount(); }catch(e){ out.countErr='' + e; }
    try{
      const ids=[];
      for(let i=0;i<core.getParameterCount();i++){ ids.push(core.getParameterId(i)); }
      out.ids=ids;
    }catch(e){ out.idsErr='' + e; }
    // focusController
    try{ out.focus={tx:m.focusController.targetX, ty:m.focusController.targetY, x:m.focusController.x, y:m.focusController.y}; }catch(e){ out.focusErr='' + e; }
    // eye blink params
    try{
      out.eyeL=core.getParameterValueById('ParamEyeLOpen');
      out.eyeR=core.getParameterValueById('ParamEyeROpen');
      out.angleX=core.getParameterValueById('ParamAngleX');
      out.eyeBallX=core.getParameterValueById('ParamEyeBallX');
      out.mouth=core.getParameterValueById('ParamMouthOpenY');
    }catch(e){ out.paramErr='' + e; }
    return JSON.stringify(out);
  })()`);
  console.log('[params]',params);
  // try setLookTarget and read after a frame
  const after = await ev(`(()=>{
    const c=window.__character;
    c.setLookTarget(0.9,0.3);
    return new Promise(r=>setTimeout(()=>{
      const m=c.view.internalModel;
      r(JSON.stringify({tx:m.focusController.targetX, ty:m.focusController.targetY, x:m.focusController.x, y:m.focusController.y, angleX:m.coreModel.getParameterValueById('ParamAngleX'), eyeBallX:m.coreModel.getParameterValueById('ParamEyeBallX')}));
    },1000));
  })()`);
  console.log('[after look]',after);
  ws.close();
}
main().catch(e=>{console.error(e);process.exit(1);});
