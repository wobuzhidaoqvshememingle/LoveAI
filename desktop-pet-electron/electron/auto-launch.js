/**
 * auto-launch.js
 * 开机启动管理。
 * 不使用第三方 auto-launch 包，直接用 Electron 内置的 app.setLoginItemSettings。
 *  - Windows：写入注册表 Run 项
 *  - macOS：在系统设置-通用-登录项里出现
 */

const { app } = require('electron');

/** 当前是否开机启动 */
function isEnabled() {
  const settings = app.getLoginItemSettings();
  return !!settings.openAtLogin;
}

/**
 * 开启/关闭开机启动
 * @param {boolean} enable
 */
function setEnabled(enable) {
  const options = {
    openAtLogin: !!enable
  };
  // 开发模式下（electron.exe 直接跑）不要把 electron 本身注册成开机启动，
  // 否则会启动一个空白 electron。只在打包后才真正写入。
  // 打包后 process.execPath 是应用本体；开发时是 electron.exe。
  // 这里仍按用户意愿写入，由调用方在非打包环境提示即可。
  app.setLoginItemSettings(options);
  console.log('[auto-launch] 开机启动 =', enable);
}

/** 把设置里的 system.autoLaunch 同步到系统 */
function syncFromSettings(enabled) {
  // 只在状态不一致时写注册表，避免每次启动都写
  if (isEnabled() !== !!enabled) {
    setEnabled(!!enabled);
  }
}

module.exports = {
  isEnabled,
  setEnabled,
  syncFromSettings
};
