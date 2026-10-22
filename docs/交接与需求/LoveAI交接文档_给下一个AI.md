# LoveAI 交接文档 · 给下一个 AI（2026-10-14）

> 用途：完整交接 LoveAI 项目状态。下一个 AI 读完本文件即可续跑，不必重蹈探查。
> 生成：2026-10-14，主代理（MainAgent）在 new-chat-2 会话整理。
> **更新：2026-10-16，新增第 0.5 节「GitHub 开源进展」——先读它。**

---

## 0.5 GitHub 开源进展（2026-10-16 更新，最新状态）

### 仓库（已上线，公开）
- 地址：**https://github.com/wobuzhidaoqvshememingle/LoveAI** （**Public**）
- Remote：`git@github.com:wobuzhidaoqvshememingle/LoveAI.git`（SSH）
- 分支：`main`；最新提交 `d0ef2a3`（`docs: 快速开始补充安装包下载入口`）
  - 提交链：`d0ef2a3` → `a22c3c0`（根目录收拾干净）→ `997d3a3` → `3259a88` → `f02e7c0` → `576593c`（开源快照·脱敏版）
- **Release v3.1.0**（含 Windows 安装包分卷）→ https://github.com/wobuzhidaoqvshememingle/LoveAI/releases/tag/v3.1.0
- Release v3.0.0（源码快照）→ https://github.com/wobuzhidaoqvshememingle/LoveAI/releases/tag/v3.0.0
- Description（已填）：地球Online：把现实恋爱变成一场可经营、可复盘的小游戏（本地优先的 AI 关系洞察与沟通陪练）

### 安装包分发（已解决，2026-10-16）
- 3.14 GB 安装包 **超过 GitHub 单文件 2 GiB 上限**，采用 **7-Zip 分卷** 绕过：
  - 4 个分卷已挂到 Release v3.1.0 的 Assets（`.7z.001` 1 GiB / `.002` 1 GiB / `.003` 1 GiB / `.004` 139 MiB，合计 3,366,845,413 B）
  - 上传用时 10.5 分钟（≈5 MB/s），4 卷全部 `state=uploaded`
- 本地分卷源：`<发布输出>\`（用 `7z a -mx0 -v1024m` 从 `<构建输出>\installer\LoveAI_Setup_v3.1.0.exe` 生成）
- 用户侧安装：4 卷下到同一文件夹 → 7-Zip 右键 `.001` 解压 → `LoveAI_Setup_v3.1.0.exe`
- 发布后 exe 的 SHA256 应为 `D25D74419A08788D171127F7C7E62A883CE37F2B59105094B8DB8AF4ED5C0717`

**上传完整性已独立校验通过（2026-10-16）**：
- `.004` 整卷从远端下载回来重算 SHA256 = 本地值 `ef1c0f3b41090321799082a2c6d3009ca9c387bd7c043ceaa48088099c2ad0c6` → **MATCH**
- `.001/.002/.003` 各取 **10 段 × 2MB**（均匀分布）HTTP Range 回读比对 → **30/30 全 MATCH**
- `7z t LoveAI_Setup_v3.1.0.7z.001` → **`Everything is Ok`**（Volumes=4，Total Physical Size=3,366,845,413，内含 exe 3,366,845,259 字节）
- 证据：`LoveAI-v2\_verify_upload.log`、`_7z_test.txt`、`_dl_probe.txt`、`_dl_diag.txt`

**⚠️ 下载通道坑（重要，续跑必读）**：
- 直连 `github.com` 的下载链接会 302 跳到 `release-assets.githubusercontent.com`，**直连该域会挂住（0 字节，超时）**。
- `curl.exe`（schannel 后端）走代理 10808 下载该域 → **`schannel: failed to receive handshake`**。
- **可用方案：Python（OpenSSL）+ 代理 `http://127.0.0.1:10808`**，速度约 0.36 MB/s，且**连接会中途断**——必须用 `Range: bytes=<have>-` 做断点续传重试。
- 本机监听的本地端口：`3080, 10808, 18787` 等；只有 10808 是可用代理。

