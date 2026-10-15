# -*- coding: utf-8 -*-
r"""
LoveAI 封包基础设施 - 数字人本地轨扩展包构建脚本（build_dh_pack）

职责：把「数字人本地轨」（LivePortrait 本地驱动）从 desktop-pet-electron 中
单独打成一个与主安装包并排发布的扩展 zip。权重（约 628MB）与 torch venv
**绝不进主安装包**；本扩展包只带「引擎代码 + 母版图 + 嵌套权重 zip + 一键安装 bat」，
终端用户运行 install_local.bat 自动建独立 torch venv、解压权重、跑冒烟。

产物（<项目根>\installer_output\）::

    LoveAI_DH_Local_v1.zip          # 外层扩展包（约 630MB）
    pack_manifest_dh.json           # 各组件文件数/字节 + 耗时 + 校验结果

日志与报告（<项目根>\logs\pack_v3\）::

    build_dh_pack.log               # 全量构建日志（关键数字写文件，不依赖 stdout）
    build_dh_pack_report.md         # 人读报告（真实数字）

外层 zip 内部布局（顶层目录 LoveAI_DH_Local_v1）::

    LoveAI_DH_Local_v1\install_local.bat        # 一键建 venv + 装依赖 + 解压权重 + 冒烟
    LoveAI_DH_Local_v1\README.md                # 中文说明
    LoveAI_DH_Local_v1\digital-human\local\     # 引擎代码镜像（排除 out/__pycache__/.vscode/权重）
    LoveAI_DH_Local_v1\pretrained_weights.zip   # 嵌套权重包（9 文件，保留原相对路径）

设计：
- 纯 stdlib（zipfile / os / json / shutil / tempfile / time / traceback），与
  collect_payload.py 同风格；中文注释；幂等重跑（每次删旧外层/嵌套 zip 重建）。
- 嵌套权重 zip 先用 ZIP_DEFLATED compresslevel=1 写到临时文件（pth/onnx 本身已
  高度压缩，别浪费 CPU），再作为单文件加入外层，随后删除临时文件。
- 外层 zip 用 ZIP_DEFLATED 默认级别。
- 构建后自动把外层 zip 解开到临时目录做自校验（关键文件在位、权重字节与源一致、
  排除项不出现），结果写进 manifest 与报告；临时目录用完即删。

用法（项目根，用主 venv python）::

    .venv\Scripts\python.exe scripts\build_dh_pack.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import traceback
import zipfile

# 项目根 = scripts/ 的上一级
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# 源：数字人本地轨目录
SRC_LOCAL = os.path.join(ROOT, "desktop-pet-electron", "digital-human", "local")
# 权重源目录（嵌套 zip 的内容根）
SRC_WEIGHTS = os.path.join(SRC_LOCAL, "LivePortrait", "pretrained_weights")

# 产物
TOP = "LoveAI_DH_Local_v1"                       # zip 内顶层目录名
OUT_DIR = os.path.join(ROOT, "installer_output")
OUT_ZIP = os.path.join(OUT_DIR, f"{TOP}.zip")
MANIFEST = os.path.join(OUT_DIR, "pack_manifest_dh.json")

LOG_DIR = os.path.join(ROOT, "logs", "pack_v3")
LOG_FILE = os.path.join(LOG_DIR, "build_dh_pack.log")
REPORT_FILE = os.path.join(LOG_DIR, "build_dh_pack_report.md")

# 8 个真实权重文件（相对 SRC_WEIGHTS 的路径，正斜杠；与 download_weights.ps1 对齐）。
# pretrained_weights 下另有一个 0 字节 .gitkeep，一并打入嵌套 zip（共 9 个条目）。
WEIGHT_RELS = [
    "liveportrait/base_models/appearance_feature_extractor.pth",
    "liveportrait/base_models/motion_extractor.pth",
    "liveportrait/base_models/spade_generator.pth",
    "liveportrait/base_models/warping_module.pth",
    "liveportrait/retargeting_models/stitching_retargeting_module.pth",
    "liveportrait/landmark.onnx",
    "insightface/models/buffalo_l/2d106det.onnx",
    "insightface/models/buffalo_l/det_10g.onnx",
]

# local\ 下整树镜像时要排除的目录（任意层级命中即剪枝）
_EXCLUDE_DIR_NAMES = {"out", "__pycache__"}
# local\LivePortrait 下要整体排除的子目录（相对 local 的正斜杠前缀）
_EXCLUDE_LP_PREFIXES = (
    "LivePortrait/.vscode",           # 编辑器配置，不进包
    "LivePortrait/pretrained_weights",  # 权重单独走嵌套 zip，绝不裸放在引擎树里
)


# --------------------------------------------------------------------------- #
# 日志：同时写文件与 stdout（本机有 stdout 被吞先例，关键数字一律落盘）
# --------------------------------------------------------------------------- #
class _Logger:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._fh = open(path, "w", encoding="utf-8")

    def log(self, msg: str) -> None:
        self._fh.write(msg + "\n")
        self._fh.flush()
        try:
            print(msg)
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


LOG: _Logger | None = None


def L(msg: str) -> None:
    if LOG is not None:
        LOG.log(msg)


# --------------------------------------------------------------------------- #
# install_local.bat / README.md 内容（构建时生成，直接写进 zip，不落盘）
# --------------------------------------------------------------------------- #
INSTALL_BAT = r"""@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal
title LoveAI Digital-Human Local - Installer

