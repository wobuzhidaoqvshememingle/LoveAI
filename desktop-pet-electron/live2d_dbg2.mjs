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
  // 派发 mousemove 重置 mouseLeaveTimer，再设目标，连续采样
  await ev(`window.dispatchEvent(new MouseEvent('mousemove')); 'mv'`);
  await ev(`window.__character.setLookTarget(0.9,0.3); 'go'`);
  const seq=[];
  for(let i=0;i<8;i++){
    seq.push(await ev(`(()=>{const fc=window.__character.view.internalModel.focusController;return {tx:+fc.targetX.toFixed(2),x:+fc.x.toFixed(2)};})()`));
    await sleep(120);
  }
  console.log('focus seq after mousemove+look(0.9,0.3):');
  for(const s of seq) console.log('  ', JSON.stringify(s));
  ws.close();
}
main().catch(e=>{console.error(e);process.exit(1);});