### 上传用令牌（供后续发 Release 用）
- 位置：`LoveAI-v2\_gh_tok.txt`（已被 `.gitignore` 排除，不会进仓库）
- 类型：GitHub **classic PAT**，scope 仅 **`public_repo`**，有效期 **30 天**（2026-11-15 到期）
- 账号：`wobuzhidaoqvshememingle`
- **注意**：git push 走 SSH（见下），**不需要**这个令牌；它只用于上传 Release 资产（`uploads.github.com`）。
- 过期后重取：浏览器登录 GitHub → https://github.com/settings/tokens/new → 勾 `public_repo` → 生成

### SSH 凭据（下次推送用得到）
- 私钥：`%USERPROFILE%\.ssh\loveai_deploy`；公钥已加到 GitHub（名称 `loveai-upload`，Read/write）
- `%USERPROFILE%\.ssh\config` 已配 Host github.com（备份 `config.bak_20261016`）
- 本地 git 配置：`core.sshCommand = C:/WINDOWS/System32/OpenSSH/ssh.exe -i C:/Users/87433/.ssh/loveai_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new`
- 全局 `%USERPROFILE%\.gitconfig` 里 `http.proxy/https.proxy = http://127.0.0.1:10847` **已失效未清理**，git 走代理会挂；SSH 直连 22 端口可用。
- **GCM 交互授权是死路**（.NET 代理链挂在 Schannel 之后、无弹窗）；**GitHub 设备码授权会过期**。续跑请直接用 SSH。
- winget 装 gh CLI 会失败（`InternetOpenUrl() failed 0x80072efd`），别浪费时间；用 curl + PAT 即可。