echo ============================================================
echo  LoveAI 数字人本地轨 - 一键安装 (LivePortrait)
echo  脚本目录: %CD%
echo  本脚本会: 建独立 venv -^> 装 torch(cu124) -^> 装依赖 -^> 解压权重 -^> 冒烟
echo ============================================================
echo.

REM ---- 1) 检查 PATH 中有 python ----
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] 未在 PATH 中找到 python，请先安装 Python 3.10+ 并勾选 Add to PATH。
  echo         实测环境: Python 3.13.x。
  pause
  exit /b 1
)
echo [OK] PATH 中的 python:
where python
echo.

REM ---- 2) 建 venv（幂等：已存在则跳过）----
if not exist ".venv-liveportrait\Scripts\python.exe" (
  echo [STEP] 创建独立 venv: .venv-liveportrait ...
  python -m venv .venv-liveportrait
  if errorlevel 1 ( echo [ERROR] venv 创建失败 & pause & exit /b 2 )
) else (
  echo [SKIP] .venv-liveportrait 已存在，跳过创建
)
set "VPY=.venv-liveportrait\Scripts\python.exe"
echo.

REM ---- 3) 升级 pip ----
echo [STEP] 升级 pip ...
"%VPY%" -m pip install --upgrade pip
if errorlevel 1 ( echo [ERROR] pip 升级失败 & pause & exit /b 3 )
echo.

REM ---- 4) torch + torchvision (CUDA 12.4 wheel) ----
echo [STEP] 安装 torch torchvision (cu124, 约 2.5GB，下载较久请耐心) ...
"%VPY%" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
if errorlevel 1 ( echo [ERROR] torch/torchvision 安装失败 & pause & exit /b 4 )
echo.

REM ---- 5) LivePortrait 运行依赖 ----
echo [STEP] 安装运行依赖 (opencv/onnxruntime/websockets/...) ...
"%VPY%" -m pip install opencv-python pyyaml scipy imageio imageio-ffmpeg tqdm rich scikit-image onnxruntime tyro websockets "huggingface_hub[cli]" onnx requests Pillow
if errorlevel 1 ( echo [ERROR] 运行依赖安装失败 & pause & exit /b 5 )
echo.

