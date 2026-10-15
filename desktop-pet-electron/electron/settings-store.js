/**
 * settings-store.js
 * 负责桌宠全部设置的持久化：
 *  - 启动时从 userData/settings.json 读取，并与默认值深度合并
 *  - 提供 getSettings / setSetting（支持点路径，如 "pet.scale"）
 *  - 设置变化时通知所有监听者（渲染进程、托盘菜单等）
 *
 * 设置文件路径：app.getPath('userData')/settings.json
 */

const { app } = require('electron');
const fs = require('fs');
const path = require('path');

// ====================== 默认设置 ======================
const DEFAULT_SETTINGS = {
  window: {
    width: 400,
    height: 600,
    opacity: 1.0,
    alwaysOnTop: true,
    x: null,
    y: null
  },
  pet: {
    scale: 0.15,
    currentModel: 'cybergf',
    volume: 0.8,
    clickReaction: true
  },
  ws: {
    url: 'ws://localhost:9876',
    enabled: true,
    reconnectInterval: 3000
  },
  behavior: {
    affection: 50,
    mood: 50,
    satiety: 50,
    idleInterval: 8000,
    randomTalkInterval: 30000
  },
  system: {
    autoLaunch: false,
    startMinimized: false
  }
};

let storePath = '';
let settings = null;
const listeners = []; // 设置变化回调列表

/** 深度合并：把 user 的值覆盖到 default 上（保留 default 里 user 没有的字段） */
function deepMerge(defaults, user) {
  if (Array.isArray(defaults)) {
    // 数组直接用 user 的值（或空数组）
    return Array.isArray(user) ? [...user] : [...defaults];
  }
  if (defaults && typeof defaults === 'object') {
    const out = {};
    for (const key of Object.keys(defaults)) {
      out[key] = deepMerge(defaults[key], user ? user[key] : undefined);
    }
    // 合并 user 里多出的字段（向前兼容）
    if (user) {
      for (const key of Object.keys(user)) {
        if (!(key in out)) out[key] = user[key];
      }
    }
    return out;
  }
  // 基本类型：user 有值就用 user，否则用 default
  return user !== undefined && user !== null ? user : defaults;
}

/** 根据点路径获取嵌套值，如 getByPath(obj, "pet.scale") */
function getByPath(obj, dotPath) {
  return dotPath.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

/** 根据点路径设置嵌套值，如 setByPath(obj, "pet.scale", 0.2) */
function setByPath(obj, dotPath, value) {
  const keys = dotPath.split('.');
  let cur = obj;
  for (let i = 0; i < keys.length - 1; i++) {
    if (cur[keys[i]] == null || typeof cur[keys[i]] !== 'object') cur[keys[i]] = {};
    cur = cur[keys[i]];
  }
  cur[keys[keys.length - 1]] = value;
}

/** 初始化：读取磁盘文件并合并默认值 */
function init() {
  storePath = path.join(app.getPath('userData'), 'settings.json');
  let userSettings = {};
  try {
    if (fs.existsSync(storePath)) {
      const raw = fs.readFileSync(storePath, 'utf-8');
      userSettings = JSON.parse(raw);
    }
  } catch (err) {
    console.error('[settings-store] 读取设置失败，使用默认值：', err);
    userSettings = {};
  }
  settings = deepMerge(DEFAULT_SETTINGS, userSettings);

  // R3.3-76：主程序共享配置（data/pet_shared.json，由 LOVEAI_PET_SHARED 注入）
  // 主程序 config.json 的 pet 段是单一事实源，启动时覆盖本地设置，实现双端一致。
  try {
    const sharedPath = process.env.LOVEAI_PET_SHARED || '';
    if (sharedPath && fs.existsSync(sharedPath)) {
      const shared = JSON.parse(fs.readFileSync(sharedPath, 'utf-8'));
      settings = deepMerge(settings, shared);
      console.log('[settings-store] 已加载主程序共享配置:', sharedPath);
    } else {
      console.log('[settings-store] 无共享配置文件，使用本地设置');
    }
  } catch (err) {
    console.error('[settings-store] 读取共享配置失败（回退本地）：', err);
  }

  console.log('[settings-store] 设置文件路径：', storePath);
}

/** 返回设置的深拷贝（防止外部直接改内部状态） */
function getAll() {
  return JSON.parse(JSON.stringify(settings));
}

/** 读取单个点路径的值 */
function get(dotPath) {
  if (!dotPath) return getAll();
  return getByPath(settings, dotPath);
}

/**
 * 设置某个值并持久化、通知监听者
 * @param {string} dotPath 点路径，如 "pet.scale"，或传顶层 key
 * @param {*} value
 */
function set(dotPath, value) {
  if (!settings) init();
  setByPath(settings, dotPath, value);
  persist();
  notify();
}

/** 把当前设置写入磁盘 */
function persist() {
  try {
    fs.writeFileSync(storePath, JSON.stringify(settings, null, 2), 'utf-8');
  } catch (err) {
    console.error('[settings-store] 写入设置失败：', err);
  }
}

/** 注册设置变化回调 */
function onChange(cb) {
  if (typeof cb === 'function') listeners.push(cb);
}

/** 通知所有监听者 */
function notify() {
  const snapshot = getAll();
  listeners.forEach((cb) => {
    try { cb(snapshot); } catch (e) { console.error(e); }
  });
}

module.exports = {
  init,
  getAll,
  get,
  set,
  onChange,
  persist
};
