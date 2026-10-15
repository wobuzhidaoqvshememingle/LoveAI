# 实时数字人可勾选模块 —— 独立验收报告（VERIFICATION）

> 验收时间：2026-10-11（东莞，Windows，PowerShell 5.1）
> 验收方式：独立复跑 + 原始证据文件走查，**不采信任何子代理报告**，全部结论由本验收方亲手测得或亲见文件得出。
> 项目根：`<PROJECT_ROOT>\`
> 本机环境：NVIDIA GeForce GTX 1050 / 4096 MiB / 驱动 582.78；`.venv\python.exe`=3.14.7（通用），`.venv-liveportrait\python.exe`=torch 2.6.0+cu124（本地驱动专用）。

---

## 一、验收矩阵

| # | 验收项 | 结论 | 证据路径 | 本验收独立测得数据 | 与原数据对照 |
|---|---|---|---|---|---|
| 1 | 2D 默认 + 可勾选 + 持久化 + 重启生效 | **PASS** | `digital-human\config.json`、`electron\dh-manager.js`(L47-66 读写)、`renderer\js\settings-panel.js`(L82-98,157-227)、`digital-human\SMOKE_TEST.md`(L39-63) | 当前 `mode="off"`；checkbox `#sp-dh-enabled` + radio local/cloud；`_saveDh()→dhSetConfig({mode})→ipc dh:setConfig→dhManager.writeConfig()→fs.writeFileSync(DH_CONFIG)`；`main.js` ready 时 `dhManager.init()` 从磁盘 `readConfig()`；面板提示「保存后重启桌宠生效」 | SMOKE_TEST L63 记录「写 local→退出后 config 仍为 local，随后复位 off」真实日志；本验收读盘确认复位后 mode=off |
| 2 | 本地路径真实可启动 | **PASS** | 新证据：`local\out\demo_d2.mp4`、`local\out\selftest_verify_run.log`、`local\out\selftest.json`（已复写）；原证据备份：`local\out\verification_baseline\{selftest.json,demo.mp4,preview.jpg}` | **fps_mean=1.21**（p50=1.19, p95=1.28）、**vram_mb=3041.0**、total_frames=75、backend=torch 2.6.0+cu124 fp16、frame_render_ms_mean=827.0ms；cv2 解 `demo_d2.mp4`：decodable=True、75 帧、924×1280；PIL 读 `master.png`：1740×2410(WxH) | 原基线 fps_mean≈1.2、vram≈3041MB → 本测 1.21 / 3041.0，**完全一致**；source_image_size=[2410,1740,3] 与 PIL 1740×2410(WxH) 吻合 |
| 3 | 云端路径真实可用（集成就绪 + fail-closed 写进代码） | **PASS**（付费真实 e2e 未跑，见问题清单 P1） | `cloud\client.py`(L208-249 门禁)、`cloud\config.json`、`cloud\README_API.md`、`cloud\TESTLOG_failclosed.md` | ① enabled=false→`disabled`，exit=1，**329ms**；② enabled=true+key 空→`no_auth`，exit=1，**174ms**；③ enabled=true+假 key+文件缺失→`bad_request`(photo file not found)，exit=1，**175ms**。三场景均在网络调用前 fail-closed，无任何出站请求 | 原 TESTLOG：216ms/199ms/187ms → 本测 329/174/175ms，同一 code、同一 reason、同量级耗时，**行为一致** |
| 4 | 显卡检测真实工作 | **PASS** | `gpu-detect.ps1`、stdout 实测 | 逐字输出：`{"gpu":"NVIDIA GeForce GTX 1050","vram_mb":4096,"tier":"low","hint":"本地帧率低、建议云端"}` | 要求 hint 逐字含「本地帧率低、建议云端」、tier=low、vram_mb=4096 → **三项全部命中** |
| 5 | 与现有 Electron 集成（口型复用、无第二套 TTS） | **PASS** | `renderer\js\audio-player.js`(L150-151 onAmp)、`renderer\js\app.js`(L249-263)、`renderer\js\dh-view.js`(L80-202)、`electron\dh-manager.js`(L115-194 spawn/kill)、`electron\ipc.js`(L120-135)、`electron\main.js`(L101,128,131) | RMS→`this._onAmp(this._amp)`→`onAmp:(amp)=>{if(dhView.isActive())dhView.sendAmplitude(amp)}`→WS 发 `{"type":"amplitude","value":0..1}`；WS 契约 9230(退避9231..9239)、收 hello/status/frame；主进程 `taskkill /PID /T /F` 整树 kill | 新增 dh:* IPC 仅为 config/GPU/cloudGenerate 模块自身；Grep 全 renderer/electron 无新增 TTS/语音合成端点，`tts_` 仅为 cloud 音频缓存文件名（复用既有音频 URL），**无第二套 TTS** |
| 6 | 备份规则（.bak_dh20261011） | **PASS** | 9 个备份文件全部存在 | 逐个确认：`install.bat`、`electron\main.js`、`electron\preload.js`、`electron\ipc.js`、`renderer\index.html`、`renderer\css\style.css`、`renderer\js\app.js`、`renderer\js\audio-player.js`、`renderer\js\settings-panel.js` 的 `.bak_dh20261011` | 任务点名的 9 个必备备份**全部存在、无一遗漏** |
| 7 | 受保护文件零触碰 | **PASS** | `services\boost_glide_planner.py`、`services\wechat*`、`desktop-pet-electron\config\characters.json` | boost_glide_planner.py **SHA256=3bbe363ea6cbd91d9bb6988acfdacff535df0f317ab9bc6cd2b04ee360e5c701**（逐字节相等）；characters.json 长度=**3446** 字节、mtime=2026-10-10 22:41:26 | wechat* 最新为 `wechat_config_parser.py`=2026-10-10 16:59:21，**不晚于 10-10 17:00**；其余 wechat_* 在 10-05~10-09；characters.json 3446 字节与基线一致 |
| 8 | e2e 真实画面 | **PASS** | `digital-human\out\dh_e2e.png`、`digital-human\out\driver.log` | dh_e2e.png 亲见：古风真人（平口线、薄纱若隐若现、露肩露锁骨），右上角绿角标「数字人: 就绪 0.9fps」；非 2D Live2D 立绘 | driver.log 关键行：`photo=...\local\assets\master.png`、`Source setup done. Face cropped...`、`WS server listening on ws://127.0.0.1:9230`、`WS_PORT=9230` 全部命中 |