REM ---- 6) 解压权重（幂等：8 个权重齐了就跳过）----
set "WBASE=digital-human\local\LivePortrait\pretrained_weights"
set "ALLPRESENT=1"
for %%F in (
  "liveportrait\base_models\appearance_feature_extractor.pth"
  "liveportrait\base_models\motion_extractor.pth"
  "liveportrait\base_models\spade_generator.pth"
  "liveportrait\base_models\warping_module.pth"
  "liveportrait\retargeting_models\stitching_retargeting_module.pth"
  "liveportrait\landmark.onnx"
  "insightface\models\buffalo_l\2d106det.onnx"
  "insightface\models\buffalo_l\det_10g.onnx"
) do (
  if not exist "%WBASE%\%%~F" set "ALLPRESENT=0"
)
if "%ALLPRESENT%"=="1" (
  echo [SKIP] 8 个权重文件已齐，跳过解压
) else (
  echo [STEP] 解压 pretrained_weights.zip -^> %WBASE% ...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Force -Path 'pretrained_weights.zip' -DestinationPath '%WBASE%'"
  if errorlevel 1 ( echo [ERROR] 权重解压失败 & pause & exit /b 6 )
)
echo.

REM ---- 7) 冒烟 ----
echo [STEP] 冒烟: verify_torch.py ...
"%VPY%" digital-human\local\verify_torch.py
set "TORCH_RC=%errorlevel%"
echo.
echo [STEP] 冒烟: verify_assets.py ...
"%VPY%" digital-human\local\verify_assets.py
set "ASSETS_RC=%errorlevel%"
echo.

echo ============================================================
echo  安装流程结束。冒烟结果:
echo    verify_torch  exit=%TORCH_RC%   (0=PASS)
echo       注意: verify_torch 内置判定要求设备名含 "GTX 1050"；若你是其他
echo       NVIDIA 卡但 nvidia-smi 可见 CUDA，exit 非 0 不代表不可用。
echo    verify_assets exit=%ASSETS_RC%  (0=PASS)
echo ============================================================
echo.
echo  集成方式（把本解压目录的内容合并进 LoveAI 安装目录）:
echo    1) digital-human\        -^>  LoveAI安装目录\desktop-pet-electron\digital-human\
echo    2) .venv-liveportrait\  -^>  LoveAI安装目录\.venv-liveportrait\
echo    3) 编辑 desktop-pet-electron\digital-human\config.json:
echo       把 "mode" 由 "off" 改为 "local" 即启用本地轨。
echo.
echo  selftest 启动命令（在 LoveAI 安装目录根下执行）:
echo    .venv-liveportrait\Scripts\python.exe desktop-pet-electron\digital-human\local\driver.py --photo desktop-pet-electron\digital-human\local\assets\master.png --selftest
echo.
pause
if "%TORCH_RC%"=="0" ( exit /b 0 ) else ( exit /b %TORCH_RC% )
"""


README_MD = r"""# LoveAI 数字人本地轨扩展包（LoveAI_DH_Local_v1）

