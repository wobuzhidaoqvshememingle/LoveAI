# 参与 LoveAI 贡献指南

首先感谢你愿意花时间参与 LoveAI！本文件说明如何搭建开发环境、遵守哪些约定、以及如何把你的修改合入项目。

LoveAI 是 **Windows 桌面端、本地优先** 的「游戏化 AI 恋爱指导助手」。贡献前请先阅读根目录 `README.md` 了解项目定位与边界，并阅读 `docs/QUICKSTART.md` 确认你能在本机把项目跑起来。

---

## 1. 你可以贡献什么

- **代码**：修复 bug、新增服务模块、改善桌面端 UI、增强网关与桌宠。
- **文档**：补全 `docs/` 下的说明、修正错别字、补充使用示例。
- **知识库**：`knowledge/` 目录（心理学 / 法学 / 经济学 / 沟通学等学科资料，当前 393 篇 Markdown）。
- **测试**：补充边界用例、把慢测试标注清楚。
- **反馈**：在 Issue 区提交复现步骤明确的 bug 报告或功能建议。

> 注意：本项目含情感咨询相关能力，但**不替用户做道德决策**。涉及自伤、暴力、违法的请求，代码与文档都必须保持明确拒绝 / 引导求助的立场，不得教唆。

---

## 2. 开发环境搭建

### 2.1 前置要求

- Windows 10 / 11（x64）
- Python **3.11 或 3.12**（安装时勾选 *Add python.exe to PATH*；当前开发机的 venv 为更新的 Python 版本也可工作，但以 3.11/3.12 为准发布）
- Node.js **18+**（仅 Electron 桌宠 `desktop-pet-electron` 需要）
- Git

### 2.2 安装依赖（PowerShell）

在仓库根目录 `$ROOT` 下执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r gateway\requirements.txt
Push-Location desktop-pet-electron; npm install; Pop-Location
```

### 2.3 启动三个组件

```powershell
# 终端 A：多模型聚合网关（FastAPI，监听 127.0.0.1:8787）
Push-Location gateway
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8787
Pop-Location

# 终端 B：主程序（PySide6 桌面端，会自动尝试拉起桌宠）
.\.venv\Scripts\python.exe .\main.py

# 终端 C（可选，单独跑 Electron 桌宠，WebSocket 连主程序 9876）
Push-Location desktop-pet-electron; npm start; Pop-Location
```

- 网关控制台：<http://127.0.0.1:8787/>
- OpenAI 兼容基址：`http://127.0.0.1:8787/v1`
- 本地模型（不随仓库分发，体积大）：`.\.venv\Scripts\python.exe .\tools\download_models.py`

---

## 3. 目录与分层约定

```
LoveAI-v2/
├── main.py                  # 主程序入口（PySide6 原生 QWidget）
├── app/                     # 应用层：窗口、托盘、快捷键、桌宠 WebSocket 服务、生命周期
│   └── widgets/             # 原生 QWidget 窗口与控件
├── services/                # 服务层：纯 Python 业务模块（100+ 个）
├── models/                  # 数据层：SQLAlchemy 模型 / 建表
├── gateway/                 # 组件二：FastAPI 多模型聚合网关（端口 8787）
│   └── app/
├── desktop-pet-electron/    # 组件三：Electron 桌宠（Live2D + PixiJS，WS 9876）
│   ├── electron/
│   └── renderer/
├── knowledge/               # 本地知识库 Markdown（RAG 检索源）
├── prompts/                 # LLM Prompt 模板
├── plugins/ skills/         # 插件 / 技能扩展（隔离加载）
└── tests/                   # pytest 测试
```

分层铁律：

1. **`services/` 是纯 Python 服务层，禁止 import PySide6**（包括 `PySide6.QtCore` 的信号槽）。确需类型标注时，放在 `if TYPE_CHECKING:` 块里，运行时不产生依赖。
2. **方向单向依赖**：`app/`（UI / 应用层）可以 import `services/`；`services/` 不得反向依赖 `app/` 或任何 UI 控件。
3. **`gateway/` 独立成层**：FastAPI 应用在 `gateway/app/`，不 import 主程序的 `app/` 或 `services/`；它有自己的 `requirements.txt` 与 `pytest.ini`。
4. **`desktop-pet-electron/` 是独立 Node 工程**：只通过 WebSocket（9876）与主程序通信，不共享 Python 代码。
5. 数据库访问统一走 `models/` 的 SQLAlchemy 模型；SQL 一律用**命名参数**（`:name`），禁止字符串拼接 SQL。

