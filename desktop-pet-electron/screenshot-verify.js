/**
 * screenshot-verify.js — 验证深色气泡 + 开心脉冲视觉
 * 离屏加载 → 触发开心动作+气泡 → 截两张（脉冲峰值/回落）
 */
const { app, BrowserWindow } = require('electron');
const path = require('path');
const fs = require('fs');

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 400,
    height: 600,
    x: 80,                  // 屏幕内：真实渲染，截图后自动关闭
    y: 80,
    frame: false,
    transparent: false,
    backgroundColor: '#e8e8ee',   // 浅色底色，模拟浅色桌面壁纸
    resizable: false,
    show: true,
    webPreferences: {
      preload: path.join(__dirname, 'electron', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  await win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  await new Promise(r => setTimeout(r, 5000));

  // 显示气泡后，等两帧立即截图，截图前最后一刻确认状态
  const dbg = await win.webContents.executeJavaScript(`
    const bubble = document.querySelector('#bubble');
    bubble.classList.remove('hidden', 'fading');
    void bubble.offsetWidth;
    bubble.querySelector('.bubble-text').textContent = '今天工作累不累呀？';
    'shown';
  `);

  await new Promise(r => setTimeout(r, 400));   // 等入场动画播完
  const preShot = await win.webContents.executeJavaScript(`
    const hit = document.elementFromPoint(200, 48);
    const b = document.querySelector('#bubble');
    JSON.stringify({
      hit: hit ? (hit.id || hit.className || hit.tagName) : 'none',
      opacity: getComputedStyle(b).opacity,
      z: getComputedStyle(b).zIndex
    });
  `);
  console.log('pre-shot state:', preShot);

  let img = await win.webContents.capturePage();
  fs.writeFileSync(path.join(__dirname, 'verify-tap.png'), img.toPNG());
  console.log('verify-tap.png saved');

  app.quit();
});
