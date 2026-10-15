/**
 * dh-manager.js — 实时数字人模块（可勾选，默认 off=2D 桌宠）
 *
 * 职责（全部在主进程，渲染进程不可直接 spawn）：
 *  - 读/写 LoveAI-v2/digital-human/config.json（mode: off|local|cloud）
 *  - 启动时跑一次 gpu-detect.ps1，结果经 IPC 送渲染进程展示
 *  - mode=local：spawn 本地 LivePortrait 驱动进程并托管
 *      · 应用退出时 child.kill()，不允许孤儿进程
 *      · 驱动异常退出/未就绪 → 广播 dh:driverState=error/exited，渲染进程回落 2D
 *  - mode=cloud：校验豆包 Seedance 生成产物（cloud/client.py verify），fail 一律 fail-closed 回落 2D
 *
 * 冻结契约（与并行任务 driver.py / cloud/client.py 对齐）：
 *  - 驱动 CLI: <venv-liveportrait>/python.exe driver.py --photo <母版> [--port 9230] [--chroma]
 *  - 渲染进程直接连 ws://127.0.0.1:<port>（9230 起，占用则驱动退避 9231..9239）
 *  - 云端 CLI: <.venv>/python.exe cloud/client.py verify --video <产物mp4>
 *    读 stdout 末行 JSON（v2：豆包 Seedance 产物轨，无第三方付费 API）
 */

const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const { app } = require('electron');

// electron/ 位于 desktop-pet-electron/electron：
//   LOVEAI_ROOT = LoveAI-v2 根（venv、母版等以项目根为基准）
//   DH_DIR      = desktop-pet-electron/digital-human（config/ps1/out 都在这里，与并行任务 A/B 对齐）
const LOVEAI_ROOT = path.join(__dirname, '..', '..');
const DH_DIR = path.join(__dirname, '..', 'digital-human');
// 打包态下 DH_DIR 位于 app.asar 内——只读可用、写入必失败（ENOENT）。
// 因此「可写物」一律落用户数据目录，「只读脚本」走解包后的真实路径。
const DH_CONFIG_BUNDLED = path.join(DH_DIR, 'config.json'); // 出厂默认（包内，只读）
const GPU_PS1 = path.join(DH_DIR, 'gpu-detect.ps1');

function dhDataDir() {
  const d = path.join(app.getPath('userData'), 'digital-human');
  try { fs.mkdirSync(d, { recursive: true }); } catch (e) { /* 交由调用方兜底 */ }
  return d;
}

/** 用户可写配置路径（打包/源码态一致） */
function dhConfigPath() { return path.join(dhDataDir(), 'config.json'); }

/** 把 app.asar 内的路径映射到解包后的真实路径（spawn 外部进程必须用真实路径） */
function toRealPath(p) {
  if (!p) return p;
  return p.replace(/[\\/]app\.asar[\\/]/, '\\app.asar.unpacked\\').replace(/[\\/]app\.asar$/, '');
}

let mainWindow = null;
let driverChild = null;       // 本地驱动子进程
let driverLogStream = null;
let gpuInfo = null;           // 启动时检测结果 {gpu,vram_mb,tier,hint}
let driverState = 'idle';     // idle|starting|ready|error|exited

/* ==================== 路径解析 ==================== */

/** 把 config 里的相对路径解析成绝对路径（相对 LoveAI-v2 项目根） */
function resolveDhRel(p) {
  if (!p) return '';
  if (path.isAbsolute(p)) return p;
  return path.normalize(path.join(LOVEAI_ROOT, p));
}

/** 读取数字人配置：优先用户可写副本 → 包内出厂默认 → off
 *  注意：用户副本可能存在但 JSON 半损（上次写崩溃），此时必须回落包内默认，
 *  否则会静默变成 off，把用户已配好的 local/cloud 路径整份丢掉。 */
function readConfig() {
  const def = { mode: 'off' };
  const candidates = [dhConfigPath(), DH_CONFIG_BUNDLED];
  for (const p of candidates) {
    try {
      if (p && fs.existsSync(p)) {
        const cfg = JSON.parse(fs.readFileSync(p, 'utf-8'));
        if (cfg && typeof cfg === 'object') return Object.assign(def, cfg);
      }
    } catch (e) {
      console.warn('[dh] 配置读取失败，尝试下一个来源:', p, e.message);
    }
  }
  return def;
}