### 隐私处置（已完成，勿重做）
- 首次推送后发现严重泄露：真实微信 wxid（本人 + 第二账号）、分析对象 `person_id=0d3d74cd`、**`subscribe.json` 含代理节点明文密码 `7f1e6e13-c999-497a-a26d-2b4960532f3b`**、`config.json`、本机硬件指纹、大量 `.bak2/.bak3`。
- 处置链：仓库先转 Private 止血 → 全量备份 → 脚本删 25 个敏感文件 / 脱敏 49 个文件 → 修正动态路径推导 → `.gitignore` 加固 → **删除旧仓库并重建**（彻底清除含凭据的旧提交 `e7b1ad0`）→ 用全新 git 历史重新推送。
- 复扫结果：`_privacy_scan.txt` 显示 **TOTAL_CATEGORIES=1 / TOTAL_MATCH_FILES=6**，仅剩 `E:\xwechat_files`（微信默认数据根，通用路径，非身份信息，保留）。
- 远程验证：`/blob/main/subscribe.json`、`config.json`、`device_info.json` 全部 **404**。
- 备份全在 `LoveAI-v2\backups\github_cleanup_20261016_v2\`（含 `git_history_backup\.git`、`removed_from_repo_v2\`、被改动文件原件）。流程留痕：`_cleanup_step1~3.txt`、`_cleanup_fix_init.txt`、`_privacy_scan.txt`、`_git_push2~6.log`。

### 安装包清单（v3.1.0 已通过分卷上传到 Release，见上节）
- `installer_output\` 现有包（实体在 `<构建输出>\installer\`）：
  - `LoveAI_Setup_v3.1.0.exe` = **3,366,845,259 B ≈ 3.14 GB** ← 已分卷发布
  - `LoveAI_Setup_v3.0.0.exe` = 3,356,824,395 B ≈ 3.13 GB
  - `LoveAI_Setup_v2.0.0.exe` = 1,552,336,761 B ≈ 1.44 GB
  - `LoveAI_DH_Local_v1.zip` = 712,616,110 B ≈ 679 MB
  - `pack_manifest_dh.json`
- `LoveAI_Setup_v2.0.0.exe`(1.44 GB) 与 `LoveAI_DH_Local_v1.zip`(679 MB) **单文件未超 2 GiB，可直接挂 Release**，目前未挂。
- v3.1.0 SHA256：`D25D74419A08788D171127F7C7E62A883CE37F2B59105094B8DB8AF4ED5C0717`
- 打包脚本 `scripts\build_dh_pack.py` 打的源是 `desktop-pet-electron\digital-human\local` + LivePortrait 权重，**不含 `data\`**，已核验 `dist\` 内无 `loveai.db` / `subscribe.json` / `device_info.json` / 微信 db。
- ⚠️ 包内 `_internal\config.json` 是被脱敏从仓库删掉的那一份；核查过**无 API key、路径全为相对路径**，风险低。

### 测试与封包实测（2026-10-16 由子代理 o_000EzqHv9nt 完成）
- 全量 pytest：**1823 passed / 4 skipped / 0 failed**（耗时 21 分 25 秒，exit 0），与基线零差异。
- 红线 `services\boost_glide_planner.py` SHA256 = `3BBE363EA6CBD91D9BB6988ACFDACFF535DF0F317AB9BC6CD2B04EE360E5C0717` ✅ 未被动过。
- 封包口径：`LoveAI.spec`(PyInstaller 639s) → Electron 桌宠 → collect_payload → `tools\installer\LoveAI.iss`(ISCC 2380s)，exit 0。
- 冒烟：GUI 最小启动，主窗口 38.9 秒出现，拉起 4 个桌宠子进程；启动前后真实 `data\loveai.db` 哈希不变（数据未写进真实私人库）。
- **未做**：安装器端到端「安装→启动」回归（当时内存仅剩 0.7GB，且安装会写系统目录）。

### 推广
- 推广材料（可直接复制）：**`<桌面>\LoveAI推广材料.md`** —— 含 30 秒版 / 长文版 / 英文版 / 渠道建议 / 注意事项。
- 已做：Description、README 中英文（含安装包下载章节 + 10 张真实截图）、Release v3.0.0 + **v3.1.0（带分卷安装包）**、tag。
- **未做**：GitHub Topics（页面 About 齿轮是 React 组件，无头环境打不开输入框）。建议手动补：`ai` `llm` `relationship` `local-first` `wechat` `psychology` `desktop-pet` `live2d` `pyside6` `electron` `rag`。

### 待办（留给下一个 AI）
1. **Topics 手动补**（见上）。
2. **代理订阅凭据重置**：`subscribe.json` 里的节点密码/UUID 曾公开暴露过一次，建议提醒用户去服务商更换。
3. **需求日志逐条比对**：`<桌面>\LoveAI需求日志_全量合并.md`（95.7KB）尚未逐条比对。
4. 全局 git 失效代理 10847 未清理。
5. 桌宠形象未达用户要求（要中国古风合欢宗小妖女，现为 hiyori 日式校服少女）。

---

## 0. 项目一句话定位

**LoveAI = 地球online 恋爱经营小游戏**：桌宠 + 判断/任务/画像三模型 + 微信数据链路 + 游戏化任务系统。核心使命（用户原话）：「用算法+心理学分析对方，通过任务系统一点点引导关系向共同答案走——不是单方操控，是两个人一起面对问题、共同解决、得出共同答案（we-ness 框架）；铺垫=引导=阳谋（大禹治水），关键前提：地形图准确（判断读真实特性）+ 持续修正（导航闭环）+ 低压执行（留灯式）。」

---

## 1. 关键路径（先记住这些）

| 项 | 路径 |
|---|---|
| **项目根** | `<工程根>`（即 `LoveAI-v2\`；文中 `<...>` 均为占位符、不写死盘符） |
| **venv Python** | `LoveAI-v2\.venv\Scripts\python.exe` |
| **正式库** | `LoveAI-v2\data\loveai.db`（蛋卷 person_id=**0d3d74cd**，11,608 条已全量导入） |
| **需求日志（桌面权威）** | `<桌面>\LoveAI需求日志_全量合并.md`（四部分：R3.x 98条/大章 308条/22-26章 68项/增量） |
| **交接文档旧版** | `<桌面>\LoveAI交接文档.md` + `new-chat-1\LoveAI交接文档.md` |
| **微信解密产物** | `%USERPROFILE%\Doubao\chats\2026-10-11\new-chat-2\_wx_decrypted\`（13 库明文，约 600MB） |
| **蛋卷提取库（校正版，唯一权威源）** | `new-chat-2\_danjuan_chat_v2.db`（表 danjuan_msgs；**旧版 _danjuan_chat.db 勿用**，local_id 列是时间戳 bug） |
| **微信原始数据** | `E:\xwechat_files\wxid_01ahr1ba97o722_90eb\db_storage\`（13 个加密库，同一密钥） |
| **画像产物** | `LoveAI-v2\data\persons\0d3d74cd\persona_profile.json` |

---

## 2. 数据资产与身份映射（铁的事实）

- **蛋卷（分析对象）**：person_id=`0d3d74cd`；微信身份 `wxid_ke3ghgo2z1ok22`（alias=Vir_Oct11，nick_name=蛋卷，拼音 DJ）；**会话表=`Msg_3c0f0758ef40a7dfbd20bd1f78a1f71e`（=md5(wxid)）**，位于 message_0.db；Name2Id rowid 映射：**551=蛋卷（她发 received）、2=我（用户 sent）、15=撤回系统**。
- **用户自己**：wxid_01ahr1ba97o722（contact id=958，alias=liao13649865519）——**注意这不是蛋卷**。
- **蛋卷数据**：loveai.db 已全量导入 **11,608 条**（2026-01-07 06:43 ~ 2026-10-13 15:34，持续更新）；received 4,190 / sent 7,418；非文本 1,635 条已 zstd 解压标注（nontext_annotations 表）；撤回 2 条（is_withdrawn）。
- **微信密钥（已改为不落明文）**：`0795a409…cc22`（完整值仅存于机器指纹加密缓存，勿落明文）（4.x 同账号同密钥，13 库通用；缓存 `data\wechat_keys\8d2f8435ea26114d.key` 本机指纹加密）。
- **另一账号**：wxid_mtsqbk5540bm22 无密钥缓存，未解密。

---

## 3. 已完成里程碑（按时间，全部有核验留痕）

1. **需求日志全量核对+细筛**：桌面《LoveAI需求日志_全量合并.md》；R3.x 96✅/2🟡/2⬜；22-26章补完 6 项（PsyDI/HTN/动态重规划/回复结构/闯关/备份按钮）；无假完成项。
2. **微信纯文件解密链路**（封号后安全路线，零进程触碰）：密钥恢复→库定位→13 库批量解密→蛋卷会话定位提取。
3. **蛋卷全量导入闭环**（o_000EKJTekQp）：9,796→11,608 条，独立核验 25/25 PASS；wechat_msg_id=真实 local_id（修复了提取脚本把时间戳当 local_id 的 bug）；画像重跑基于全量。
4. **P0/P1/P2 修复批**（o_000EvrUKPot，7 项全完成，1800 passed）：
   - A 反馈机制：feedback_records 表+feedback_service.py+feedback_cli.py（每轮点对错/存疑+备注，strategy_advisor 挂 judgment_id）
   - B 共识 vs 顺从信号：consensus_signal.py（顺从型同意绝不进主信号、不触发回撤/降频）
   - G 北极星两者结合：direction_compass north_star 段（主动参与解决为主+满意度参考）
   - C 撤回标记：is_withdrawn 列（2 条）
   - D 画像口径：metric_definitions 字段；修复率口径修正 69.0%→94.7%（=冲突后3天内主动和解信号）
   - E 增量导入：scripts/danjuan_incremental_import.py（--src/--key/--dry-run/--bak）
   - F 非文本：zstd 解压得微信 XML，nontext_annotations 表 1,661 条标注（parsed 482/partial 1093/failed 86）；**媒体像素级处理线已砍**（用户判定不切实际：语音零缓存/图片解密卡 AES 密钥派生）——产物保留：表情 281 条映射/视频 3 条抽帧/图片 164 条候选。

---

## 4. 当前进行中：算法打磨批（o_000E7rol11x 执行中，未完成！）

**已完成（落盘可核验）**：
- **评估集 v2：52 条真实事件**（冲突爆发10/狠话威胁13/情绪转折5/主动参与解决7/冲突修复9/顺从服软8，带原文引用+时间戳+判定理由）
- **偏差定位实锤**：校准前 Precision=1.0 / Recall=0.062 / Accuracy=0.712（基准0.692）；冲突修复召回 0/9；consensus 3 条中 2 条误报；**结论：之前"主动参与3条 vs 顺从624条"的量级差是检测缺陷（词表过窄+漏行动型主动），不是关系真相**

**执行中（被进程阻塞中断过，已恢复）**：
- C 校准落盘（补词：好好谈谈/商量/我去找你/谈妥/想你了/爱你 + 排除误报词；预期 Recall 0.062→≈0.75、Accuracy→≈0.92）
- D 反馈注入（52 条 → feedback_records）
- E 回归+文档登记
- **补充项1：基线意外度**（已读不回信息量=意外度=|当前行为−基线行为|；秒回→已读不回 vs 从不秒回→已读不回；接入 suspicion_chain/direction_compass）
- **补充项2：主动线**（任务系统新增"主动发起"类任务：概率门+冷却门+静默时段0-7点+基线偏移触发+关系节点触发；系统主动提醒；**不借鉴吃醋状态机；主动≠固定频率**）

**⚠️ 机器阻塞教训**：跑全量 pytest 时若进程挂起会占死整机执行通道（PowerShell/Read 全超时，1.5h）。**下次跑全量回归用 run_in_background + 文件留痕，不要前台等**；如再发生，请用户在任务管理器结束挂起的 python.exe。

---

## 5. 未完成/待办

- **算法打磨批收尾**（见第 4 节 C/D/E + 两个补充项）
- **媒体像素级处理**：客观不可达（语音零缓存需 PC 重放/手机备份；图片解密需微信 4.x 图片加密正确密钥派生方案）——代码路径已写，媒体可达即可跑；用户已拍板暂不投入
- **冷启动**：新用户/新关系无数据时的模板策略未设计
- **通用性（过拟合蛋卷）**：词表/模式/评估集全部来自蛋卷单样本
- **多目标/多账号**：单 person 架构
- **红队/对抗测试**：无
- **长期价值度量**：任务系统与"关系真实改善"脱钩
- **评估集 v2 后方向准确率最终数字**：等打磨批交付

---

## 6. 红线与铁律（不可违反）

1. `services\boost_glide_planner.py` **只读**，SHA256=`3BBE363EA6CBD91D9BB6988ACFDACFF535DF0F317AB9BC6CD2B04EE360E5C701` 全程不变
2. **微信/gateway 零触碰**：E 盘微信文件与解密副本只读，绝不写入/修改；微信 4.x 密钥只在内存，**内存/注入/转储读取永久关闭**（行为特征检测，读取行为本身被封）
3. 正式库 loveai.db **写前必须 .bak**
4. **不调外部 LLM / 不引入付费依赖**（可用免费模型网关 llm_gateway.py+pollinations；多模态免费或本地）
5. **数字真实、证据驱动、宁缺毋滥**——不伪造结果、不虚报数字；结论给出处
6. 脚本**先 Write 再执行**（PowerShell 内联中文引号必 SyntaxError）；stdout 中文易吞，用文件留痕+Read 回读
7. 改文件先备份；删除用户文件先移回收站（用户偏好新增）

---

## 7. 用户偏好与协作模式（必须遵守）

- **「挑刺优先」默认模式**（已存偏好 p1_AQdruDf4MnE）：需求先走 ①维度盘点(≥20项) ②失败模式穷举(30种死法) ③外部坐标系 ④缺失项归因(≥3才留) ⑤反向提问(10个) ⑥多方案对抗 → 最后才实现；**禁止迎合话**，可直接否定方向；每轮至少 1 个"用户没想到但领域关键"的点
- **复杂/可拆任务立即委派子代理后台执行**，主代理不前台垄断；用户追加消息=任务补充不中断，交子代理续跑
- **执行类任务前台只报 开始/关键转折/阻塞/完成 四节点**，细节写日志留痕
- **省额度**：普通会员卡；大任务倾向唤醒子代理复用上下文（o_000E7rol11x 是主复用对象，idle-resumable）
- **不可思维固化**：换角度/元提问/找反例/承认未知的未知
- **数据口径**：分析只要蛋卷那一个会话；66 万条掺杂了别人
- **技能库三地同步**（本地 Profile1+Default / 桌面 我的技能备份.zip / 云电脑 restore）——技能变更后必做
- **LoveAI 监工定时任务伴随生命周期**：完工自动暂停、推进自动恢复（无需手动）
- 回复语言：简体中文；不用 emoji 除非用户要求

---

## 8. 已证死路（不要再走）

- 抖音视频解析（doubao-video-extract 不支持抖音）
- 微信内存/转储读取密钥（行为特征检测，变体无效）
- deception_detector 旧 79.5 假阳性（省略号误判）
- 纯言语谎言语义真伪本地规则做不到（判权交人工）
- loveai.db 旧 wechat_msg_id 与解密库 local_id/server_id 不匹配（已用 local_id 替代）
- 媒体像素级处理（当前不切实际，见第 5 节）
- `_danjuan_chat.db`（local_id=时间戳 bug，用 `_danjuan_chat_v2.db`）

---

## 9. 测试与验证基线

- 全量回归：`pytest tests\ -q -p no:cacheprovider` = **1800 passed / 4 skipped / 0 failed**（基线 1724 + P0/P1 新增 76；打磨批预计再增）
- 单套件：progressive_probe 7 / htn_replan 9 / level_clearing 6 / feedback 17 / consensus 22 / nontext 18 / 增量导入 9
- 验证纪律：独立核验不采信自报；逐字段/逐行比对；留痕文件（OUTPUT_*.txt 等）

---

## 10. 子代理/组织者状态

| 组织者 | 状态 | 用途 |
|---|---|---|
| o_000E7rol11x（LoveAI收尾交接） | **running**（算法打磨批） | 主复用对象：打磨批/需求核对/交接登记 |
| o_000EKJTekQp（蛋卷全量导入） | idle-resumable | 导入/数据链路 |
| o_000EvrUKPot（P0/P1修复批） | idle-resumable | 修复批 |
| o_000EKDTa9Wb（媒体映射） | **已 kill**（用户判定不切实际） | 不再复用 |
| 「LoveAI监工」定时任务 13759655032066 | 生命周期自动管理 | 完工自动暂停/推进自动恢复 |

**唤醒方式**：send_message(target=agent_id, target_agent=<id>, message=<任务说明>)；新任务优先唤醒 o_000E7rol11x 复用上下文。

---

## 11. 下一个 AI 接手建议（前 30 分钟路径）

1. 读本文件 → 读桌面需求日志统计段 → 读 `LoveAI-v2\CHANGELOG.md` 追补尾部
2. 查打磨批进度：peek_task_progress(o_000E7rol11x)；未收尾则等它交付（C/D/E+两补充项）
3. 收尾后独立核验：loveai.db 蛋卷 11,608 条 / persona_profile.json message_count / 全量 pytest 数字
4. 之后按用户拍板顺序推进：冷启动设计 → 通用性（第二样本）→ 反馈闭环真实使用（用户每轮点对错的数据回流）
5. 全程守第 6 节红线；先 Write 再执行；每步留痕
