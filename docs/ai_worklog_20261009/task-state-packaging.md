# LoveAI-v2 封包链路静态审计 — 状态落盘

> 本文件由「封包审计」代理维护。**不要**改 task-state.md（那是并发测试代理的）。
> 本阶段：静态审计 + 三处脚本静态修复；**未执行** collect_payload / PyInstaller / ISCC，
> **未写入** dist_bundle\payload，**未打开/锁定** data\chroma_db。

## 任务目标
对 LoveAI-v2「开箱即用」封包链路（LoveAI.spec + tools\installer\LoveAI.iss + scripts\collect_payload.py）
做静态审计与源组件盘点，修复确凿缺陷，产出可直接执行的构建就绪报告。

## 已完成步骤
1. 完整 Read 三个封包脚本，理清三者衔接（spec=PyInstaller onedir；iss=Inno 安装器按勾选分发；collect=归集 payload）。
2. 源组件真实盘点（绝对路径/文件数/体积，全部用命令实取，非估算）。
3. venv hiddenimports 存在性核查（find_spec，只读）；services 语音导入均为 try/except 惰性。
4. 读 services\runtime_paths.py 核对运行期落点契约。
5. 备份并修复两处确凿缺陷（先备份固定 .bak 再改，语法校验通过）。

## 当前状态（进度板）
- [x] 步骤1 读三脚本
- [x] 步骤2 源组件盘点
- [x] 步骤3 一致性/缺陷核对
- [x] 步骤4 修复两处明确缺陷（spec icon 路径；collect total 双计）
- [x] 步骤5 落盘本状态文件
- [→] 交付最终构建就绪报告

## 已修复（改前已备份 .bak）
| 文件 | 位置 | 改动 |
|---|---|---|
| LoveAI.spec | EXE( icon ) 约 L199 | icon 由 cwd 相对路径改为 ROOT 绝对路径，避免换目录运行 PyInstaller 时图标判定失效 |
| scripts\collect_payload.py | main() 总计约 L265 | total 排除 ollama_gpu_* 子键，修复 INCLUDE_GPU=1 时 total_mb 把 GPU 子树重复累加 |
- 备份：LoveAI.spec.bak、scripts\collect_payload.py.bak（已覆盖旧 .bak，校验可读）

## 产物路径
- 工程根：<PROJECT_ROOT>
- 已存在（先前构建，本阶段未重建）：dist\LoveAI\（938.5MB）、installer_output\LoveAI_Setup_v2.0.0.exe（1486MB）、dist_bundle\payload\（已归集，manifest 已读）

## 关键发现（详见交付报告）
- 高危（跨脚本契约，待构建期验证）：iss 把预置知识库释放到 {app}\data，而 runtime_paths.knowledge_preset_dir() 指向 _internal\data；精简核心+安装器流程下，应用若按 knowledge_preset_dir() 找预置库将落空。
- 中危：assets\icons\app.ico 缺失（icons 目录为空），spec 已优雅回退 None（不崩），但 exe 用默认图标——需发行方补图标。
- 低危：iss AppId 的 GUID 组含非 hex 字符(LOVEAI)，但 Inno 不校验、可编译，仅不规范。

## 完成标准
- 三个脚本逻辑摘要、盘点表（绝对路径/文件数/体积/OK或缺失）、问题清单（含已修/未修与验证法）、
  可照跑构建步骤、终端用户开箱验收清单、参考项目收获分类——全部写入最终回复。

---

# 【执行阶段：封包全流程 + 真实验证】2026-10-09

> 前置：全量隔离回归已绿（1273 passed），chroma_db 无占用。本阶段真实跑完三件套并终端用户验证。

## 三步真实结果
- ① collect_payload.py：EXITCODE=0。ollama 28文件/72.1MB(CPU,含llama-server.exe)；models_local 46文件/1676.6MB；knowledge 7文件/329.7MB(unified_search.db+chroma_db)；voice STUB。total 2078.5MB。
- ② pyinstaller LoveAI.spec --noconfirm：EXITCODE=0。dist\LoveAI\LoveAI.exe 24.82MB + _internal；整包 857.1MB/4037文件；精简核心不含 ollama_runtime/models_local/data/voice（正确）。
- ③ ISCC LoveAI.iss：EXITCODE=0（942秒）。installer_output\LoveAI_Setup_v2.0.0.exe = 1532.7MB。