---

## 4. 代码规范

- **命名**：Python 模块 / 函数 / 变量用 `snake_case`，类用 `PascalCase`，常量用 `UPPER_SNAKE_CASE`。
- **类型注解**：函数签名必须标注参数与返回值类型；公共服务方法建议补 docstring（一句话说明做什么、副作用是什么）。
- **服务不依赖 UI**：新增服务放 `services/`，业务逻辑能在无 QApplication 的环境下被 `import` 并单测。
- **懒加载与降级**：重依赖（本地模型、Chroma、playwright、OCR）按需导入并 try/except 降级，缺依赖不得导致主程序崩溃。
- **新增服务必须配测试**：每新增一个 `services/xxx.py`，在 `tests/` 下配 `test_xxx.py`，覆盖正常路径 + 至少一个降级 / 异常路径。
- **安全敏感逻辑**：`safety_filter` 的任何改动必须保持「自伤 / 暴力 / 违法 → 拒绝或引导求助」的立场，并补对应测试。

---

## 5. 提交信息约定

建议采用 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/) 格式：

```
<type>: <简短描述>

[可选正文：动机 / 取舍]
```

常用 type：

| type | 用途 |
| --- | --- |
| `feat` | 新功能 |
| `fix` | 修复 bug |
| `docs` | 只改文档 |
| `test` | 新增 / 修改测试 |
| `refactor` | 既不加功能也不修 bug 的重构 |
| `chore` | 构建、依赖、杂项 |

示例：`feat: 聊天记录导入器支持 CSV 自动识别说话人`。

---

## 6. 分支与 PR 流程

1. **Fork** 本仓库到自己账号。
2. 从主干切出特性分支：`git checkout -b feat/xxx` 或 `fix/xxx`。
3. 本地**自测通过**：
   ```powershell
   # 全量测试（当前约 1636 个用例）
   .\.venv\Scripts\python.exe -m pytest
   # 快速回归（跳过慢测试）
   .\.venv\Scripts\python.exe -m pytest -m "not slow"
   # 网关改动时，在 gateway/ 目录单独跑网关测试
   Push-Location gateway; ..\.venv\Scripts\python.exe -m pytest; Pop-Location
   ```
4. 推送分支，向 `LoveAI-team/LoveAI` 的主干发起 **Pull Request**。
5. PR 描述建议包含：改了什么、为什么改、怎么测的（贴测试结果）、是否需要用户可见的配置 / 数据迁移。
6. 至少一名维护者 **Code Review** 通过、CI 绿了之后才能合并。

---

## 7. 测试要求

- 测试框架：**pytest**。仓库根目录 `pytest.ini` 已配置。
- 当前基线：**1636 个测试用例**（`pytest --collect-only` 实测）。旧文档里出现过的「242」是历史数字，不要再引用。
- **新增功能必须带测试**，不许只交功能代码不交测试。
- 耗时较长的测试（如真实 RAG 索引、playwright 浏览器）必须标注：
  ```python
  @pytest.mark.slow
  def test_something_slow():
      ...
  ```
  这样 `pytest -m "not slow"` 可以快速回归。
- 测试不得污染正式数据：需要数据库时用临时库 / tmp_path。

---

## 8. 文档与 CHANGELOG

- 任何**用户可感知**的变更（新功能、不兼容改动、重要修复）都要在 `CHANGELOG.md` 里追加一条。
- CHANGELOG 格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/)：按版本倒序，分「新增 / 变更 / 修复」小节，注明日期。
- 文档放 `docs/`；用户上手相关写进 `docs/QUICKSTART.md`。
- 不要在文档里虚构功能或命令——只写代码里真实存在的东西。

---

## 9. 许可证与署名

本项目以 **GNU AGPL-3.0-only** 许可证发布（见根目录 `LICENSE`）。

- 你提交的代码 / 文档即表示你同意以 **AGPL-3.0** 授权给本项目及下游用户。
- 提交前请确认你有权利这样做，且不混入许可证不兼容的第三方代码。
- 第三方资源（如 Live2D Cubism Core）有其自身许可，引用时请保留原始版权声明。

再次感谢！有问题欢迎在 Issue 区交流。
