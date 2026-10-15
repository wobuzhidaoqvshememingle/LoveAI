# LoveAI 本地数字人驱动（LivePortrait 低显存）—— 本地轨说明

本目录是「实时数字人 = 双轨·默认本地」架构中的**本地驱动轨**。
只负责：源图关键点提取 → 程序化眨眼 + 怠速微动 + 音频振幅映射口型 → 渲染 → WS 推送。
**不碰 Electron 集成**（另有并行任务按冻结契约对接）。

---

## 1. 环境与硬件实测

- GPU：NVIDIA GeForce GTX 1050 4GB（Pascal sm_6.1，驱动 582.78）
- 系统 Python：3.13.13
- 专用 venv：`..\..\.venv-liveportrait\`（与项目主 `.venv` 隔离，主 venv 保持纯净未装 torch）
- torch：2.6.0+cu124，`torch.cuda.is_available()==True`，设备名含 "GTX 1050"
  - 验证脚本：`..\..\..\..\desktop-pet-electron\digital-human\local\verify_torch.py`
  - 验证输出存档：`LoveAI-v2\logs\torch_verify.log`

---

## 2. 安装命令（完整可复现）

> 全部在 PowerShell 下执行。`$py` 为系统 Python 3.13.13。

```powershell
$py = "C:\Users\87433\AppData\Local\Doubao\User Data\sandbox_runtime\bases\d0ace100fdff5b2d247847aca1e3d83b\python\python.exe"
$venv = "<PROJECT_ROOT>\.venv-liveportrait"

# 1) 建 venv
& $py -m venv $venv
& "$venv\Scripts\python.exe" -m pip install --upgrade pip

# 2) torch + torchvision（CUDA 12.4 wheel，GTX 1050 sm_61 支持）
& "$venv\Scripts\python.exe" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124

# 3) LivePortrait 运行依赖
& "$venv\Scripts\python.exe" -m pip install opencv-python pyyaml scipy imageio imageio-ffmpeg tqdm rich scikit-image onnxruntime tyro websockets "huggingface_hub[cli]" onnx requests Pillow

# 4) 验证 CUDA
& "$venv\Scripts\python.exe" verify_torch.py
```

完整 `pip freeze` 见 `LoveAI-v2\logs\pip_freeze.txt`。

### 代码与权重来源

- **代码**：LivePortrait 官方仓库 `KwaiVGI/LivePortrait`（main 分支 zip），
  经 `https://gh-proxy.com/` 加速下载（github.com 直连超时）。解压到 `LivePortrait\`。
- **权重**：HF 仓 `KlingTeam/LivePortrait`，经 `https://hf-mirror.com/` 直链下载
  （huggingface.co 直连不可达）。落到 `LivePortrait\pretrained_weights\`，与
  `src/config/{inference_config,crop_config}.py` 中默认路径一致。
- 下载脚本：`download_weights.ps1`，日志：`LoveAI-v2\logs\weights_download.log`。

---

## 3. 启动方法

```powershell
$venv = "<PROJECT_ROOT>\.venv-liveportrait"
$dh   = "<PROJECT_ROOT>\desktop-pet-electron\digital-human\local"

# 自检模式（无需音频/WS 客户端，跑 75 帧程序化驱动，产出 out\demo.mp4 等）
& "$venv\Scripts\python.exe" "$dh\driver.py" --photo "$dh\assets\master.png" --selftest --log "<PROJECT_ROOT>\logs\dh-local.log"

# 实时 WS 模式（Electron 对接用）
& "$venv\Scripts\python.exe" "$dh\driver.py" --photo "$dh\assets\master.png" --log "<PROJECT_ROOT>\logs\dh-local.log"

# 绿幕抠像模式（推 PNG+alpha）
& "$venv\Scripts\python.exe" "$dh\driver.py" --photo "$dh\assets\master_greenscreen.png" --chroma --log "...\dh-local.log"
```

CLI 参数（冻结契约，逐字对齐）：
`--photo <path> [--port 9230] [--chroma] [--fps-max N] [--selftest] [--demo-out <mp4>] [--log <file>]`

---

## 4. 实测性能（GTX 1050 4GB，真实测得）

来自 `out\selftest.json`（2026-10-11 实跑）：

| 指标 | 数值 |
|---|---|
| fps_mean | **1.21** |
| fps_p50 | 1.19 |
| fps_p95 | 1.28 |
| 单帧渲染均值 | 830 ms |
| 峰值显存 (torch.cuda.max_memory_allocated) | **3041 MB / 4096 MB** |
| 总帧数 | 75（≥60 达标） |
| 后端 | torch 2.6.0+cu124，fp16 半精度 |
| 源图尺寸 | 2410×1740 |
| 模型输入 | 256×256（LivePortrait 原生），裁剪画布 512×512 |
| 输出帧 | 924×1280（pasteback 后全幅） |

**说明与诚实声明：**
- fps 个位数（~1.2）是 4GB Pascal 卡跑 LivePortrait 的预期结果，**未伪造**。
  帧时间用 `time.perf_counter()` 测完整渲染周期（含 warp_decode + pasteback），
  不是「单次推理时延」冒充。
- 偶发卡顿：帧 26 出现一次 1067ms（p95=1.28fps 体现），其余稳定 ~800ms。
- 输入尺寸：任务书提到「512 输入 OOM 降 384」——LivePortrait 模型原生输入固定
  256×256，**不是 512**；512 是人脸**裁剪画布**（crop_cfg.dsize）。当前 256 输入 +
  512 裁剪在 4GB 下未 OOM（峰值 3041MB），故无需降到 384。
- fp16 半精度已开（`flag_use_half_precision=True`）。
- onnxruntime 跑人脸检测/landmark 用 **CPU**（未装 onnxruntime-gpu），检测只在源图
  加载时跑一次，不影响每帧 fps。

---

## 5. WS 接口契约（已冒烟测试通过）

- 地址：`ws://127.0.0.1:9230`（被占用自动退避 9231…9239，启动时打印 `WS_PORT=<实际端口>`，
  并在 status 消息 `msg` 字段报告）。
