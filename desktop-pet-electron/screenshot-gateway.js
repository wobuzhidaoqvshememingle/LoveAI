/**
 * screenshot-gateway.js — 截网关仪表盘真实画面
 * 用法：node screenshot-gateway.js（需网关已在 :8787 运行）
 */
const { app, BrowserWindow } = require('electron');
const path = require('path');
const fs = require('fs');

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1200,
    height: 800,
    x: 80,
    y: 80,
    resizable: true,
    show: true,
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });

  await win.loadURL('http://127.0.0.1:8787/');
  await new Promise(r => setTimeout(r, 3500));

  const image = await win.webContents.capturePage();
  const out = path.join(__dirname, '..', 'docs', 'screenshots', 'gateway-dashboard.png');
  fs.writeFileSync(out, image.toPNG());
  console.log('gateway dashboard saved:', out);

  app.quit();
});
