# 采集通道实现模式规范（所有适配器必须遵守）

本文件是**已冻结**的模式基线。实现任何新适配器前，先按顺序 Read 下列文件，严格沿用其接口、术语与风格：

- `services/collectors/base.py` —— `Message` / `Availability` / `SourceAdapter`
- `services/collectors/registry.py` —— `MODULE_SPECS`（模块路径与类名的**权威**清单）
- `services/collectors/env.py` —— 真实环境探测（**只能调用，不得改**）
- `services/collectors/vision.py` —— 本地 OCR 与聊天截图解析（**只读**）
- `services/collectors/oracle.py` —— 密钥验密 / 转储扫描（**只读**）
- `services/collectors/store.py` —— 统一入库（**只读**）

**模式范本（照抄结构）**：三个已落地并通过测试的种子适配器
`pc/p1_sqlcipher.py`、`thirdparty/t3_manual.py`、`thirdparty/t4_screenshots.py`，
以及测试 `tests/test_collectors_seed.py`（10 项全绿）。

---

## 1. 适配器契约

每个适配器继承 `SourceAdapter`，以**类属性**给出元数据，并实现两个方法：

```python
class XxxAdapter(SourceAdapter):
    id = "P6_passive_vision"          # 全局唯一，已在 MODULE_SPECS 隐含
    name = "被动只读视觉采集"
    platform = PLAT_PC                # PLAT_PC / PLAT_ANDROID / PLAT_OFFICIAL / PLAT_THIRDPARTY
    requires = frozenset({"vision"})  # root/key/adb/app/vision/admin/device/backup
    risk_level = RISK_LOW             # none/low/medium/high
    default_enabled = True            # 高风险必须 False
    local_runnable = True             # 能否用 fixture 离线自测
    success_prior = 0.7               # 经验先验 0-1
    produces_plaintext = True         # 官方编排 O1/O2 = False

    def available(self) -> Availability:
        # 必须真实探测 env.* / 文件 / 进程 / 模块，禁止硬编码 True
        ...
    def collect(self, **opts) -> List[Message]:
        ...
```

- `available()`：条件满足返回 `ok("理由", **details)`；不满足返回
  `missing("理由", ["如何满足条件1", "条件2"], **details)`。
- `collect()`：返回 `List[Message]`。**不确定的字段一律 `None`，禁止编造**时间戳/wxid。
- `Message` 方向只用 `sent` / `received`；`msg_type` 默认 `text`。
- 高风险通道：`risk_level=RISK_HIGH` 且 `default_enabled=False`；`registry.run`
  会在没有 `confirm_high_risk=True` 时自动拦截，无需自己实现。

## 2. 包装而非重写

优先 `import` 并包装现有能力，不重复造轮子；确需引用先 Read 现状：
`wechat_db_decryptor.py`、`wechat_db_reader.py`、`wechat_key_extractor.py`、
`ocr_service.py`、`chat_importer.py`、`wechat_vision_monitor.py`、
`wechat_windows_mcp.py`、`wechat_operator.py`（**只复用 observe/scroll，绝不复用 send**）、
`free_model_router.py`。**不得修改这些文件的对外接口**；本任务一般不需要改它们。

## 3. 落地证据（硬要求，空壳一律不算）

- **本地可跑**通道：必须在 `services/collectors/fixtures/<你的组>/` 提供样例，
  并在**你专属**的测试文件里有通过的断言，覆盖：可用、产出条数、方向、内容、（可入库）。
- **依赖外部**（Frida/模拟器/商业软件/真机/视觉模型）通道：
  `available()` 必须真实检测；条件不满足时 `missing` 给出可执行引导；
  测试用 fixture 或 monkeypatch 验证"检测逻辑 + 引导文案"，并在条件满足时代码路径可跑。
- 测试文件**只建你自己的一个**：见各组任务分派，命名固定。
- 运行方式（Bash 在本机禁用，只用 PowerShell）：
  `& .venv\Scripts\python.exe -m pytest tests\test_collectors_<组>.py -v`
- 完成后回报：每个适配器的**绝对路径 + 关键行号 + pytest 日志 + 样本输出**。

## 4. 边界（禁止越界）

- **只新建**：你负责的适配器文件、`fixtures/<你的组>/`、你的测试文件。
- **禁止修改**：`base.py / env.py / registry.py / store.py / vision.py / oracle.py /
  __init__.py / ios_reserve.py`，以及其他组的适配器、fixture、测试。
- **禁止触碰**（另一个在途任务所有）：`services/diagnostic_report.py`、
  `services/digital_twin.py`、`services/llm_gateway.py` 及其相关测试。
- **本地优先**：默认不把聊天截图/内容上传第三方；视觉/OCR 走本地或已配置通道，
  不自行逆向桌面 App 凭据、不绕过账号配额。
- **发送永远手动**：任何适配器不得调用发送/群发；只读。
- 若万不得已要改既有文件，先用固定名 `.bak`（无时间戳）备份并验证，再改。

## 5. 风险与合规（须在元数据与引导中体现）

微信 2026-01 已将录音/录屏/截图/辅助服务读取/破解数据库列入违规打击；
模拟器、云设备、高频无障碍 2026 有封号及人工核实不解封实锤。
默认优先零/低风险；注入 hook、模拟器登录、蓝屏/休眠触发、自动遍历 = 高风险，默认禁用。
