// 临时把模型居中放大，截图确认是否真渲染
const BASE='http://127.0.0.1:9225';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function getPageTarget(){for(let i=0;i<15;i++){try{const r=await fetch(BASE+'/json');const l=await r.json();const p=l.find(t=>t.type==='page'&&t.url&&t.url.includes('index.html'));if(p)return p;}catch(e){}await sleep(800);}throw new Error('no page');}
function cdpCall(ws,id,method,params){return new Promise((res,rej)=>{const on=(ev)=>{const m=JSON.parse(ev.data);if(m.id===id){ws.removeEventListener('message',on);m.error?rej(new Error(JSON.stringify(m.error))):res(m.result);}};ws.addEventListener('message',on);ws.send(JSON.stringify({id,method,params:params||{}}));});}
async function main(){
  const page=await getPageTarget();
  const ws=new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res,rej)=>{ws.onopen=res;ws.onerror=rej;});
  let mid=1;
  const ev=async(e)=>{const r=await cdpCall(ws,mid++,'Runtime.evaluate',{expression:e,returnByValue:true});if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value;};
  // 读窗口/画布尺寸
  console.log('viewport:', await ev(`JSON.stringify({iw:window.innerWidth,ih:window.innerHeight,dpr:window.devicePixelRatio,canvas:document.querySelector('#stage canvas')?{w:document.querySelector('#stage canvas').width,h:document.querySelector('#stage canvas').height}:null})`));
  // 把模型移到画面正中央，放大1.5倍
  await ev(`(()=>{const c=window.__character;c.view.anchor.set(0.5,0.5);c.view.position.set(200,300);c.view.scale.set(0.18);return 'moved';})()`);
  await sleep(500);
  const shot=await cdpCall(ws,mid++,'Page.captureScreenshot',{format:'png'});
  const fs=await import('node:fs');
  fs.writeFileSync(new URL('./live2d_shot_centered.png',import.meta.url),Buffer.from(shot.data,'base64'));
  console.log('saved live2d_shot_centered.png',Buffer.from(shot.data,'base64').length,'bytes');
  ws.close();
}
main().catch(e=>{console.error(e);process.exit(1);});
