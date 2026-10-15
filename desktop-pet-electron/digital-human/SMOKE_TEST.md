# 实时数字人可勾选模块 — 冒烟测试记录

> 测试时间：2026-10-11（东莞，Windows）
> **目录基准：`LoveAI-v2\desktop-pet-electron\digital-human\`**（与并行任务 A/B 的 driver.py / client.py 同目录）
> 铁律：只记真实输出；没跑的测试如实标「待驱动就绪后补测」，不造假。

---

## 0. 被测对象状态（测试时）

- `desktop-pet-electron/digital-human/local/driver.py`：**已就绪**（并行任务 A 交付，WS 契约 hello→status→frame→quit 实测通过）
- `desktop-pet-electron/digital-human/local/assets/master.png`：**已存在**（并行任务 A 已落母版）
- `desktop-pet-electron/digital-human/cloud/client.py`：**已存在**（并行任务 B 已落）
- `.venv-liveportrait`：**已存在**（torch CUDA 可用，GTX 1050，实测 fps≈0.9~1.2）
- 本机 GPU：NVIDIA GeForce GTX 1050 / 4096 MiB / 驱动 582.78
- 旧目录 `LoveAI-v2\digital-human\`：**已删除**（本次路径对齐时迁移后清空）

---

## 1. GPU 检测（gpu-detect.ps1）✅ 通过

**命令：**
```
powershell -NoProfile -ExecutionPolicy Bypass -File desktop-pet-electron\digital-human\gpu-detect.ps1
```

**真实输出（一行 JSON）：**
```
{"gpu":"NVIDIA GeForce GTX 1050","vram_mb":4096,"tier":"low","hint":"本地帧率低、建议云端"}
```

**结论：**
- 4096 MiB → tier=low ✅（规则：>=6144=high；0<vram<6144=low；无 N 卡=none）
- low 档 hint 逐字为「本地帧率低、建议云端」✅
- JSON 可正常解析 ✅

---

## 2. 路径对齐后重启冒烟（mode=local）✅ 通过

**步骤：**
1. config.json → `mode:"local"`，路径全部改为项目根相对
2. 启动 Electron：`.\node_modules\.bin\electron.cmd electron/main.js --remote-debugging-port=9225`
3. 等 12 秒读日志

**主进程 stdout 真实摘录：**
```
[ipc] 所有 IPC 处理器已注册
[ipc] 数字人 IPC 已注册
[dh] GPU 检测结果: {"gpu":"NVIDIA GeForce GTX 1050","vram_mb":4096,"tier":"low",...}
[dh] 启动，mode = local | gpu = NVIDIA GeForce GTX 1050 tier = low
```
> 注：hint 中文在控制台 stdout 因系统代码页显示为乱码，内存中 JSON 解析正确（见测试 1）。

**主进程 stderr 真实摘录（fail-closed）：**
```
[dh] 本地驱动未就绪，回落 2D。缺失项: [
  'driver.py: C:\\...\\LoveAI-v2\\desktop-pet-electron\\digital-human\\local\\driver.py'
]
```
**关键点：缺失路径已指向 `desktop-pet-electron\digital-human\local\driver.py`（新位置）；master.png 因已落盘不再出现在缺失列表——证明 resolveDhRel 基准（LOVEAI_ROOT）解析正确。**

**持久化：** 应用退出后 config.json 仍为 `mode:"local"` ✅（随后复位为 `off`）。

---

## 3. fail-closed 截图证据 ✅

| 文件 | 内容 |
|---|---|
| `out/dh_failclosed.png` | 首次冒烟（路径未对齐前）：2D Live2D 正常 + 角标「数字人: 驱动未就绪 → 2D」 |
| `out/dh_failclosed2.png` | 路径对齐后复跑：同上，2D 正常 + 角标提示 |

应用未崩、未卡死；退出后无本模块拉起的 python 孤儿进程。

---

## 4. e2e 真驱动画面测试 ✅ 通过（2026-10-11 补跑）

**结果：** 渲染端成功连上本地 LivePortrait 驱动 WS，收到 frame 并画到 `#dh-canvas`，画面是古风母版真人（粉色纱衣女子），角标显示「数字人: 就绪 0.9fps」。**不是 2D Live2D 立绘**。

**截图：** `out/dh_e2e.png`（CDP localhost:9225 截取）

