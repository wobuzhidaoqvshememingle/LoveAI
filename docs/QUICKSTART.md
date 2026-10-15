# LoveAI 快速上手（5 分钟跑通）

本指南带你从克隆仓库到第一次看到人物档案。所有命令均在仓库根目录 `$ROOT` 下、使用 **PowerShell** 执行。

> 一句话定位：LoveAI 是 Windows 桌面端、本地优先的「游戏化 AI 恋爱指导助手」。不替你做道德决策，只帮你看懂聊天、生成策略、像打游戏一样经营关系。

---

## 0. 前置要求

| 项 | 要求 |
| --- | --- |
| 操作系统 | Windows 10 / 11（x64） |
| Python | **3.11 或 3.12**（安装时勾选 Add to PATH） |
| Node.js | **18+**（仅 Electron 桌宠需要；不用桌宠可跳过） |
| Git | 任意较新版本 |

---

## 1. 一键安装与启动（Windows，推荐）

1. 克隆仓库后，在根目录双击 **`install.bat`**。
   - 它会：创建 `.venv` → 安装主程序 `requirements.txt` → 安装 `gateway\requirements.txt` → 在 `desktop-pet-electron` 执行 `npm install`。
   - 幂等，可重复运行。
   - **预期结果**：最后看到依赖安装完成的提示；`.venv\` 与 `desktop-pet-electron\node_modules\` 均已生成。
2. 双击 **`start.bat`**。
   - 它会先在 `:8787` 启动网关，再启动主程序；主程序会经 `pet_launcher` 自动拉起桌宠。
   - **预期结果**：浏览器打开 <http://127.0.0.1:8787/> 能看到网关控制台；桌面上弹出 LoveAI 主窗口与（可选）桌宠。
3. 首次打开，在主程序「设置 / 引导」里配置一个可用模型（见下一节）。

> 没有 `.bat` 或想逐行看清每步？用下面的手动方式，效果完全一致。

---

## 2. 手动分步（开发者 / 跨终端）

在 `$ROOT` 下打开 PowerShell：

```powershell
# 1) 建虚拟环境并装依赖
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r gateway\requirements.txt
Push-Location desktop-pet-electron; npm install; Pop-Location

# 2) 终端 A：启动网关（监听 127.0.0.1:8787）
Push-Location gateway
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8787
Pop-Location

# 3) 终端 B：启动主程序（会自动尝试拉起桌宠）
.\.venv\Scripts\python.exe .\main.py

# 4) 终端 C（可选，单独跑桌宠）
Push-Location desktop-pet-electron; npm start; Pop-Location
```

- **预期结果（网关）**：<http://127.0.0.1:8787/> 出现暗色控制台；访问 `http://127.0.0.1:8787/v1/models` 返回模型列表（实测 7 个）；`/admin/stats/summary` 返回 JSON 用量统计。
- **预期结果（主程序）**：PySide6 主窗口打开，暗色 UI，托盘出现 LoveAI 图标。
- 本地模型文件**不随仓库分发**，需要时单独下载：
  ```powershell
  .\.venv\Scripts\python.exe .\tools\download_models.py
  ```

---

## 3. 逐功能上手指引

### 3.1 配置模型 / 网关

1. 保持网关运行（`:8787`）。
2. 浏览器打开 <http://127.0.0.1:8787/>，在仪表盘里添加至少一家模型的 API Key（支持 34 家主流厂商；也可直接用内置免费路由，无需自己买 Key）。
3. 在主程序设置里，把 **OpenAI 兼容基址**指向：
   ```
   http://127.0.0.1:8787/v1
   ```
   若网关未设置 `gateway_api_key`，api_key 随便填一个非空字符串即可。

**预期结果**：主程序里随便发一条测试消息，网关控制台的请求数 / token 数开始增长。

> 不想配云端 Key？三条免费 / 本地路径任选：① 网关仪表盘启用 OpenRouter `:free` / NVIDIA NIM 免费通道；② 本机跑 Ollama 并在网关里加本机端点；③ 用上面的 `download_models.py` 装本地小模型（主程序自带四级降级链：付费云端 → 免费路由 → 本地 GGUF → 本地小模型 → 规则兜底）。

### 3.2 导入聊天记录（不依赖微信密钥）

主程序提供聊天导入器，**不需要微信密钥**：

- 支持纯文本（形如 `我: ……` / `对方: ……` / 带时间戳）、JSON、CSV。
- 自动识别说话人、去重、写入 `persons` + `chat_messages` 表。

**预期结果**：导入后人物列表里出现对方，消息数正确，重复消息不会重复入库。

> 关于微信实时监听：一键读取 / 实时监听从当前主线降级为「迭代 4」计划项（见 CHANGELOG 3.1.0）。现阶段请先用导入通道喂数据，不要等待微信 Hook。

### 3.3 生成人物档案（CHARACTER FILE）

导入十几条以上聊天后，触发一次聊天分析 / 人物侧写。

**预期结果**：生成一份「人物档案」——包含关系阶段标签、心形好感度（0–100）+ 星级、SVG 情绪折线、当前策略卡、情绪基线时间线、近期互动与危险信号归档。

想直接看效果而不喂数据？跑官方 demo：

```powershell
.\.venv\Scripts\python.exe .\tools\run_demo_character_file.py
```

它会输出 `docs/character_file_demo.html`，用浏览器打开即可看到档案页样式。