/** 写回数字人配置（仅 mode 等用户改的字段）——写用户可写副本，不写包内 */
function writeConfig(patch) {
  const cur = readConfig();
  const next = Object.assign(cur, patch);
  const target = dhConfigPath();
  try {
    fs.writeFileSync(target, JSON.stringify(next, null, 2), 'utf-8');
  } catch (e) {
    // 极端情况（用户目录不可写）兜底到临时目录，保证开关状态不丢
    const fallback = path.join(app.getPath('temp'), 'LoveAI-digital-human-config.json');
    fs.writeFileSync(fallback, JSON.stringify(next, null, 2), 'utf-8');
    console.error('[dh] 写用户配置失败(%s)，已兜底到临时目录: %s', e.message, fallback);
  }
  return next;
}

/* ==================== GPU 检测 ==================== */

/** 启动时跑一次 gpu-detect.ps1，解析 stdout 末行 JSON */
function detectGpu() {
  return new Promise((resolve) => {
    if (!fs.existsSync(GPU_PS1)) {
      gpuInfo = { gpu: 'none', vram_mb: 0, tier: 'none', hint: '未检测到NVIDIA显卡，建议使用云端数字人或保持2D' };
      resolve(gpuInfo);
      return;
    }
    const ps = spawn('powershell.exe',
      ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', toRealPath(GPU_PS1)],
      { windowsHide: true });
    let out = '';
    ps.stdout.on('data', (d) => { out += d.toString(); });
    ps.stderr.on('data', () => {});
    ps.on('error', () => {
      gpuInfo = { gpu: 'none', vram_mb: 0, tier: 'none', hint: '未检测到NVIDIA显卡，建议使用云端数字人或保持2D' };
      resolve(gpuInfo);
    });
    ps.on('close', () => {
      try {
        const lines = out.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
        const last = lines[lines.length - 1];
        gpuInfo = JSON.parse(last);
      } catch (e) {
        gpuInfo = { gpu: 'none', vram_mb: 0, tier: 'none', hint: '未检测到NVIDIA显卡，建议使用云端数字人或保持2D' };
      }
      console.log('[dh] GPU 检测结果:', JSON.stringify(gpuInfo));
      resolve(gpuInfo);
    });
  });
}

function getGpuInfo() { return gpuInfo; }

/* ==================== 本地驱动进程托管 ==================== */

/** 广播驱动状态给渲染进程 */
function broadcastDriverState(state, extra = {}) {
  driverState = state;
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('dh:driverState', Object.assign({ state }, extra));
  }
}