本扩展包为 LoveAI 桌宠补充 **数字人本地轨**：把 [LivePortrait](https://github.com/KwaiVGI/LivePortrait)
的模型权重、驱动引擎代码、母版素材一并随包发布，并提供一键脚本自动搭建独立的
torch 虚拟环境，离线/弱网也能跑。

---

## 1. 这个包是干什么的

LoveAI 主安装包内的数字人能力 = **云端兜底 + 2D**。本地实时数字人（LivePortrait
本地驱动）因为要带约 628MB 模型权重 + 一个近 5GB 的 torch 专用 venv，**不进主安装包**，
改由本扩展包并排发布。装上本扩展包，桌宠就多一条「本地实时口型/微动数字人」轨道：

- 源图关键点提取 → 程序化眨眼 + 怠速微动 + 音频振幅映射口型 → 渲染 → WebSocket 推送。
- 权重 + 引擎代码 + 自动建 venv，终端无需手动 pip 拉权重。

## 2. 硬件要求

- **NVIDIA 显卡，4GB 显存起**（CUDA，sm_60 及以上）。
- 实测机器：GeForce GTX 1050 4GB（Pascal sm_6.1），torch 2.6.0+cu124，可跑通。
- 非 NVIDIA / 显存不足 4GB 无法走本地轨（可继续用主包的云端兜底 + 2D）。

## 3. 安装步骤

1. 解压本 zip，得到 `LoveAI_DH_Local_v1\` 目录。
2. 双击运行里面的 `install_local.bat`：
   - 自动建独立 venv `.venv-liveportrait\`（与主程序 venv 隔离，主 venv 不装 torch）；
   - 自动装 torch/torchvision（cu124）与运行依赖；
   - 自动把 `pretrained_weights.zip` 解压到
     `digital-human\local\LivePortrait\pretrained_weights\`（已齐则跳过，幂等）；
   - 自动跑 `verify_torch.py` / `verify_assets.py` 冒烟。
3. 看到「安装流程结束」后按提示集成进 LoveAI 安装目录（见下节）。

> 下载 torch（约 2.5GB）需要联网；权重已随包，无需再联网下载。

## 4. 集成进 LoveAI 安装目录

本包解压后的目录结构与安装目录是「镜像」关系，按下面合并即可让
`desktop-pet-electron\digital-human\config.json` 里的相对路径生效：

| 本包内路径 | 合并到 LoveAI 安装目录的路径 |
|---|---|
| `LoveAI_DH_Local_v1\digital-human\` | `LoveAI安装目录\desktop-pet-electron\digital-human\`（合并，不覆盖云端轨） |
| `LoveAI_DH_Local_v1\.venv-liveportrait\` | `LoveAI安装目录\.venv-liveportrait\` |

然后编辑 `desktop-pet-electron\digital-human\config.json`，把
`"mode": "off"` 改为 `"mode": "local"` 即启用本地轨。

config.json 里的相对路径约定（从安装目录根算起）：

- `python`: `.venv-liveportrait/Scripts/python.exe`
- `driver`:  `desktop-pet-electron/digital-human/local/driver.py`
- `photo`:   `desktop-pet-electron/digital-human/local/assets/master.png`

## 5. 打包版集成状态（诚实声明）

> **请勿承诺打包版可直接启用本地轨。**

当前 `LoveAI_Setup_v3.0.0.exe` 安装后的**打包版桌宠**中，数字人模块的路径解析以应用包
内部（`app.asar`）为基准，本地轨（LivePortrait）的路径适配**尚未在打包环境完成**
（本轮未改动桌宠代码）。因此：

- **源码开发态**：在 `desktop-pet-electron` 目录下 `npm start`，按上一节（第 4 节）
  合并目录、把 `config.json` 的 `mode` 改为 `local`，**可立即启用本地轨**。
- **打包版应用**：本地轨启用适配列为**后续版本事项**，当前无法直接通过本包启用本地轨。
  打包版数字人现状 = **云端兜底 + 2D**；本地未就绪时的回落提示是**正常**行为。

## 6. 诚实性能声明（GTX 1050 4GB 实测，2026-10-11）

来自 `out\selftest.json` 的真实测量（未伪造、未吹）：

| 指标 | 数值 |
|---|---|
| 平均帧率 fps_mean | **约 1.2 fps** |
| 单帧渲染均值 | 约 830 ms |
| 峰值显存 | 约 3041 MB / 4096 MB |
| 模型输入 | 256×256（LivePortrait 原生），裁剪画布 512×512 |
| 后端 | torch 2.6.0+cu124，fp16 半精度 |

**请理性预期**：4GB Pascal 卡跑 LivePortrait 就是个位数帧率（~1.2fps），
远称不上流畅视频。本包不承诺「实时流畅」——它证明的是「4GB 显存可端到端跑通
本地数字人管线」。要更高帧率需更大显存/更新的卡。onnxruntime 人脸检测走 CPU
（仅源图加载时跑一次，不影响每帧帧率）。

## 7. selftest 启动命令

在 LoveAI 安装目录根下：

```
.venv-liveportrait\Scripts\python.exe desktop-pet-electron\digital-human\local\driver.py --photo desktop-pet-electron\digital-human\local\assets\master.png --selftest
```

selftest 模式无需音频/WebSocket 客户端，跑 75 帧程序化驱动并在
`digital-human\local\out\` 产出 demo.mp4 / selftest.json。

## 8. 文件清单

```
LoveAI_DH_Local_v1\
  install_local.bat                 # 一键安装
  README.md                         # 本文件
  pretrained_weights.zip            # 嵌套权重包（9 文件，约 628MB）
  digital-human\local\
    driver.py                       # 驱动本体（WS 服务 + selftest）
    ws_smoke.py / ws_dbg.py         # WebSocket 冒烟/调试
    verify_torch.py / verify_assets.py / verify_mp4.py   # 验证脚本
    download_weights.ps1            # 权重下载脚本（离线包已含权重，备用）
    README_LOCAL.md                 # 本地轨详细说明
    assets\  master.png, master_greenscreen.png          # 锁定母版
    LivePortrait\                   # 官方仓库代码（src\、app.py、inference.py、
                                    # requirements*.txt、readme*.md、assets\ 等）
```

未打入包的内容（有意排除）：`out\`（selftest 产物）、`__pycache__\`、
`LivePortrait\.vscode\`、以及裸放的 `LivePortrait\pretrained_weights\`
（权重已收进 `pretrained_weights.zip`）。

## 9. 常见问题

- **装到一半 pip 报网络错**：torch 来自 download.pytorch.org，需可联网；权重已随包，不依赖 HF/GitHub。
- **verify_torch 打印 FAIL**：脚本内置判定要求设备名含 "GTX 1050"。若是其他 NVIDIA 卡但
  `nvidia-smi` 可见 CUDA，通常仍可用；CPU 机器则本地轨跑不了，请回退云端兜底。
- **显存不足 / OOM**：本管线在 4GB 卡上峰值约 3GB；更老/更小显存的卡可能 OOM。
- **启用后桌宠没动**：确认 config.json 的 `mode` 已改为 `local`，且 `.venv-liveportrait`
  放在安装目录根、`digital-human` 已合并进 `desktop-pet-electron\digital-human`。
"""


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _tree_file_list(root: str):
    """生成 (绝对路径, 相对 root 的正斜杠路径)，自动剪枝排除目录。"""
    for dp, dns, fns in os.walk(root):
        rel = os.path.relpath(dp, root)
        relf = rel.replace("\\", "/")
        if relf == ".":
            relf = ""
        # 剪枝：排除目录名
        parts = set(p for p in relf.split("/") if p) if relf else set()
        if parts & _EXCLUDE_DIR_NAMES:
            dns[:] = []
            continue
        # 剪枝：LivePortrait/.vscode 与 LivePortrait/pretrained_weights
        skip = False
        for pref in _EXCLUDE_LP_PREFIXES:
            if relf == pref or relf.startswith(pref + "/"):
                skip = True
                break
        if skip:
            dns[:] = []
            continue
        for fn in fns:
            ap = os.path.join(dp, fn)
            arc = (relf + "/" + fn) if relf else fn
            yield ap, arc


def build_nested_weights_zip(tmp_dir: str, manifest: dict) -> str:
    """把 SRC_WEIGHTS 全部文件打成临时嵌套 zip（compresslevel=1）。返回临时 zip 路径。"""
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_zip = os.path.join(tmp_dir, "pretrained_weights.zip")
    if os.path.exists(tmp_zip):
        os.remove(tmp_zip)
    t0 = time.time()
    weight_bytes = 0
    weight_files = []
    with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for dp, _, fns in os.walk(SRC_WEIGHTS):
            for fn in fns:
                ap = os.path.join(dp, fn)
                rel = os.path.relpath(ap, SRC_WEIGHTS).replace("\\", "/")
                zf.write(ap, rel)
                sz = os.path.getsize(ap)
                weight_files.append({"rel": rel, "bytes": sz})
                weight_bytes += sz
    dt = time.time() - t0
    manifest["weights"] = {
        "status": "OK" if os.path.isdir(SRC_WEIGHTS) else "MISSING",
        "src": SRC_WEIGHTS,
        "files": len(weight_files),
        "bytes_uncompressed": weight_bytes,
        "nested_zip_bytes": os.path.getsize(tmp_zip),
        "build_sec": round(dt, 1),
        "entries": weight_files,
    }
    L(f"  [权重] {len(weight_files)} 文件, 源 {weight_bytes/1024/1024:.1f} MB, "
      f"嵌套 zip {os.path.getsize(tmp_zip)/1024/1024:.1f} MB, 耗时 {dt:.1f}s")
    return tmp_zip


def build_outer_zip(nested_zip: str, manifest: dict) -> None:
    """写外层 zip：bat/README + digital-human/local 引擎树 + 嵌套权重 zip。"""
    if os.path.exists(OUT_ZIP):
        os.remove(OUT_ZIP)
    os.makedirs(OUT_DIR, exist_ok=True)

    t0 = time.time()
    counts = {"engine_code": 0, "assets": 0, "liveportrait_code": 0}
    bytes_ = {"engine_code": 0, "assets": 0, "liveportrait_code": 0}

    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1) install_local.bat / README.md（生成内容，直接写字符串）
        zf.writestr(f"{TOP}/install_local.bat",
                    INSTALL_BAT.encode("utf-8"))
        zf.writestr(f"{TOP}/README.md",
                    README_MD.encode("utf-8"))
        L("  [写入] install_local.bat / README.md")

        # 2) digital-human/local 引擎树（已按排除规则剪枝）
        for ap, arc in _tree_file_list(SRC_LOCAL):
            full_arc = f"{TOP}/digital-human/local/{arc}"
            zf.write(ap, full_arc)
            sz = os.path.getsize(ap)
            # 分类统计
            if arc.startswith("assets/"):
                key = "assets"
            elif arc.startswith("LivePortrait/"):
                key = "liveportrait_code"
            else:
                key = "engine_code"
            counts[key] += 1
            bytes_[key] += sz

        # 3) 嵌套权重 zip 作为单文件加入
        zf.write(nested_zip, f"{TOP}/pretrained_weights.zip")

    dt = time.time() - t0
    manifest["engine_code"] = {"files": counts["engine_code"], "bytes": bytes_["engine_code"]}
    manifest["assets"] = {"files": counts["assets"], "bytes": bytes_["assets"]}
    manifest["liveportrait_code"] = {"files": counts["liveportrait_code"],
                                     "bytes": bytes_["liveportrait_code"]}
    for k in ("engine_code", "assets", "liveportrait_code"):
        L(f"  [{k}] {counts[k]} 文件, {bytes_[k]/1024/1024:.2f} MB")
    L(f"  [外层 zip] 写入完成, 构建耗时 {dt:.1f}s, "
      f"zip 大小 {os.path.getsize(OUT_ZIP)/1024/1024:.1f} MB")
    manifest["outer_build_sec"] = round(dt, 1)


def self_check(manifest: dict) -> dict:
    """解压外层 zip 到临时目录做自校验，返回校验结果 dict。用完删临时目录。"""
    res: dict = {"checks": [], "pass": True}

    def _chk(name: str, ok: bool, detail: str = "") -> None:
        res["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        if not ok:
            res["pass"] = False
        L(f"    [{'PASS' if ok else 'FAIL'}] {name} {detail}")

    vdir = tempfile.mkdtemp(prefix="loveai_dh_verify_")
    try:
        with zipfile.ZipFile(OUT_ZIP, "r") as zf:
            names = zf.namelist()
            setnames = set(names)

            # 关键文件在位
            _chk("install_local.bat 在位", f"{TOP}/install_local.bat" in setnames)
            _chk("README.md 在位", f"{TOP}/README.md" in setnames)
            _chk("driver.py 在位", f"{TOP}/digital-human/local/driver.py" in setnames)
            _chk("pretrained_weights.zip 在位", f"{TOP}/pretrained_weights.zip" in setnames)

            # 排除项不得出现
            bad_out = [n for n in names if "/digital-human/local/out/" in n]
            _chk("不含 out\\", len(bad_out) == 0, f"命中 {len(bad_out)} 条")
            bad_pyc = [n for n in names if "__pycache__" in n]
            _chk("不含 __pycache__", len(bad_pyc) == 0, f"命中 {len(bad_pyc)} 条")
            bad_vsc = [n for n in names if "/.vscode/" in n]
            _chk("不含 .vscode", len(bad_vsc) == 0, f"命中 {len(bad_vsc)} 条")
            bad_pw = [n for n in names if "/digital-human/local/LivePortrait/pretrained_weights/" in n]
            _chk("引擎树内不裸放权重", len(bad_pw) == 0, f"命中 {len(bad_pw)} 条")

            # 解压嵌套权重 zip 到临时目录，逐文件比对字节
            nested_arc = f"{TOP}/pretrained_weights.zip"
            zf.extract(nested_arc, vdir)
            nested_path = os.path.join(vdir, nested_arc.replace("/", os.sep))
            with zipfile.ZipFile(nested_path, "r") as nested:
                nested_names = nested.namelist()
                _chk("嵌套 zip 条目数=9", len(nested_names) == 9,
                     f"实际 {len(nested_names)}")
                mismatch = []
                for rel in WEIGHT_RELS:
                    sp = os.path.join(SRC_WEIGHTS, rel.replace("/", os.sep))
                    if rel not in nested_names:
                        mismatch.append(f"{rel} 缺")
                        continue
                    src_sz = os.path.getsize(sp)
                    zip_sz = nested.getinfo(rel).file_size
                    if src_sz != zip_sz:
                        mismatch.append(f"{rel} 源{src_sz}!=包{zip_sz}")
                _chk("8 个权重字节数与源一致", len(mismatch) == 0,
                     "; ".join(mismatch) if mismatch else "全部一致")

                # 实际解压嵌套 zip 到磁盘再核一次大小（任务要求“解压后字节数一致”）
                extr = os.path.join(vdir, "pw")
                os.makedirs(extr, exist_ok=True)
                nested.extractall(extr)
                disk_mismatch = []
                for rel in WEIGHT_RELS:
                    sp = os.path.join(SRC_WEIGHTS, rel.replace("/", os.sep))
                    dp = os.path.join(extr, rel.replace("/", os.sep))
                    if not os.path.exists(dp):
                        disk_mismatch.append(f"{rel} 未解压出")
                    elif os.path.getsize(dp) != os.path.getsize(sp):
                        disk_mismatch.append(f"{rel} 解压后大小不符")
                _chk("解压后 8 权重磁盘字节与源一致", len(disk_mismatch) == 0,
                     "; ".join(disk_mismatch) if disk_mismatch else "全部一致")
    finally:
        shutil.rmtree(vdir, ignore_errors=True)

    manifest["self_check"] = res
    return res


def write_report(manifest: dict, elapsed: float) -> None:
    """把真实数字写成人读 Markdown 报告。"""
    sc = manifest.get("self_check", {})
    lines = []
    lines.append("# LoveAI 数字人本地轨扩展包 - 构建报告")
    lines.append("")
    lines.append(f"- 构建时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 总构建耗时：{elapsed:.1f}s")
    lines.append(f"- 外层 zip：`{OUT_ZIP}`")
    lines.append(f"- 外层 zip 大小：{os.path.getsize(OUT_ZIP):,} 字节 "
                 f"（{os.path.getsize(OUT_ZIP)/1024/1024:.1f} MB）")
    lines.append("")
    lines.append("## 各组件清单")
    lines.append("")
    lines.append("| 组件 | 文件数 | 字节 | MB |")
    lines.append("|---|---:|---:|---:|")
    for k in ("engine_code", "assets", "liveportrait_code"):
        c = manifest[k]
        lines.append(f"| {k} | {c['files']} | {c['bytes']:,} | {c['bytes']/1024/1024:.2f} |")
    w = manifest["weights"]
    lines.append(f"| weights(源,未压缩) | {w['files']} | {w['bytes_uncompressed']:,} | "
                 f"{w['bytes_uncompressed']/1024/1024:.1f} |")
    lines.append(f"| weights(嵌套zip) | - | {w['nested_zip_bytes']:,} | "
                 f"{w['nested_zip_bytes']/1024/1024:.1f} |")
    lines.append("")
    lines.append("## 权重逐文件（相对 pretrained_weights）")
    lines.append("")
    lines.append("| 相对路径 | 字节 |")
    lines.append("|---|---:|")
    for e in w["entries"]:
        lines.append(f"| {e['rel']} | {e['bytes']:,} |")
    lines.append("")
    lines.append("## 自校验结果")
    lines.append("")
    lines.append(f"总体：{'全部 PASS' if sc.get('pass') else '存在 FAIL'}")
    lines.append("")
    lines.append("| 检查项 | 结果 | 明细 |")
    lines.append("|---|---|---|")
    for c in sc.get("checks", []):
        lines.append(f"| {c['check']} | {'PASS' if c['ok'] else 'FAIL'} | {c['detail']} |")
    lines.append("")
    lines.append("## 说明")
    lines.append("")
    lines.append("- 嵌套权重 zip 用 ZIP_DEFLATED compresslevel=1（pth/onnx 本身已压缩）。")
    lines.append("- 引擎树镜像自 `desktop-pet-electron\\digital-human\\local`，"
                 "已排除 `out\\`、`__pycache__\\`、`LivePortrait\\.vscode\\`、"
                 "`LivePortrait\\pretrained_weights\\`（权重走嵌套 zip）。")
    lines.append("- 本脚本未修改任何既有源码/配置。")
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    L(f"  [报告] 已写: {REPORT_FILE}")


def main() -> int:
    global LOG
    os.makedirs(LOG_DIR, exist_ok=True)
    LOG = _Logger(LOG_FILE)
    t_start = time.time()

    L("=" * 64)
    L("LoveAI 数字人本地轨扩展包构建")
    L("=" * 64)
    L(f"项目根: {ROOT}")
    L(f"源 local: {SRC_LOCAL}")
    L(f"源权重:   {SRC_WEIGHTS}")

    # 前置检查：源必须在位
    if not os.path.isdir(SRC_LOCAL):
        L(f"[FATAL] 源 local 目录不存在: {SRC_LOCAL}")
        return 1
    if not os.path.isdir(SRC_WEIGHTS):
        L(f"[FATAL] 权重目录不存在: {SRC_WEIGHTS}")
        return 1
    for rel in WEIGHT_RELS:
        sp = os.path.join(SRC_WEIGHTS, rel.replace("/", os.sep))
        if not os.path.isfile(sp):
            L(f"[FATAL] 缺权重文件: {sp}")
            return 1

    manifest: dict = {
        "root": ROOT,
        "src_local": SRC_LOCAL,
        "src_weights": SRC_WEIGHTS,
        "top_dir": TOP,
        "outer_zip": OUT_ZIP,
    }

    tmp_dir = tempfile.mkdtemp(prefix="loveai_dh_pack_")
    try:
        # 1) 嵌套权重 zip -> 临时文件
        L("[1/4] 打包嵌套权重 zip ...")
        nested_zip = build_nested_weights_zip(tmp_dir, manifest)

        # 2) 外层 zip（含引擎树 + bat/README + 嵌套权重）
        L("[2/4] 打包外层 zip ...")
        build_outer_zip(nested_zip, manifest)

        # 3) 删除临时嵌套 zip 文件（外层 zip 已把它读入）
        L("[3/4] 清理临时嵌套 zip ...")
        try:
            os.remove(nested_zip)
            L(f"  已删除 {nested_zip}")
        except OSError as e:
            L(f"  [WARN] 删除临时嵌套 zip 失败: {e}")

        # 4) 自校验
        L("[4/4] 自校验 ...")
        sc = self_check(manifest)

        # 汇总
        elapsed = time.time() - t_start
        manifest["outer_zip_bytes"] = os.path.getsize(OUT_ZIP)
        manifest["total_build_sec"] = round(elapsed, 1)
        manifest["self_check_pass"] = sc.get("pass", False)

        with open(MANIFEST, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        L(f"  [清单] 已写: {MANIFEST}")

        write_report(manifest, elapsed)

        L("-" * 64)
        L(f"外层 zip: {OUT_ZIP}")
        L(f"外层 zip 大小: {os.path.getsize(OUT_ZIP):,} 字节 "
          f"({os.path.getsize(OUT_ZIP)/1024/1024:.1f} MB)")
        L(f"总耗时: {elapsed:.1f}s")
        L(f"自校验: {'ALL PASS' if sc.get('pass') else 'HAS FAIL'}")
        L("=" * 64)
        return 0 if sc.get("pass") else 10
    except Exception:
        L("[FATAL] 构建异常:\n" + traceback.format_exc())
        return 1
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if LOG is not None:
            LOG.close()


if __name__ == "__main__":
    sys.exit(main())