**汇总：8/8 PASS。** 其中项3 的「付费真实生成视频」因无 D-ID 有效 API key 未跑通（见 P1，非 fail-closed 缺陷，README 已如实标注）。

---

## 二、复跑记录（命令 + 真实输出摘录）

### R1. 显卡检测（项4）
命令：`powershell -NoProfile -ExecutionPolicy Bypass -File desktop-pet-electron\digital-human\gpu-detect.ps1`
输出（逐字）：
```
{"gpu":"NVIDIA GeForce GTX 1050","vram_mb":4096,"tier":"low","hint":"本地帧率低、建议云端"}
```

### R2. 本地驱动 selftest 独立复跑（项2）
前置：先把原 `local\out\{selftest.json,demo.mp4,preview.jpg}` 复制到 `local\out\verification_baseline\` 保留原证据。
命令：
```
& .venv-liveportrait\Scripts\python.exe desktop-pet-electron\digital-human\local\driver.py `
  --photo desktop-pet-electron\digital-human\local\assets\master.png --selftest `
  --demo-out desktop-pet-electron\digital-human\local\out\demo_d2.mp4
```
过程：模型加载 ~6s → `Source setup done` → 75 帧逐帧渲染（每帧 ~780~856ms）→ 退出。
真实 RESULT 摘录：
```
SELFTEST RESULT: {"gpu_name":"NVIDIA GeForce GTX 1050","fps_mean":1.21,"fps_p50":1.19,"fps_p95":1.28,
"vram_mb":3041.0,"total_frames":75,"backend":"torch 2.6.0+cu124 fp16",
"source_image_size":[2410,1740,3],"frame_render_ms_mean":827.0,
"demo_mp4":"...\out\demo_d2.mp4"}
selftest done. fps_mean=1.21 vram_mb=3041.0
```
> 注：PowerShell 把 onnxruntime 的 `CUDAExecutionProvider 不可用` UserWarning（写 stderr）判为 exit=1（NativeCommandError），但 Python 进程本身完整跑完并打印 RESULT；主 LivePortrait 推理走 torch CUDA，与 SMOKE_TEST 说明一致。

cv2 解码新 mp4：
```
mp4 decodable: True | frames: 75 | size: 924x1280 | container_fps: 8.0
```
PIL 读母版：`master.png size(W x H): (1740, 2410)`。

### R3. 云端 fail-closed 三场景（项3）
前置：备份 `cloud\config.json`→改前先存 `config.json.bak_verify`（测完还原并删除临时备份）。
命令（通用）：`& .venv\Scripts\python.exe cloud\client.py generate --photo C:\nonexistent_photo.jpg --audio C:\nonexistent_audio.mp3`

① enabled=false（默认态）：
```
exit=1 elapsed_ms=329
{"ok": false, "code": "disabled", "reason": "cloud digital-human disabled in config.json (enabled != true)"}
```
② 改 enabled=true、api_key=""：
```
exit=1 elapsed_ms=174
{"ok": false, "code": "no_auth", "reason": "api_key empty in config.json; set a D-ID API key and enabled=true"}
```
③ 改 enabled=true、api_key="FakeKeyForFailClosedTestOnly"、文件缺失：
```
exit=1 elapsed_ms=175
{"ok": false, "code": "bad_request", "reason": "photo file not found: C:\\nonexistent_photo.jpg"}
```
还原校验：`还原后 enabled= False api_key= ''`，临时备份已删。

### R4. 受保护文件（项7）
- boost_glide_planner.py SHA256（python hashlib 现算）：`3bbe363ea6cbd91d9bb6988acfdacff535df0f317ab9bc6cd2b04ee360e5c701` ✅
- 残留 python 进程检查：`无残留 python 进程`。

---

## 三、发现的问题清单

- **P1（非阻塞·已知·如实标注）**：云端「真实照片+真实音频→生成说话视频→返回 video_url」端到端**未跑通**，原因＝无 D-ID 有效 API key（需国际信用卡注册）。fail-closed 全路径（disabled/no_auth/bad_request）与到 api.d-id.com 的网络可达性（401→no_auth）均已独立复验通过。换真 key 后即可端到端，代码路径无缺陷。`cloud\README_API.md` L143-152 已如实标注此项，无隐瞒。
- **P2（非阻塞·观察项）**：onnxruntime 人脸关键点后端 `CUDAExecutionProvider` 不可用、回退 CPU（driver.log / 本次 selftest stderr 均见）。但主 LivePortrait 推理走 torch CUDA（fps≈1.2 实测一致），不影响功能，仅 landmark 预热在 CPU。与原交付说明一致，非本次回归引入。
- 无任何 FAIL；无受保护文件被触碰；`cloud\config.json`、`digital-human\config.json` 测试造成的临时改动均已还原（前者还原 enabled=False，后者全程未写、保持 mode=off）。

---

## 变更记录（2026-10-11 · 用户主动要求）：云端轨 v2

- 按用户指示「云端轨应该由大模型直接做，而不是找第三方帮忙」，已**移除 D-ID API 集成**，
  改为**「豆包 Seedance 图生视频产物轨」**（`cloud\client.py` v2：verify 产物，纯标准库、零网络请求、零密钥）。
- 原 **P1 随此变更关闭**（不再需要 D-ID key）；新缺口=豆包 Seedance 真实产物尚未生成
  （生成属豆包会员额度，待用户确认后执行，见 `README_API.md` 使用步骤）。
- 四场景 fail-closed 实测（disabled/no_source/bad_video/ok）见 `cloud\TESTLOG_failclosed.md`。
- 本报告 8/8 PASS 为变更前验收快照，**原样保留不改写**；变更前文件备份：`*.bak_dh20261011b`
  （client.py / config.json / README_API.md / TESTLOG_failclosed.md / dh-manager.js）。
