// 抓渲染进程 console / 异常 / 网络错误，reload 后收集
const BASE = 'http://127.0.0.1:9225';
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
async function getPageTarget() {
  for (let i=0;i<15;i++){ try { const r=await fetch(BASE+'/json'); const l=await r.json(); const p=l.find(t=>t.type==='page'&&t.url&&t.url.includes('index.html')); if(p) return p; } catch(e){} await sleep(800);}
  throw new Error('no page');
}
function cdpCall(ws,id,method,params){return new Promise((res,rej)=>{const on=(ev)=>{const m=JSON.parse(ev.data);if(m.id===id){ws.removeEventListener('message',on);m.error?rej(new Error(JSON.stringify(m.error))):res(m.result);}};ws.addEventListener('message',on);ws.send(JSON.stringify({id,method,params:params||{}}));});}
async function main(){
  const page=await getPageTarget();
  const ws=new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res,rej)=>{ws.onopen=res;ws.onerror=rej;});
  let mid=1;
  const logs=[];
  ws.addEventListener('message',(ev)=>{
    const m=JSON.parse(ev.data);
    if(m.method==='Runtime.consoleAPICalled'){
      const args=(m.params.args||[]).map(a=>a.value!==undefined?JSON.stringify(a.value):(a.description||'')).join(' ');
      logs.push('[console.'+m.params.type+'] '+args);
    } else if(m.method==='Runtime.exceptionThrown'){
      const e=m.params.exceptionDetails;
      logs.push('[exception] '+(e.exception&&e.exception.description||e.text));
    } else if(m.method==='Log.entryAdded'){
      logs.push('[log.'+m.params.entry.level+'] '+m.params.entry.text+' url='+(m.params.entry.url||''));
    }
  });
  await cdpCall(ws,mid++,'Runtime.enable');
  try{ await cdpCall(ws,mid++,'Log.enable'); }catch(e){}
  await cdpCall(ws,mid++,'Network.enable');
  // 失败请求
  ws.addEventListener('message',(ev)=>{
    const m=JSON.parse(ev.data);
    if(m.method==='Network.loadingFailed'||m.method==='Network.responseReceived'){
      if(m.method==='Network.loadingFailed') logs.push('[net.fail] reqId='+m.params.requestId+' err='+m.params.errorText);
    }
  });
  // reload
  await cdpCall(ws,mid++,'Page.enable');
  await cdpCall(ws,mid++,'Page.reload',{ignoreCache:true});
  await sleep(6000);
  // 查模型实际可见性
  const r=await cdpCall(ws,mid++,'Runtime.evaluate',{expression:`(()=>{
    const c=window.__character;
    if(!c) return 'no __character';
    const v=c.view;
    const im=v.internalModel;
    return JSON.stringify({
      isLive2D:c._isLive2D,
      viewVisible:v.visible,
      viewAlpha:v.alpha,
      viewX:v.x, viewY:v.y, viewScaleX:v.scale? v.scale.x:null,
      viewWidth:v.width, viewHeight:v.height,
      stageChildren:window.__app.stage.children.map(ch=>({ctor:ch.constructor.name,visible:ch.visible,x:ch.x,y:ch.y,w:ch.width,h:ch.height})),
      textures: im && im.coreModel ? 'coreModel ok' : 'no core',
    });
  })()`,returnByValue:true});
  logs.push('[state] '+JSON.stringify(r.result.value));
  const fs=await import('node:fs');
  fs.writeFileSync(new URL('./live2d_console.txt',import.meta.url), logs.join('\n'));
  console.log(logs.join('\n'));
  ws.close();
}
main().catch(e=>{console.error(e);process.exit(1);});
