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
  await sleep(5500);
  const st=await ev(`(()=>{const c=window.__character;if(!c)return 'no char';const v=c.view;const im=v.internalModel;return JSON.stringify({isLive2D:c._isLive2D,model:c._modelConfig&&c._modelConfig.id,path:c._modelConfig&&(c._modelConfig.modelPath||c._modelConfig.imagePath),natW:c._natW,natH:c._natH,scale:c._baseScale,w:v.width,h:v.height,pos:[+v.x.toFixed(1),+v.y.toFixed(1)],alpha:v.alpha});})()`);
  console.log('[state]',st);
  // 口型/眼跟随同帧验证
  console.log('[mouth]', await ev(`(()=>{const c=window.__character;c.setMouthOpen(0.8);const v=c.view.internalModel.coreModel.getParameterValueById('ParamMouthOpenY');c.setMouthOpen(0);return v;})()`));
  const shot=await cdpCall(ws,mid++,'Page.captureScreenshot',{format:'png'});
  const fs=await import('node:fs');
  fs.writeFileSync(new URL('./live2d_shot_hiyori.png',import.meta.url),Buffer.from(shot.data,'base64'));
  console.log('shot saved live2d_shot_hiyori.png');
  ws.close();
}
main().catch(e=>{console.error(e);process.exit(1);});
