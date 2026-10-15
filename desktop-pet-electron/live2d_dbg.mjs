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
  // 诊断1：fc 是否存在，autoInteract 状态
  console.log(await ev(`(()=>{const c=window.__character;const im=c.view.internalModel;return JSON.stringify({hasFc:!!(im&&im.focusController),autoInteract:c.view.autoInteract,viewCtor:c.view.constructor.name});})()`));
  // 诊断2：同步调 fc.focus(0.9,0.3) 立刻读
  console.log(await ev(`(()=>{const c=window.__character;const fc=c.view.internalModel.focusController;fc.focus(0.9,0.3);return JSON.stringify({rightNow:{tx:fc.targetX,ty:fc.targetY,x:fc.x,y:fc.y}});})()`));
  // 诊断3：等 200ms 再读
  await sleep(200);
  console.log(await ev(`(()=>{const fc=window.__character.view.internalModel.focusController;return JSON.stringify({after200:{tx:fc.targetX,ty:fc.targetY,x:fc.x,y:fc.y}});})()`));
  // 诊断4：直接 setLookTarget 包装后立刻读
  console.log(await ev(`(()=>{const c=window.__character;c.setLookTarget(-0.8,-0.4);const fc=c.view.internalModel.focusController;return JSON.stringify({viaWrapper:{tx:fc.targetX,ty:fc.targetY,x:fc.x,y:fc.y}});})()`));
  ws.close();
}
main().catch(e=>{console.error(e);process.exit(1);});
