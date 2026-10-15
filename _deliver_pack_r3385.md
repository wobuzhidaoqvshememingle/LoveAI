# LoveAI v3.1.0 重新封包交付报告（r3385）

- 生成时间：2026-10-15（本地 JST；编译于 23:40 完成）
- 工作目录：`C:\Users\87433\Doubao\chats\2026-10-04\new-chat-28\LoveAI-v2`
- 任务性质：**重新封包**（沿用既有链路 PyInstaller → Inno Setup），非新建打包方案
- 结论：**成功**。安装包已真实产出并通过核验；PyInstaller 与 ISCC 均 exit code 0；ISCC 日志含 `Successful compile`。

---

## 1. 环境核查（构建前）

| 项 | 数值 |
|---|---|
| C: 可用 | 16.4 GB |
| D: 可用 | 97.5 GB |
| E: 可用 | 159.62 GB |
| 物理内存 / 空闲 | 7.86 GB / 1.37 GB |
| 遗留重型进程 | 无（无 pytest / pyinstaller / ISCC） |
| 本任务无关的并行进程 | `.venv\python.exe _gh_device.py` ×2（约 38 MB）+ 浏览器自动化 runtime ×1（约 29 MB），属其它任务，**未触碰** |

**关键发现（决定输出落盘位置）**：`dist`、`installer_output`、`dist_bundle`、`models_local` 均为 **Junction**：

```
dist             -> E:\LoveAI_build\relocated\dist
installer_output -> E:\LoveAI_build\installer
dist_bundle      -> E:\LoveAI_build\relocated\dist_bundle
models_local     -> E:\LoveAI_build\relocated\models_local
```