### 3.4 查看关系阶段与好感度

- **18 维好感度引擎**实时更新、7 天无互动自动衰减、每日快照。
- 关系阶段识别 + 双向投入 / 承诺兑现追踪，并可在关系图谱里查看关键人物影响。

**预期结果**：好感度环形图 / 雷达图随消息更新；关系阶段标签随互动推进变化。

### 3.5 领取游戏化任务与勋章

- **任务引擎**把策略拆成可执行的分步任务（邀约 / 维护 / 修复类限时任务），执行后回来点「完成」判定推进。
- **勋章墙 / 英雄墙**内置 25+ 勋章，随好感度里程碑、任务完成自动解锁。

**预期结果**：完成首轮分析后自动出现 3 个主线任务；达成里程碑时弹出新勋章。

### 3.6 数字分身模拟

在数字分面板里选一条想发的消息，可以：

- `predict_reaction`：预测对方可能的反应；
- `simulate_reply`：模拟回复；
- `compare_options`：A/B 两条回复对比。

后续录入对方真实反应后，复盘引擎会用「预测 vs 实际」滚动校准分身准确率。

**预期结果**：分身给出方向性预测文本；预测会落库，等你回填真实反应后可看到有效度更新。

### 3.7 启用桌宠

- **默认路径**：主程序启动时 `pet_launcher` 自动拉起 Electron 桌宠（打包版优先用 exe，开发态走 `npm start`），WebSocket 端口 **9876**。
- **纯开源路径**：不想要 Live2D（专有许可）就用主程序内置的 **Qt3D 程序化骨骼宠物**，不需要 Node。

**预期结果**：桌面出现透明置顶的小人，会气泡说话、表情随好感度 / 情绪联动。

### 3.8 隐私与老板键

- 聊天库默认 **SQLCipher 加密**存放在 `data/` 下；默认不上传任何匿名数据。
- 一键老板键：**`Ctrl + Alt + H`**，立刻隐藏窗口 / 桌宠；锁屏时桌宠自动隐藏。
- 默认 `auto_send=false`：**不会自动替你发消息**，任何发送都要你确认。

**预期结果**：按 `Ctrl+Alt+H` 后窗口与托盘图标立即隐藏，再按一次恢复。

### 3.9 备份

用主程序「备份」面板（或 `backup_service`）一键把数据打成 zip：含数据库、配置、记忆等，支持列表 / 恢复（恢复有二次确认）。

**预期结果**：`backups/` 下出现带时间戳的 zip；点「恢复」选它即可回灌。

---

## 4. 常见问题排查

| 症状 | 原因 / 解决 |
| --- | --- |
| 网关起不来 | 用手动命令在 `gateway/` 目录里前台跑一次，看报错：`Push-Location gateway; ..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8787; Pop-Location`。最常见是没激活 venv、依赖没装全（补 `pip install -r gateway\requirements.txt`）。 |
| 端口被占（8787 / 9876） | 查占用：`netstat -ano \| findstr :8787`、`netstat -ano \| findstr :9876`；拿到 PID 后 `tasklist /FI "PID eq <PID>"` 确认再 `taskkill /PID <PID> /F`。或在网关启动命令里换 `--port`。 |
| pip 安装失败 | 先 `.\.venv\Scripts\python.exe -m pip install --upgrade pip`；仍失败加 `-i https://pypi.tuna.tsinghua.edu.cn/simple` 换国内源。 |
| npm install 失败 | 删干净重装：`Push-Location desktop-pet-electron; Remove-Item -Recurse -Force node_modules, package-lock.json; npm install; Pop-Location`；或 `npm config set registry https://registry.npmmirror.com`。 |
| 模型报无 Key / 无响应 | ① 网关仪表盘里加 Key 或启用免费路由（OpenRouter `:free` / NVIDIA NIM）；② 本机起 Ollama 并在网关加 `http://127.0.0.1:11434/v1`；③ 跑 `tools\download_models.py` 下本地小模型走 L1/L2 兜底。 |
| 微信聊天读不到 | 当前版本微信实时采集不在主线（迭代 4），请用「导入 txt / JSON / CSV」通道喂数据，不要尝试 Hook。 |
| 桌宠不显示 | ① 确认 `desktop-pet-electron\node_modules` 已装好（`npm install`）；② 单独 `Push-Location desktop-pet-electron; npm start; Pop-Location` 看控制台报错；③ 确认主程序 WS 9876 没被占；④ 实在不行切主程序内置 Qt3D 宠物（不需要 Node）。 |
| 数据库被锁（database is locked） | 同一时刻只允许一个进程写 `data/loveai.db`：关掉其他开着主程序的窗口 / 刚跑完的测试进程；备份前先退出主程序。pytest 用临时库，正常开发不会污染正式数据。 |
| 想跑测试确认环境 | 全量：`.\.venv\Scripts\python.exe -m pytest`（约 1636 个用例）；快速回归：`.\.venv\Scripts\python.exe -m pytest -m "not slow"`。 |

---

## 5. 下一步

- 想改代码？读 [`../CONTRIBUTING.md`](../CONTRIBUTING.md)。
- 行为准则：[`../CODE_OF_CONDUCT.md`](../CODE_OF_CONDUCT.md)。
- 版本历史：[`../CHANGELOG.md`](../CHANGELOG.md)。
