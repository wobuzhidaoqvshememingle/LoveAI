# LoveAI 便携整合清单（U 盘）

> 生成时间：2026-10-18
> 脚本：`LoveAI-v2\_copy_to_usb.ps1`（本文件同级，UTF-8 with BOM）
> 目标：把工程与全部备份/归档/发行包/运行数据整合进 U 盘，使工程后续可直接在 U 盘或任意机器（含网吧机）读取使用。

---

## 一、目标结构

```
<U盘>:\LoveAI\
├── LoveAI-v2\        工程本体（含全部 .bak 备份、历史归档、_inventory）
├── release\          发行安装包（r3386 / r3387 / r3388 三个版本）
├── backups\          数据库备份（VACUUM 前后两份 .db）
├── data\             运行数据目录（原始）
├── legacy\           历史版本 LoveAI-v2.0 最终版
├── extras\           桌宠回滚源与技能临时产物
├── docs\             已改好路径的交接/需求文档 + 本脚本副本
├── redundant\        （仅 -Full 时）验收树 / 构建目录 / 重复安装包
├── MANIFEST.txt      拷贝清单与对账结果
└── _copy_loveai_*.log  robocopy 全量日志
```

---

## 二、核心包清单（实测数字，robocopy 口径，含长路径）

| # | 内容 | 源路径 | 文件数 | 体积 |
|---|---|---|---|---|
| 1 | 工程本体（含全部备份/归档） | `<工程根>` | 58,364 | 37.15 GB |
| 2 | 发行安装包（三版本） | `F:\LoveAI_release` | 4 | 12.61 GB |
| 3 | 数据库备份（两份） | `F:\LoveAI_backups` | 2 | 1.28 GB |
| 4 | 运行数据目录 | `D:\亲密关系智能体` | 3,128 | 9.74 GB |
| 5 | 历史版本 v2.0 最终版 | `F:\LoveAI-v2.0-最终版` | 1,044 | 1.74 GB |
| 6 | 桌宠回滚源与技能临时产物 | `E:\Temp\skill_runs` | 87 | 0.74 GB |
| | **合计** | | **62,629** | **63.26 GB** |

> 口径说明：文件数与体积均取自 `robocopy /L` 汇总行（不受 Windows 260 字符路径限制）。
> 工程本体已扣除 `.venv`、`.venv-liveportrait`、`.pytest_cache` 三项（约 6.34 GB，见第五节）。

---

## 三、冗余包清单（默认不带，加 `-Full` 才带）

| 内容 | 源路径 | 体积 | 性质 |
|---|---|---|---|
| gate4 运行树 | `C:\LoveAI_gate4` | 6.46 GB | 已安装运行副本 |
| gate4 运行树 | `E:\LoveAI_gate4` | 4.92 GB | 已安装运行副本 |
| 验收安装树 verify31 | `D:\LoveAI_verify31` | 5.64 GB | 一次性验收产物 |
| 验收安装树 verify32 | `D:\LoveAI_verify32` | 6.46 GB | 一次性验收产物 |
| 验收安装树 verify33 | `D:\LoveAI_verify33` | 6.43 GB | 一次性验收产物 |
| 验收安装树 verify34 | `D:\LoveAI_verify34` | 6.41 GB | 一次性验收产物 |
| 构建工作目录 | `E:\LoveAI_build` | 28.87 GB | 构建中间产物 |
| 历史归档 | `E:\LoveAI_archive_20261009` | 3.88 GB | 2026-10-09 归档 |
| v3.1.0 分卷包副本 | `E:\LoveAI_release_v31` | 3.21 GB | 与 release 重复 |
| v3.1.0 exe 副本 | `D:\LoveAI_pack31` | 3.21 GB | 与 release 重复 |
| v3.1.0 残卷 | `E:\LoveAI_verify` | 1.53 GB | 分卷不完整 |

> 判定依据：`verify31~34` 为同一安装包的四次验收展开；`gate4` 为已安装副本；`LoveAI_build` 为构建目录；
> 三处 v3.1.0 安装包与 `F:\LoveAI_release` 内容重复。合计约 77 GB。

---

## 四、怎么跑

```powershell
# 1) 只看清单，不拷贝（不必插 U 盘）
powershell -NoProfile -ExecutionPolicy Bypass -File .\_copy_to_usb.ps1 -DryRun

# 2) 正式拷贝（核心包 63.26 GB）
powershell -NoProfile -ExecutionPolicy Bypass -File .\_copy_to_usb.ps1

# 3) 连冗余一起带（约 140 GB）
powershell -NoProfile -ExecutionPolicy Bypass -File .\_copy_to_usb.ps1 -Full

# 4) 盘符不是 G 时
powershell -NoProfile -ExecutionPolicy Bypass -File .\_copy_to_usb.ps1 -Drive H
```