因此 `dist\LoveAI\` 与安装包实际落在 **E 盘**（159 GB 可用），C 盘空间自始不是约束。E 盘微信文件与解密副本**零触碰**。

---

## 2. 构建前备份（遵守"先备份再覆盖"）

| 被覆盖对象 | 备份方式 | 备份路径 | 规模 |
|---|---|---|---|
| `dist\LoveAI`（2026-10-12 旧 onedir） | 同卷改名（瞬时、保留原件） | `dist\LoveAI.bak_r3385_20261015_2250` | 4,765 文件 / **1,740,562,194 B**（1,659.9 MB） |
| `build\LoveAI`（PyInstaller 缓存） | 复制 | `build\LoveAI.bak_r3385_20261015_2250` | 17 文件 / **111,344,033 B**（106.2 MB） |

未覆盖任何既有安装包：`installer_output` 原仅有 v2.0.0 / v3.0.0 / DH zip，**无** v3.1.0，故本次为新产出，未覆盖文件。旧的 v3.1.0（2026-10-12）仍在 `D:\LoveAI_pack31\`，未被改动。

---

## 3. 步骤一：PyInstaller 重建 `dist\LoveAI`

- 命令（项目根执行，未自创参数）：
  `.venv\Scripts\python.exe -m PyInstaller LoveAI.spec --noconfirm`
- 环境：`OMP_NUM_THREADS=1`、`OPENBLAS_NUM_THREADS=1`、`MKL_NUM_THREADS=1`、`PYTHONIOENCODING=utf-8`；`LOVEAI_BUNDLE_MODELS/OLLAMA/KNOWLEDGE/VOICE` 显式清除（保证 v3.1.0 口径）
- 执行方式：**后台 + 文件留痕**（Start-Process 重定向）
- **start = 2026-10-15 22:47:04**
- **end = 2026-10-15 22:57:43**
- **elapsed = 639.2 s（约 10 分 39 秒）**
- **exit_code = 0**
- 日志绝对路径：
  - `C:\Users\87433\Doubao\chats\2026-10-04\new-chat-28\LoveAI-v2\logs\pack_r3385\pyinstaller.log`（合并 stdout+stderr）
  - `...\logs\pack_r3385\pyinstaller.stdout.log` / `pyinstaller.stderr.log` / `pyinstaller_meta.txt`

日志中的口径证据：

```
[LoveAI.spec] 组件开关: MODELS=False OLLAMA=False KNOWLEDGE=False VOICE=False
[LoveAI.spec]   + Electron 桌宠运行资源 desktop-pet-electron/dist/win-unpacked
578482 INFO: Building EXE from EXE-00.toc completed successfully.
637061 INFO: Building COLLECT COLLECT-00.toc completed successfully.
637245 INFO: Build complete! The results are available in: ...\dist
```

产物结构：

| 项 | 值 |
|---|---|
| `dist\LoveAI\LoveAI.exe` | 存在，**32,415,810 字节**，mtime 2026-10-15 22:56:44 |
| `dist\LoveAI\_internal\` | 存在 |
| `dist\LoveAI` 文件数 / 总大小 | **4,767 文件 / 1,741,944,851 字节（1,661.2 MB）** |

（该 4,767 文件数为清理本次冒烟运行期残留后的最终值，与 PyInstaller 原始输出一致。）

---

## 4. 步骤二：Inno Setup 编译安装包

- 命令（项目根执行，`.iss` 未改）：
  `& "C:\Program Files\Inno Setup 7\ISCC.exe" "tools\installer\LoveAI.iss"`
- 执行方式：**后台 + 文件留痕**
- **start = 2026-10-15 23:00:20**
- **end = 2026-10-15 23:40:00**
- **elapsed = 2380 s（约 39 分 40 秒）**
- **exit_code = 0**
- ISCC 自报：**`Successful compile (2379.063 sec). Resulting Setup program filename is: C:\Users\87433\Doubao\chats\2026-10-04\new-chat-28\LoveAI-v2\installer_output\LoveAI_Setup_v3.1.0.exe`**
- 日志绝对路径：
  - `C:\Users\87433\Doubao\chats\2026-10-04\new-chat-28\LoveAI-v2\logs\pack_r3385\iscc.log`（合并 stdout+stderr）
  - `...\logs\pack_r3385\iscc.stdout.log` / `iscc.stderr.log` / `iscc_meta.txt`

输入新鲜性证据：`iscc.stdout.log` 逐条打印 `Compressing: ...\dist\LoveAI\_internal\...`（如 `_internal\av\audio\fifo.pyd`、`_internal\PySide6\Qt6WebEngineCore.dll`、`...\desktop-pet-electron\dist\win-unpacked\resources\app.asar`），确证 ISCC 读取的是 **22:57 重建后的新 dist**，而非 10-12 旧产物。

耗时说明：本次 ISCC 实际 **2380 s**，明显长于历史参照 1538 s（2026-10-12）。原因是本机仅有 ~1.2 GB 空闲物理内存、lzma2/ultra + SolidCompression 压缩 3.9 GB 负载时频繁换页；同机还存在其它并行任务占用内存。**耗时数字取自真实计时**，未做估算或美化。

---

## 5. 产物核验（步骤三）

| 项 | 值 |
|---|---|
| 绝对路径 | `C:\Users\87433\Doubao\chats\2026-10-04\new-chat-28\LoveAI-v2\installer_output\LoveAI_Setup_v3.1.0.exe` |
| 真实落盘路径 | `E:\LoveAI_build\installer\LoveAI_Setup_v3.1.0.exe`（经 junction） |
| **精确字节数** | **3,366,845,259 字节**（3,210.87 MB / 3.1355 GiB） |
| **SHA-256** | **D25D74419A08788D171127F7C7E62A883CE37F2B59105094B8DB8AF4ED5C0717** |
| mtime | 2026-10-15 23:40:00 |
| PE 头 | `MZ`（可解析为可执行文件） |
| 文件数（dist\LoveAI） | 4,767 |

---

## 6. 步骤四：冒烟验证

**做了哪一种：做了「最小启动冒烟（GUI）」——不是仅做静态完整性核验。**

- 脚本：`...\logs\pack_r3385\smoke_startup_r3385.py`（本次自建，有界，约 40 s 内强制结束）
- 输出：`...\logs\pack_r3385\smoke.txt`

结果：

| 检查 | 结果 |
|---|---|
| exe 启动 | 存活，`pid=7236` |
| **主窗口出现** | **是，t = 38,871 ms（hwnd=1836518，内存 218.1 MB）** |
| 子进程 | 拉起 4 个 `LoveAIPet.exe`（Electron 桌宠，48–255 MB） |
| 结束清理 | 强制结束后残留 `LoveAI*` 进程数 = **0** |
| 关键依赖在位 | `_internal\PySide6\Qt6Core.dll` ✔、`Qt6WebEngineCore.dll` ✔、`Qt6WebEngineWidgets.dll` ✔、`python3.dll` ✔、`python314.dll` ✔、桌宠 `win-unpacked` ✔ |

**数据红线自证（重要）**：冻结态 `runtime_paths.writable_data_dir()` = `exe 同级\data`，即写入 `dist\LoveAI\data`，**不会**指向项目真实库。启动前后对真实库做了 SHA-256 比对：

```
REAL_DB_BEFORE  mtime=2026-10-14 22:18:43  bytes=7299072  sha=0328ADB380ECDC8DFF16201092EF4D13DACA50A77C8B3BBE0FD4996DADCE58DE
REAL_DB_AFTER   mtime=2026-10-14 22:18:43  bytes=7299072  sha=0328ADB380ECDC8DFF16201092EF4D13DACA50A77C8B3BBE0FD4996DADCE58DE
REAL_DB_UNCHANGED = True
```

冻结程序实际写入 `dist\LoveAI\data`（`loveai.db` / `-shm` / `-wal` / `pet_shared.json` / `rag_index_state.json` / `unified_search.db`），证明数据落安装目录。冒烟产生的运行期残留（该 `data\logs\backups\models_local\ollama_data` 目录、桌宠 `debug.log`）已在核验后清理，`dist\LoveAI` 恢复为 PyInstaller 原始输出。

---

## 7. 与上一版 v3.1.0 的差异

| 项 | 上一版（`D:\LoveAI_pack31\`） | 本次（`installer_output\`） | 差异 |
|---|---|---|---|
| 字节数 | 3,366,473,792（3,210.52 MB） | 3,366,845,259（3,210.87 MB） | **+371,467 字节（+0.35 MB，+0.011%）** |
| SHA-256 | `D7941D1E292ADA325527AA26612471F96F94EF25924AE3B6A4BDF8DC7E390944` | `D25D74419A08788D171127F7C7E62A883CE37F2B59105094B8DB8AF4ED5C0717` | 不同（内容已更新） |
| 编译时间 | 2026-10-12 09:32:04 | 2026-10-15 23:40:00 | — |
| 内核 onedir | 2026-10-12 09:04（已过期） | 2026-10-15 22:57（含 10-14 源码改动） | 本次已把 `services\consensus_signal.py`、`baseline_surprise.py`、`proactive_outreach.py` 等改动打进去 |
| `dist\LoveAI` 规模 | 4,765 文件 / 1,740,562,194 B（1,659.9 MB） | 4,767 文件 / 1,741,944,851 B（1,661.2 MB） | +2 文件 / +1,382,657 字节 |

体积几乎不变、哈希改变，符合"同口径重新封包、仅内核源码更新"的预期。

---

## 8. 红线与"未改动"核验

| 对象 | 构建前 SHA-256 | 构建后 SHA-256 | 结论 |
|---|---|---|---|
| `LoveAI.spec` | `771AEBD4CE1260F71E0901665A56D1AB4F99664C575E79D04C8B45DBBCF35C0B` | 同左 | **未改动** ✔ |
| `tools\installer\LoveAI.iss` | `9CA8D593FF32FE4750FBAE92D983E26BB90BECBE810EF8CE291E94BE2198CEF1` | 同左 | **未改动** ✔ |
| `services\boost_glide_planner.py` | `3BBE363EA6CBD91D9BB6988ACFDACFF535DF0F317AB9BC6CD2B04EE360E5C701` | 同左 | **红线保持** ✔ |
| `.gitignore` | `53B7466517D6486DAB922685148184B46EEB22426629C0C4380DCC6FA5A75763`（3,209 B） | `613DDA611BA0F299AB5E425B73D415B2EAC27771711967B693EF9E4A97B985B7`（3,762 B） | **被"非本任务的并行进程"改动**，详见第 9 节 |

- `data\loveai.db`：mtime 保持 2026-10-14 22:18:43，SHA-256 未变（见第 6 节）——**零写入** ✔
- E 盘微信文件与解密副本：未访问 ✔
- 未改动任何源码（`services\`、`app\`、`tests\` 等）；本报告与脚本均写在 `logs\pack_r3385\` 与工作目录顶层，未触碰源码。

---

## 9. 需要并行的组织者知悉：构建期间存在并发改动（非本任务所为）

本任务执行期间（22:47–23:40），同一工作树内有**另一个并行任务**在重构仓库（提交 `576593c`，2026-10-15 23:34:42，message：`chore: LoveAI v3 开源快照（隐私脱敏版）`）：

1. **删除**了项目根 `config.json`（现仅余 `config.json.bak` / `config.json.bak_v3ver_20261011`）。
   - 影响评估：**不影响本产物**。该文件在 22:57:17 已被打入 `dist\LoveAI\_internal\config.json`（3,804 B），ISCC 从 `dist\` 读取，未受影响。
2. 将 `scripts\_pack_smoke_*.py` 移入 `backups\github_cleanup_20261016_v2\removed_from_repo_v2\scripts\`（23:31:37）。
   - 影响评估：仅使**任务建议的参考脚本**在仓库内消失；本任务改用自建的有界冒烟脚本完成验证。
3. **修改**了 `.gitignore`（23:33:19，3,209→3,762 B）、重建 `DELIVERY_consensus_north_star.md`、增设 `backups\github_cleanup_20261016_v2\` 等。

**以上改动均非本任务所为**；本任务的写入范围严格限定在 `dist\`（PyInstaller 输出）、`installer_output\`（安装包）、`logs\pack_r3385\`（日志/报告）、`build\`（PyInstaller 缓存）及其 `.bak`。由于 ISCC 的输入（`dist\LoveAI\*`、`desktop-pet-electron\dist\win-unpacked\*`、`dist_bundle\payload\*`）在 22:57 之后未被该并行任务触碰，且安装包在 23:40 已编译完成，**产物未受污染**。

---

## 10. 失败或未完成项

- **无失败项，无未完成项。** 步骤 1–5 全部完成，PyInstaller 与 ISCC 均 exit code 0，ISCC 日志含 `Successful compile`。
- 已知取舍与说明：
  1. 本次仅做**主程序 `dist\LoveAI\LoveAI.exe` 的 GUI 最小启动冒烟**；未执行**安装器端到端安装**（未运行 `LoveAI_Setup_v3.1.0.exe` 做真机安装/卸载回归），原因是本机内存紧张（构建期间空闲低至 ~0.7 GB）且安装会向系统写入 `{autopf}\LoveAI`，属未在本任务授权范围内的动作。此项**明确未做**。
  2. 未重打 Electron 桌宠（复用 `desktop-pet-electron\dist\win-unpacked`，2026-10-12 08:47）、未重跑 `scripts\collect_payload.py`（复用 `dist_bundle\payload`，2026-10-11）——按任务要求。
  3. 冒烟使用了 `dist\LoveAI\LoveAI.exe`；该 exe 的桌宠走 `_internal\desktop-pet-electron` 兜底路径运行正常。

---

## 11. 关键数字速览

```
安装包   : C:\Users\87433\Doubao\chats\2026-10-04\new-chat-28\LoveAI-v2\installer_output\LoveAI_Setup_v3.1.0.exe
           (真实落盘 E:\LoveAI_build\installer\LoveAI_Setup_v3.1.0.exe)
