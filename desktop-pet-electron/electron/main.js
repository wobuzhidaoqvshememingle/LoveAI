/**
 * main.js — Electron 主进程入口
 * 职责：
 *  - 创建透明、无边框、置顶的桌宠窗口（300x420）
 *  - 初始化设置存储、IPC、系统托盘、开机启动
 *  - 加载渲染进程 renderer/index.html
 *  - Windows 下关闭所有窗口不退出（仅托盘退出才退出）
 */

const { app, BrowserWindow, screen, ipcMain, session } = require('electron');
const path = require('path');
const settingsStore = require('./settings-store');
const ipc = require('./ipc');
const tray = require('./tray');
const autoLaunch = require('./auto-launch');
const dhManager = require('./dh-manager');

let mainWindow = null;

/** 创建主窗口 */
function createWindow() {
  const s = settingsStore.getAll();
  const { width, height } = s.window;

  // 计算初始位置：屏幕右下角（若上次保存过 x/y 则用上次的）
  let initX = s.window.x;
  let initY = s.window.y;
  if (initX == null || initY == null) {
    const display = screen.getPrimaryDisplay();
    const area = display.workAreaSize;
    initX = area.width - width - 40;   // 距右边 40px
    initY = area.height - height - 20; // 距底部 20px
  }

  mainWindow = new BrowserWindow({
    width,
    height,
    x: initX,
    y: initY,
    frame: false,            // 无边框
    transparent: true,       // 透明背景
    alwaysOnTop: s.window.alwaysOnTop !== false,
    resizable: false,        // 不可拉伸
    skipTaskbar: true,       // 不进任务栏
    hasShadow: false,         // 不要阴影（透明窗口阴影会变成黑块）
    backgroundColor: '#00000000', // Windows 透明窗口必须设置
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true, // 开启上下文隔离
      nodeIntegration: false,  // 关闭 node 集成
      sandbox: false            // preload 里用了 require，必须关 sandbox
    }
  });

  // 初始透明度
  mainWindow.setOpacity(typeof s.window.opacity === 'number' ? s.window.opacity : 1.0);

  // 加载渲染进程页面（本地 file:// 协议）
  // main.js 在 electron/ 下，renderer/ 在项目根目录，所以从 __dirname 往上一级
  const rendererPath = path.join(__dirname, '..', 'renderer', 'index.html');
  mainWindow.loadFile(rendererPath);

  // 开发模式自动打开 DevTools
  if (process.argv.includes('--dev')) {
    mainWindow.webContents.openDevTools({ mode: 'detach' });
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

/** App 就绪后初始化全部模块 */
app.whenReady().then(() => {
  // 0. 放行麦克风权限（真语音输入 getUserMedia 必需）
  session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => {
    if (permission === 'media' || permission === 'audioCapture') {
      callback(true);
    } else {
      callback(false);
    }
  });
  session.defaultSession.setPermissionCheckHandler((_wc, permission) => {
    return permission === 'media' || permission === 'audioCapture';
  });

  // 1. 设置存储
  settingsStore.init();

  // 2. 窗口
  createWindow();

  // 3. IPC
  ipc.init(mainWindow);

  // 4. 托盘
  tray.init(mainWindow);

  // 4.5 实时数字人模块（默认 off=2D；local 才常驻驱动进程）
  //     启动时跑 GPU 检测；mode=local 时 spawn 驱动并托管
  dhManager.init(mainWindow);

  // 5. 开机启动（同步设置里的开关到系统）
  autoLaunch.syncFromSettings(settingsStore.get('system.autoLaunch'));

  // 渲染进程同步查询版本号
  ipcMain.on('app:getVersion', (event) => {
    event.returnValue = app.getVersion();
  });

  // macOS：激活时若没有窗口则重建
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

// Windows / Linux：关闭所有窗口时不退出（托盘常驻）
// 只有 macOS 默认在所有窗口关闭后退出
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    // 不调用 app.quit()，保持后台托盘运行
  }
});

// 退出前把开机启动状态写回设置，并杀掉数字人本地驱动进程（无孤儿进程）
app.on('before-quit', () => {
  app.isQuitting = true;
  try { dhManager.stopLocalDriver(); } catch (e) { /* noop */ }
});
app.on('will-quit', () => {
  try { dhManager.stopLocalDriver(); } catch (e) { /* noop */ }
});
