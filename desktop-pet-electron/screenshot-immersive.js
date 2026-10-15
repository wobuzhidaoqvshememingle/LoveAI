/**
 * screenshot-immersive.js — 沉浸式模式截图
 */
const { app, BrowserWindow } = require('electron');
const path = require('path');
const fs = require('fs');

app.disableHardwareAcceleration();

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    frame: false,
    transparent: false,
    backgroundColor: '#0a0e1a',
    webPreferences: {
      preload: path.join(__dirname, 'electron', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
    show: false,
  });

  await win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  await new Promise(r => setTimeout(r, 3500));

  // 直接驱动 PIXI 适配到全屏 1280x800，模型偏右
  await win.webContents.executeJavaScript(`
    document.body.classList.add('immersive-mode');
    document.body.classList.remove('corner-mode');
    document.getElementById('voice-ball').classList.remove('hidden');
    document.getElementById('subtitle-bar').classList.remove('hidden');
    document.getElementById('exit-immersive').classList.remove('hidden');
    document.getElementById('immersive-status').classList.remove('hidden');
    const w = window.innerWidth, h = window.innerHeight;
    window.__app.renderer.resize(w, h);
    document.getElementById('stage').style.width = w + 'px';
    document.getElementById('stage').style.height = h + 'px';
    window.__character.setViewport(w, h, { alignX: 'right', bottomMargin: 0 });
    'ok';
  `);

  await new Promise(r => setTimeout(r, 1500));

  const image = await win.webContents.capturePage();
  const outPath = path.join(__dirname, 'screenshot-immersive.png');
  fs.writeFileSync(outPath, image.toPNG());
  console.log('immersive screenshot saved:', outPath);

  app.quit();
});
