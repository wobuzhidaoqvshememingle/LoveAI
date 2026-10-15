/**
 * tray.js
 * 系统托盘：
 *  - 托盘图标（优先读取 assets/tray-icon.png，没有则用代码生成的 16x16 粉色圆形占位图）
 *  - 右键菜单：显示/隐藏宠物、切换角色、设置、开机启动、退出
 *  - 左键单击托盘图标：切换宠物显示/隐藏
 *  - 「切换角色」「设置」通过 webContents.send('tray:command', cmd) 通知渲染进程
 */

const { app, Tray, Menu, nativeImage, ipcMain } = require('electron');
const path = require('path');
const settingsStore = require('./settings-store');
const autoLaunch = require('./auto-launch');

let tray = null;
let mainWindow = null;

/** 生成一个 16x16 的粉色圆形占位图标（BGRA 原始像素） */
function generatePlaceholderIcon() {
  const size = 16;
  const buf = Buffer.alloc(size * size * 4); // 每行 size 像素，每像素 4 字节 BGRA
  const cx = size / 2 - 0.5;
  const cy = size / 2 - 0.5;
  const r = size / 2 - 0.5;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dx = x - cx;
      const dy = y - cy;
      const idx = (y * size + x) * 4;
      if (dx * dx + dy * dy <= r * r) {
        buf[idx] = 0xD8;     // B
        buf[idx + 1] = 0x7B; // G
        buf[idx + 2] = 0xFF; // R（粉色）
        buf[idx + 3] = 0xFF; // A
      }
    }
  }
  return nativeImage.createFromBitmap(buf, { width: size, height: size });
}

/** 加载托盘图标 */
function loadIcon() {
  const iconPath = path.join(app.getAppPath(), 'assets', 'tray-icon.png');
  try {
    if (require('fs').existsSync(iconPath)) {
      const img = nativeImage.createFromPath(iconPath);
      if (!img.isEmpty()) return img;
    }
  } catch (e) { /* 忽略，用占位图 */ }
  return generatePlaceholderIcon();
}

/** 通知渲染进程一条托盘命令 */
function sendTrayCommand(cmd) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('tray:command', cmd);
  }
}

/** 切换宠物窗口显示/隐藏 */
function toggleWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  if (mainWindow.isVisible()) {
    mainWindow.hide();
  } else {
    mainWindow.show();
    mainWindow.setSkipTaskbar(true); // 桌宠永远不进任务栏
  }
  refreshMenu();
}

/** 根据当前状态重建托盘菜单 */
function refreshMenu() {
  if (!tray) return;
  const visible = mainWindow && !mainWindow.isDestroyed() && mainWindow.isVisible();
  const autoOn = autoLaunch.isEnabled();

  const contextMenu = Menu.buildFromTemplate([
    {
      label: visible ? '隐藏宠物' : '显示宠物',
      click: () => toggleWindow()
    },
    {
      label: '切换角色',
      click: () => sendTrayCommand('next-character')
    },
    {
      label: '沉浸式模式',
      click: () => sendTrayCommand('toggle-immersive')
    },
    {
      label: '设置',
      click: () => sendTrayCommand('toggle-settings')
    },
    { type: 'separator' },
    {
      label: '开机启动',
      type: 'checkbox',
      checked: autoOn,
      click: (item) => {
        autoLaunch.setEnabled(item.checked);
        settingsStore.set('system.autoLaunch', item.checked);
      }
    },
    { type: 'separator' },
    {
      label: '退出',
      click: () => {
        app.isQuitting = true;
        app.quit();
      }
    }
  ]);
  tray.setContextMenu(contextMenu);
}

/** 初始化托盘 */
function init(window) {
  mainWindow = window;

  tray = new Tray(loadIcon());
  tray.setToolTip('LoveAI 桌宠');

  // 左键单击托盘图标：切换显示/隐藏
  tray.on('click', () => toggleWindow());

  refreshMenu();

  // 监听窗口显示/隐藏变化（例如渲染进程调用 hide），同步菜单文字
  if (mainWindow) {
    mainWindow.on('show', () => refreshMenu());
    mainWindow.on('hide', () => refreshMenu());
  }

  // 渲染进程请求刷新菜单（例如设置里改了开机启动）
  ipcMain.on('tray:refresh', () => refreshMenu());

  return tray;
}

module.exports = {
  init,
  refreshMenu,
  toggleWindow
};