脚本行为：
- 正式拷贝前硬检查 U 盘就绪（`No Media` / 0 字节 / 介质未加载）→ **直接退出，绝不产生半成品**
- 建立目录结构 → 逐项 robocopy → 每项拷完自动对账（源 vs 目标 文件数 + 字节数）
- 写出 `MANIFEST.txt` 与 `_copy_loveai_<时间戳>.log`
- **只新增，不删除任何文件**

---

## 五、换机使用须知（重要）

1. **免安装打包版可直接跑**
   `<U盘>:\LoveAI\LoveAI-v2\dist\LoveAI\LoveAI.exe` 自带运行时，拷到任意 Windows 机器双击即可。

2. **源码版需要重建 Python 环境**
   工程内两个虚拟环境**未随盘**（已排除），因为它们写死了本机运行时路径：
   - `.venv\pyvenv.cfg` → `home = C:\Users\87433\AppData\Local\Doubao\User Data\sandbox_runtime\bases\c98c5042338ed152c6f10ecd8591889f\python`（Python 3.14.7）
   - `.venv-liveportrait\pyvenv.cfg` → `home = ...\bases\d0ace100fdff5b2d247847aca1e3d83b\python`（Python 3.13.13）
   换机后需用目标机 Python 重建环境并重装依赖。

3. **数字人（LivePortrait）有硬件门槛**
   需 CUDA，且可用显存 ≥ 3.2 GB（本机 GTX 1050 4 GB 成功那次 torch 峰值 3041 MB）；
   系统可用内存门槛 2500 MB。**普通网吧机通常跑不动**，2D 桌宠不受影响。

4. **文档路径已改写为可移植形式**
   工程内 `.md` / `.txt` 文档里的本机绝对路径已批量替换为占位符：
   `<工程根>`、`<D盘>`、`<E盘>`、`<F盘>`、`<G盘>`、`<桌面>`、`%USERPROFILE%`、`%ProgramFiles%`、`%SystemRoot%` 等。
   换机阅读时不再指向本机路径。每份被改文件旁都有 `.bak_pathfix2_*` 原始备份。

5. **微信相关路径按用户要求原样保留**
   `E:\xwechat_files\*`、`F:\Weixin\*`、`D:\weflow_src_QUARANTINED_do_not_run`、`F:\AppData_relocated\Roaming_Tencent`
   等路径**未做任何改动**，相关文件也未删除。

6. **源码内仍有硬编码盘符路径（未改）**
   工程内约 495 个 `.py` 含盘符路径（其中 `backups` 170、`tools` 166 为历史归档与工具脚本；
   `services` 10、`gateway` 3 等为核心代码）。**这些未改动**——盲目把代码里的路径换成占位符会直接让程序找不到文件。
   若要让源码版在别的机器跑通，需逐文件改为运行时解析（如 `Path(__file__).parent`），属独立改造任务。

---

## 六、本次文档路径改写记录

| 轮次 | 动作 | 文件数 | 处数 | 备份后缀 |
|---|---|---|---|---|
| 1 | 活跃文档试点 | 3 | — | `.bak_pathfix_20261018_115945` |
| 2 | 全量替换 | 376 | 47,871 | `.bak_pathfix2_20261018_121017` |
| 3 | 占位符统一为中文 | 290 | 46,446 | （沿用第 2 轮备份） |
| 4 | JSON 转义形式补充 | 40 | 275 | （沿用第 2 轮备份） |

- 覆盖范围：工程根下全部 `.md` / `.txt`
- 排除：`.venv`、`.venv-liveportrait`、`.pytest_cache`、`node_modules`、`cherry-studio-src`、`dist`、`dist_bundle`、`installer_output`、`build`、`knowledge`、`__pycache__`、`.git`、`chat_exports`、`__MACOSX`
- 另排除功能性文件：`requirements*`、`*config.json*`、`SHA256SUMS*`、`.env*`（改了会破坏运行）
- 手段：Python **字节级** `bytes.replace`（保持原编码与行尾，不做文本模式重写）
- 备份：**376 份** `.bak_pathfix2_20261018_121017`（第 2 轮之前即原始态，可完整还原）+ 3 份第 1 轮备份
- 校验：逐个比对抗改文件与备份的对应关系 → **376/376 零缺失**

**残留（有意保留）**：微信路径、`C:\Users\%USERNAME%` 类变量形式、probe/日志里的样例路径（`C:\path\to`、`C:\nonexistent*.mp4` 等）。
