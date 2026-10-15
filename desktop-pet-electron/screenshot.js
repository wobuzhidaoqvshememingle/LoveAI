/**
 * screenshot.js — 一次性截图脚本
 * 用法：node screenshot.js
 * 启动一个离屏 BrowserWindow，加载 renderer/index.html，
 * 等模型加载完成后截一张 PNG 到项目根目录 screenshot.png 然后退出。
 */
const { app, BrowserWindow } = require('electron');
const path = require('path');
const fs = require('fs');

app.disableHardwareAcceleration();

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 400,
    height: 600,
    frame: false,
    transparent: false,           // 截图用不透明背景，能看清模型
    backgroundColor: '#1a1030',   // 深紫底色，衬托赛博女友
    resizable: false,
    webPreferences: {
      preload: path.join(__dirname, 'electron', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
    show: false,
  });

  // 注入可见背景，再加载页面
  await win.loadFile(path.join(__dirname, 'renderer', 'index.html'));

  // 等 5 秒让 Live2D 模型加载、首帧渲染
  await new Promise(r => setTimeout(r, 5000));

  // 截整页
  const image = await win.webContents.capturePage();
  const outPath = path.join(__dirname, 'screenshot.png');
  fs.writeFileSync(outPath, image.toPNG());
  console.log('screenshot saved:', outPath);

  app.quit();
});
