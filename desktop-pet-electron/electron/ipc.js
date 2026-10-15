/**
 * ipc.js
 * 主进程与渲染进程之间的 IPC 桥接：
 *  - 设置读写：settings:get / settings:set
 *  - 窗口拖拽：window:drag（渲染进程按住角色后持续发送相对位移 dx,dy）
 *  - 窗口控制：window:setPosition / window:setAlwaysOnTop / window:setOpacity
 *  - 设置变化广播：settings:changed（主进程 → 渲染进程）
 */

const { ipcMain, screen } = require('electron');
const settingsStore = require('./settings-store');
const dhManager = require('./dh-manager');

let mainWindow = null;

/** 把当前设置推给渲染进程 */
function broadcastSettings() {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('settings:changed', settingsStore.getAll());
  }
}

/** 初始化所有 IPC 处理器 */
function init(window) {
  mainWindow = window;

  // ---------- 设置 ----------
  ipcMain.handle('settings:get', () => {
    return settingsStore.getAll();
  });

  ipcMain.handle('settings:set', (_event, key, value) => {
    settingsStore.set(key, value);
    return settingsStore.getAll();
  });

  // 设置变化时（包括托盘/开机启动等其它途径改的）广播给渲染进程
  settingsStore.onChange(() => {
    broadcastSettings();
  });

  // ---------- 窗口拖拽 ----------
  // 渲染进程按住角色后，按帧发送相对位移 {dx, dy}
  ipcMain.on('window:drag', (_event, dx, dy) => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    const [x, y] = mainWindow.getPosition();
    const nextX = Math.round(x + dx);
    const nextY = Math.round(y + dy);
    mainWindow.setPosition(nextX, nextY);
    // 持久化位置（可选，下次启动沿用）
    settingsStore.set('window.x', nextX);
    settingsStore.set('window.y', nextY);
  });

  // ---------- 窗口位置 ----------
  ipcMain.on('window:setPosition', (_event, x, y) => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    mainWindow.setPosition(Math.round(x), Math.round(y));
  });

  // R3.3-76：取当前窗口左上角屏幕坐标（拖动结束后上行共享配置用）
  ipcMain.handle('window:getPosition', () => {
    if (!mainWindow || mainWindow.isDestroyed()) return [0, 0];
    return mainWindow.getPosition();
  });

  // ---------- 置顶 ----------
  ipcMain.on('window:setAlwaysOnTop', (_event, on) => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    mainWindow.setAlwaysOnTop(!!on);
    settingsStore.set('window.alwaysOnTop', !!on);
  });

  // ---------- 透明度 ----------
  ipcMain.on('window:setOpacity', (_event, opacity) => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    let op = Number(opacity);
    if (Number.isNaN(op)) op = 1.0;
    op = Math.min(1.0, Math.max(0.1, op));
    mainWindow.setOpacity(op);
    settingsStore.set('window.opacity', op);
  });

  // ---------- 沉浸式模式 ----------
  // 切换全屏暗色沉浸式界面 vs 角落透明小窗
  ipcMain.on('window:setImmersive', (_event, on) => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    if (on) {
      // 沉浸式：全屏、不透明暗色、不置顶
      const display = screen.getPrimaryDisplay();
      const size = display.workAreaSize;
      mainWindow.setResizable(true);
      mainWindow.setSize(size.width, size.height);
      mainWindow.setPosition(0, 0);
      mainWindow.setAlwaysOnTop(false);
      mainWindow.setBackgroundColor('#0a0a12');
      mainWindow.setOpacity(1.0);
      mainWindow.setSkipTaskbar(false);
      mainWindow.show();
    } else {
      // 恢复角落小窗（与 settings-store 默认保持一致）
      const w = 400, h = 600;
      const display = screen.getPrimaryDisplay();
      const area = display.workAreaSize;
      mainWindow.setResizable(false);
      mainWindow.setSize(w, h);
      mainWindow.setPosition(area.width - w - 40, area.height - h - 20);
      mainWindow.setAlwaysOnTop(true);
      mainWindow.setBackgroundColor('#00000000');
      mainWindow.setOpacity(1.0);
      mainWindow.setSkipTaskbar(true);
    }
    settingsStore.set('window.immersive', !!on);
    mainWindow.webContents.send('immersive:changed', !!on);
  });

  console.log('[ipc] 所有 IPC 处理器已注册');

  // ---------- 实时数字人（可勾选模块，默认 off=2D） ----------
  // 读 digital-human/config.json
  ipcMain.handle('dh:getConfig', () => dhManager.readConfig());

  // 写模式（mode: off|local|cloud）。契约要求：必须重启生效。
  ipcMain.handle('dh:setConfig', (_event, patch) => {
    const next = dhManager.writeConfig(patch || {});
    return { config: next, restartRequired: true };
  });

  // 取启动时的 GPU 检测结果
  ipcMain.handle('dh:getGPU', () => dhManager.getGpuInfo());

  // cloud 模式说话：主进程 spawn client.py，读末行 JSON；失败 fail-closed
  ipcMain.handle('dh:cloudGenerate', async (_event, audioUrl) => {
    try {
      return await dhManager.cloudGenerate(audioUrl);
    } catch (e) {
      return { ok: false, reason: e.message };
    }
  });

  console.log('[ipc] 数字人 IPC 已注册');
}

module.exports = {
  init,
  broadcastSettings
};