- 入站（驱动接收，文本 JSON）：
  - `{"type":"amplitude","value":0.0~1.0}` —— 口型开合系数（0=闭嘴，1→≈0.8 嘴部 delta）
  - `{"type":"hi"}` —— 渲染端上线，驱动回 status
  - `{"type":"quit"}`
- 出站（驱动发送，文本 JSON）：
  - `{"type":"hello","proto":1}` —— 连接建立即发
  - `{"type":"status","state":"ready","fps":<ema_fps>,"gpu":"NVIDIA GeForce GTX 1050","msg":"listening on <port>"}`
  - `{"type":"frame","fmt":"jpeg","data":"<base64>","w":924,"h":1280,"ts":<ms>}`
    （`--chroma` 时 `fmt="png"` 且含 alpha 通道）

冒烟测试记录：`out\ws_smoke.log` / `out\ws_dbg.log`。实测握手 → hello → status → 连续 6 帧 jpeg → quit 全通。

### Electron 对接要点
- 驱动进程由 Electron 侧拉起（`python driver.py --photo ... --log ...`），解析 stdout
  的 `WS_PORT=<port>` 行拿到实际端口。
- Electron 作为 WS client 连入，收到 `hello` 后发 `hi`，然后持续收 `frame` 消息，
  base64 解码后丢给 `<img>` / canvas / 视频纹理。
- 口型：把现有音频振幅链路归一化到 0~1 后发 `amplitude` 消息即可。
- 驱动已用 `asyncio.to_thread` 把 GPU 渲染丢到线程，事件循环不阻塞，握手/收消息不卡。

---

## 6. 绿幕抠像（--chroma）

`--chroma` 时输出 RGBA PNG：cv2 HSV 阈值（H 35~85, S>60, V>40）+ 7px 高斯羽化出 alpha。
**注意**：当前母版 `master_greenscreen.png` 是绿幕版，但本自检用的是普通 `master.png`
（非绿幕背景），故 `--chroma` 的抠像效果未在本次 selftest 中验证。绿幕版抠像质量
待 Electron 联调时用 `master_greenscreen.png` 实测后补记。

---

## 7. 缺口清单（诚实列出）

1. **fps 仅 ~1.2**：远低于流畅实时（≥15fps）。4GB Pascal 卡硬上限。优化方向（未做）：
   降输出分辨率、关闭 pasteback（直接推 256×256 裁剪图）、torch.compile（当前关闭，
   sm_61 上 compile 可能反而更慢/报错）、换 onnxruntime-gpu 跑检测。
2. **绿幕抠像效果未实测**：见上节第 6 条。
3. **头部/肩部怠速动很弱**：当前只加了 ±0.006 的 xy 平移正弦摆动，未做真正的
   头部姿态（pitch/yaw/roll）微动——那需要每帧重算旋转矩阵，会再加显存/耗时。
4. **多客户端**：当前帧广播给所有连接，但未做背压/断线清理优化。
5. **音频→振幅链路**：本驱动只接收 `amplitude` 数值，不负责从音频算振幅；
   上游（现有音频振幅链路）对接是 Electron 侧任务。
6. **insightface onnx 用 CPU**：未装 onnxruntime-gpu（避免 CUDA 版本冲突），检测虽
   只跑一次，但若换源图频繁会有几秒 CPU 开销。
7. **未做 384 降级**：256 原生输入未 OOM，故未触发 384 路径（代码里也未实现 384
   分支，因为模型固定 256）。

---

## 8. 文件清单

- `driver.py` —— 驱动本体（WS 服务 + selftest）
- `LivePortrait\` —— 官方仓库代码 + pretrained_weights
- `assets\master.png` / `master_greenscreen.png` —— 锁定母版（1740×2410）
- `out\demo.mp4` —— selftest 75 帧渲染视频（cv2 校验 75/75 可解码）
- `out\preview.jpg` —— 闭嘴/半开/大开三帧拼图
- `out\selftest.json` —— 真实 fps / 显存 / 配置统计
- `verify_torch.py` / `verify_assets.py` / `verify_mp4.py` —— 验证脚本
- `download_weights.ps1` —— 权重下载脚本
