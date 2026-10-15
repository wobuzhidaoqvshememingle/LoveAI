/**
 * preload.js
 * 在渲染进程加载前执行，通过 contextBridge 暴露安全的 API 给 window.petAPI。
 * 渲染进程只能通过这里暴露的接口与主进程通信（contextIsolation 开启）。
 */

const { contextBridge, ipcRenderer } = require('electron');

// 开发模式判断：preload 中无法访问 app.isPackaged，用命令行参数 --dev 判断
const isDev = process.argv.includes('--dev');

contextBridge.exposeInMainWorld('petAPI', {
  // ==================== 设置 ====================
  /** 获取完整设置对象（Promise<object>） */
  getSettings: () => ipcRenderer.invoke('settings:get'),

  /**
   * 设置某个值
   * @param {string} key 点路径，如 "pet.scale" 或顶层 key
   * @param {*} value
   */
  setSetting: (key, value) => ipcRenderer.invoke('settings:set', key, value),

  /** 监听设置变化：(settings) => void */
  onSettingsChanged: (callback) => {
    if (typeof callback !== 'function') return;
    ipcRenderer.on('settings:changed', (_event, settings) => callback(settings));
  },

  // ==================== 窗口控制 ====================
  /** 拖拽窗口：传相对位移 dx, dy */
  dragWindow: (dx, dy) => ipcRenderer.send('window:drag', dx, dy),

  /** 直接设置窗口左上角位置（屏幕坐标） */
  setWindowPosition: (x, y) => ipcRenderer.send('window:setPosition', x, y),

  /** 读取窗口左上角当前屏幕坐标（Promise<[x,y]>） */
  getWindowPosition: () => ipcRenderer.invoke('window:getPosition'),

  /** 设置/取消置顶 */
  setAlwaysOnTop: (on) => ipcRenderer.send('window:setAlwaysOnTop', on),

  /** 设置窗口整体透明度 0.1 ~ 1.0 */
  setOpacity: (opacity) => ipcRenderer.send('window:setOpacity', opacity),

  /** 切换沉浸式模式（全屏暗色 vs 角落小窗） */
  setImmersive: (on) => ipcRenderer.send('window:setImmersive', on),

  /** 监听沉浸式模式切换 */
  onImmersiveChanged: (callback) => {
    if (typeof callback !== 'function') return;
    ipcRenderer.on('immersive:changed', (_event, on) => callback(on));
  },

  // ==================== 托盘命令 ====================
  /**
   * 监听托盘菜单发来的命令
   * @param {(cmd: 'toggle-settings' | 'next-character' | 'quit') => void} callback
   */
  onTrayCommand: (callback) => {
    if (typeof callback !== 'function') return;
    ipcRenderer.on('tray:command', (_event, cmd) => callback(cmd));
  },

  // ==================== 应用信息 ====================
  getVersion: () => {
    try { return require('electron').ipcRenderer.sendSync('app:getVersion'); }
    catch (e) { return '0.0.0'; }
  },
  platform: process.platform,

  // ==================== 环境 ====================
  isDev: isDev,

  // ==================== 实时数字人（可勾选模块） ====================
  /** 读 digital-human/config.json（{mode: off|local|cloud, ...}） */
  dhGetConfig: () => ipcRenderer.invoke('dh:getConfig'),

  /** 写模式（off|local|cloud），返回 {config, restartRequired:true} */
  dhSetConfig: (patch) => ipcRenderer.invoke('dh:setConfig', patch),

  /** 取启动时 GPU 检测结果 {gpu,vram_mb,tier,hint} */
  dhGetGPU: () => ipcRenderer.invoke('dh:getGPU'),

  /** cloud 模式说话：主进程 spawn client.py，返回 {ok, ...} */
  dhCloudGenerate: (audioUrl) => ipcRenderer.invoke('dh:cloudGenerate', audioUrl),

  /** 监听本地驱动状态：{state: off|starting|ready|error|exited, ...} */
  onDhDriverState: (callback) => {
    if (typeof callback !== 'function') return;
    ipcRenderer.on('dh:driverState', (_event, info) => callback(info));
  }
});