**执行命令：**
```
# 1. 配置 mode=local
# 2. 启动
.\node_modules\.bin\electron.cmd electron/main.js --remote-debugging-port=9225
# 3. 等驱动加载模型 ~30s（日志: Source setup done / WS server listening on ws://127.0.0.1:9230）
# 4. 截图
node digital-human\cdp-shot.js digital-human\out\dh_e2e.png
```

**driver.log 关键行摘录：**
```
photo=...\digital-human\local\assets\master.png chroma=False selftest=False fps_max=0
Source setup done. Face cropped and keypoints extracted.
WS server listening on ws://127.0.0.1:9230
WS_PORT=9230
```
> onnxruntime 警告：`CUDAExecutionProvider` 不可用（回退 CPU），但主 LivePortrait 推理走 torch CUDA（fps≈0.9~1.2，与 selftest 实测一致）。

**过程中发现并修复的两个集成 bug（如实记录）：**
1. **渲染端只扫一次端口就放弃**：驱动加载模型 ~30s，页面先起来时驱动还没 listen，扫一次失败 → 永远不连。修复：`dh-view.js` 改为每 3s 重试扫 9230..9239，直到连上。
2. **浏览器 WS 握手超时 800ms 太短**：驱动每帧渲染 ~800ms（1.2fps），torch 占 GIL 饿死 asyncio 事件循环，握手响应排队 1~2s 才到。修复：连接超时从 800ms 放宽到 5000ms。修复后 `connected=true / active=true / port=9230`，首帧到达接管画面。

**孤儿进程检查：** 应用退出后 `Get-Process python` 无残留，9230 连接数=0（主进程 `before-quit` 用 `taskkill /PID /T /F` 整树清理）。

**结论：** 本地数字人 e2e 链路真实打通——Electron 主进程 spawn 驱动 → 渲染进程连 WS 收帧 → canvas 显示真人母版，fail-closed 回落路径之前已验。

---

## 5. amplitude 链路复用检查 ✅（代码走查）

- `renderer/js/audio-player.js`：`sample()` 内算 RMS 后调 `this._onAmp(this._amp)`
- `renderer/js/app.js`：`new AudioPlayer({ onAmp: (amp) => dhView.sendAmplitude(amp) })`
- `dh-view.js sendAmplitude()`：WS 开着就发 `{"type":"amplitude","value":0..1}`
- **没有第二套 TTS / 没有第二套音频分析**，同一 RMS 值既驱动 Live2D setMouthOpen 又转发数字人 ✅

---

## 6. 边界检查 ✅

| 项 | 状态 |
|---|---|
| 2D 默认（config mode=off） | ✅ 已复位 |
| characters.json 未触碰 | ✅ |
| services/boost_glide_planner.py 未触碰 | ✅ |
| services/wechat* 未触碰 | ✅ |
| 好感度/情感/任务逻辑未触碰 | ✅ |
| cloud 分支 fail-closed | ✅ 代码路径已实现，待 client.py 联调补 e2e |

---

## 7. 已知小问题（非阻塞）

1. GPU hint 中文在 Electron stdout 日志里因系统代码页显示乱码；内存 JSON 正确。
2. config.json 必须无 BOM（PS 5.1 `Set-Content -Encoding UTF8` 会写 BOM 导致 JSON.parse 失败）。本模块 `dh-manager.writeConfig()` 用 `fs.writeFileSync` 写无 BOM JSON，已规避。

---

## 8. 产物路径速查（全部新位置）

| 文件 | 绝对路径 |
|---|---|
| 数字人配置 | `<PROJECT_ROOT>\desktop-pet-electron\digital-human\config.json` |
| GPU 检测脚本 | `<PROJECT_ROOT>\desktop-pet-electron\digital-human\gpu-detect.ps1` |
| CDP 截图脚本 | `<PROJECT_ROOT>\desktop-pet-electron\digital-human\cdp-shot.js` |
| 本测试记录 | `<PROJECT_ROOT>\desktop-pet-electron\digital-human\SMOKE_TEST.md` |
| fail-closed 截图 | `...\desktop-pet-electron\digital-human\out\dh_failclosed.png` / `dh_failclosed2.png` |
| 应用日志 | `...\desktop-pet-electron\digital-human\out\app_test.log` / `app_test2.log` (+`.err`) |
| e2e 截图（待补） | `...\desktop-pet-electron\digital-human\out\dh_e2e.png` |