/** 启动本地驱动子进程（mode=local 且路径齐备时才调） */
function startLocalDriver() {
  const cfg = readConfig();
  if (cfg.mode !== 'local') return;

  const py = resolveDhRel(cfg.local && cfg.local.python);
  const driver = resolveDhRel(cfg.local && cfg.local.driver);
  const photo = resolveDhRel(cfg.local && cfg.local.photo);
  const port = (cfg.local && cfg.local.port) || 9230;

  // ---- fail-closed：任何前置缺失都不启动，直接回落 2D ----
  const missing = [];
  if (!fs.existsSync(py)) missing.push('python: ' + py);
  if (!fs.existsSync(driver)) missing.push('driver.py: ' + driver);
  if (!fs.existsSync(photo)) missing.push('master.png: ' + photo);
  if (missing.length) {
    console.warn('[dh] 本地驱动未就绪，回落 2D。缺失项:', missing);
    broadcastDriverState('error', { reason: '本地驱动未就绪', missing });
    return;
  }

  const args = [driver, '--photo', photo, '--port', String(port)];
  if (cfg.local && cfg.local.chroma) args.push('--chroma');

  console.log('[dh] spawn 本地驱动:', py, args.join(' '));
  broadcastDriverState('starting', { port });

  try {
    driverChild = spawn(py, args, {
      cwd: DH_DIR,
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
  } catch (e) {
    console.error('[dh] spawn 驱动失败:', e.message);
    broadcastDriverState('error', { reason: 'spawn 失败: ' + e.message });
    return;
  }

  // 日志落盘：打包态 DH_DIR 在 app.asar 内写不进，统一落用户数据目录
  try {
    const logPath = path.join(dhDataDir(), 'driver.log');
    driverLogStream = fs.createWriteStream(logPath, { flags: 'a' });
  } catch (e) { driverLogStream = null; }

  driverChild.stdout.on('data', (d) => {
    const s = d.toString();
    if (driverLogStream) driverLogStream.write('[out] ' + s);
    // 驱动就绪后通常会打印监听信息；这里不解析，渲染进程连 WS 后由 status 消息确认
  });
  driverChild.stderr.on('data', (d) => {
    const s = d.toString();
    if (driverLogStream) driverLogStream.write('[err] ' + s);
  });

  driverChild.on('error', (e) => {
    console.error('[dh] 驱动进程错误:', e.message);
    broadcastDriverState('error', { reason: e.message });
  });

  driverChild.on('exit', (code, signal) => {
    console.warn('[dh] 驱动进程退出 code=%s signal=%s（应用运行期 = 异常，回落 2D）', code, signal);
    driverChild = null;
    if (driverLogStream) { try { driverLogStream.end(); } catch (e) {} driverLogStream = null; }
    broadcastDriverState('exited', { code, signal });
  });
}

/** 杀掉驱动子进程（退出/切 off 时调），保证无孤儿进程 */
function stopLocalDriver() {
  if (driverChild) {
    try {
      // Windows 下 spawn 出来的 python 子进程，kill 父进程不一定带走子进程树；
      // 用 taskkill /T /F 整树结束，避免孤儿。
      spawn('taskkill.exe', ['/pid', String(driverChild.pid), '/T', '/F'], { windowsHide: true });
    } catch (e) { /* noop */ }
    try { driverChild.kill(); } catch (e) {}
    driverChild = null;
  }
  driverState = 'idle';
}

/* ==================== 云端产物校验 ==================== */

/**
 * cloud 模式说话时调用（v2 产物轨）：
 *  豆包 Seedance 已生成数字人视频产物（config.cloud.product_path，mp4）。
 *  本函数只做产物校验（spawn client.py verify），产物有效则返回 {ok, video_path} 供播放；
 *  产物缺失/损坏 → resolve({ok:false, reason})，渲染进程回落 2D + 气泡提示。
 *  audioUrl 参数保留仅为兼容 renderer 旧调用（dhCloudGenerate(audio_url)），v2 不再下载音频。
 */
async function cloudGenerate(audioUrl) {
  const cfg = readConfig();
  const py = resolveDhRel(cfg.cloud && cfg.cloud.python);
  const client = resolveDhRel(cfg.cloud && cfg.cloud.client);
  const product = resolveDhRel(cfg.cloud && cfg.cloud.product_path);
  const timeoutMs = ((cfg.cloud && cfg.cloud.timeout_sec) || 30) * 1000;

  if (!fs.existsSync(py)) return { ok: false, reason: 'cloud python 不存在: ' + py };
  if (!fs.existsSync(client)) return { ok: false, reason: 'cloud client.py 不存在' };
  if (!fs.existsSync(product)) return { ok: false, reason: '豆包数字人产物不存在（先用豆包生成并放到 product_path）: ' + product };

  // 1. spawn client.py verify，读 stdout 末行 JSON
  return new Promise((resolve) => {
    const args = ['cloud/client.py', 'verify', '--video', product];
    let child;
    try {
      child = spawn(py, args, { cwd: DH_DIR, windowsHide: true });
    } catch (e) {
      resolve({ ok: false, reason: 'spawn client 失败: ' + e.message });
      return;
    }
    let out = '';
    let done = false;
    const timer = setTimeout(() => {
      if (done) return;
      done = true;
      try { child.kill(); } catch (e) {}
      resolve({ ok: false, reason: 'cloud 产物校验超时' });
    }, timeoutMs);

    child.stdout.on('data', (d) => { out += d.toString(); });
    child.stderr.on('data', () => {});
    child.on('error', (e) => {
      if (done) return; done = true; clearTimeout(timer);
      resolve({ ok: false, reason: e.message });
    });
    child.on('close', () => {
      if (done) return; done = true; clearTimeout(timer);
      try {
        const lines = out.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
        const last = lines[lines.length - 1] || '{}';
        const j = JSON.parse(last);
        resolve(j && j.ok ? { ok: true, video_url: j.video_path } : { ok: false, reason: (j && j.reason) || 'cloud 返回 ok:false' });
      } catch (e) {
        resolve({ ok: false, reason: '解析 cloud 输出失败: ' + e.message });
      }
    });
  });
}

/* ==================== 生命周期 ==================== */

/** app ready 后调用：检测 GPU，按 config.mode 分支 */
async function init(window) {
  mainWindow = window;
  await detectGpu();
  const cfg = readConfig();
  console.log('[dh] 启动，mode =', cfg.mode, '| gpu =', gpuInfo.gpu, 'tier =', gpuInfo.tier);
  if (cfg.mode === 'local') {
    startLocalDriver();
  } else if (cfg.mode === 'cloud') {
    console.log('[dh] cloud 模式：不常驻进程，说话时按需 spawn client.py');
    broadcastDriverState('ready', { mode: 'cloud' });
  } else {
    console.log('[dh] off 模式：纯 2D 桌宠');
    broadcastDriverState('off');
  }
}

module.exports = {
  init,
  readConfig,
  writeConfig,
  getGpuInfo,
  startLocalDriver,
  stopLocalDriver,
  cloudGenerate,
  broadcastDriverState,
};