字节数   : 3,366,845,259
SHA-256  : D25D74419A08788D171127F7C7E62A883CE37F2B59105094B8DB8AF4ED5C0717
PyInstaller: exit 0, 639.2 s  (22:47:04 -> 22:57:43)
ISCC       : exit 0, 2380 s   (23:00:20 -> 23:40:00, 自报 Successful compile 2379.063 sec)
dist\LoveAI: 4,767 文件 / 1,741,944,851 字节
冒烟       : 主窗口 t=38,871 ms 出现；真实 data\loveai.db SHA-256 前后一致（未写入）
```

## 12. 日志与产物路径清单

```
logs\pack_r3385\run_pyinstaller.ps1
logs\pack_r3385\pyinstaller.log
logs\pack_r3385\pyinstaller.stdout.log
logs\pack_r3385\pyinstaller.stderr.log
logs\pack_r3385\pyinstaller_meta.txt
logs\pack_r3385\run_iscc.ps1
logs\pack_r3385\iscc.log
logs\pack_r3385\iscc.stdout.log
logs\pack_r3385\iscc.stderr.log
logs\pack_r3385\iscc_meta.txt
logs\pack_r3385\smoke_startup_r3385.py
logs\pack_r3385\smoke.txt
logs\pack_r3385\verify.txt
logs\pack_r3385\final_check.txt / final_check2.txt / cleanup3.txt / newfiles.txt
```