## 启动验证
- dev 精简核心：PID 存活、窗口"LoveAI v3.0 - LoveAI"、Responding=True；自动生成 data/logs/ollama_data/backups/models_local。
- 静默全装(/VERYSILENT /TYPE=full 到 <E盘>\Temp\LoveAITest)：退出码0。组件全部物理释放。

## 终端用户冒烟结论
- 知识库命中：{app}\data\unified_search.db 被打开并写大(302.69→343.63MB)、自动生成 rag_index_state.json → 预置库在 {app}\data 命中（契约A实际成立，知识服务走可写目录而非 _internal\data）。
- 便携ollama：ollama.exe+llama-server.exe 物理就位，但**启动后未自动拉起 serve、11434 未监听、无 ollama 进程** → 需应用主动 spawn 或用户触发。
- rag_index_state.json 全新 → 预置库已建索引但状态文件缺失，存在重复INSERT/重复召回风险（待验证）。
- _internal 可写性：loveai.db 与 loveai.log 实际落在 _internal\data / _internal\logs（冻结包目录）。per-user(lowest)安装可写故正常；若改管理员装到 Program Files 会只读失败。
- 卸载：程序文件移除，用户数据按设计保留；临时目录已手动清空，无残留进程。

## 已落地 vs 仍待增强
- 已落地：三件套全绿、精简核心独立启动、全组件物理释放、知识库命中、静默装/卸载闭环。
- 仍待增强：①应用启动自动拉起便携 ollama serve；②rag_index_state.json 预置以避免重复建索引；③loveai.db/log 应落到 install_dir 可写目录而非 _internal（兼容管理员安装）；④app.ico 缺失用默认图标。

---

# 【重跑 + 三处根因复验】2026-10-09 下午

## 三步重跑结果
- ① collect_payload：首次因系统 ollama 托盘(PID23896/23392)占用源 ollama.exe 报 WinError32；连托盘停后重跑 EXITCODE=0。ollama 28/72.1MB、models 46/1676.6MB、knowledge 7/329.7MB、voice STUB，total 2078.5MB。
- ② pyinstaller：EXITCODE=0，Build complete。
- ③ ISCC：EXITCODE=0（1139秒），installer_output\LoveAI_Setup_v2.0.0.exe=1532.7MB。

## 终端用户复验（静默全装 <E盘>\Temp\LoveAITest，/TYPE=full）
- 【重点1 便携ollama】通过但偏慢：应用拉起 安装目录 ollama_runtime\ollama.exe serve，PID=6384、PPID=27988(主程序)，监听127.0.0.1:11434，GET /api/tags HTTP200 返回 {"models":[]}。首次冷启动约30-60s（超过应用内5s轮询阈值，但进程最终驻留并服务）。
- 【重点2 索引幂等】unified_search.db 保持释放时 302.69MB 未增长（修复前首启会涨到343.63MB重复建索引）；rag_index_state.json 生成在 {app}\data。初步证明不重复 INSERT。
- 【重点3 db/logs落点】已修复：loveai.db 落在 {app}\data\loveai.db，_internal\data\loveai.db 已不存在；日志在 {app}\logs\loveai.log。
- 【退出回收】未通过：主程序 CloseMainWindow 后 LoveAI=0，但便携 ollama(PID6384,PPID27988) 仍存活、11434 仍监听——成孤儿。需运行时 application.py 关闭路径修 _stop_portable_ollama。
- 【卸载清理】静默卸载退出码0，程序文件移除；临时目录已手动清空；LoveAI/ollama 进程=0，11434=False。

## 仍待增强（运行时代码，非封包三脚本）
- _stop_portable_ollama 实际未杀死子进程（孤儿）；CloseMainWindow 路径疑似被托盘拦截未走完整 shutdown。
- AuthHelper 初始化失败：__init__() got unexpected keyword argument 'data_dir'（日志 WARNING）。
- 便携 ollama 冷启动>应用内5s轮询阈值，建议延长轮询或后台就绪提示。
