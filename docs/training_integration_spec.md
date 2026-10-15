# 本地 AI 反哺训练系统 —— 集成 Spec

> 本文档说明如何把新增的三个服务（`TrainingDataCollector` / `TrainingEngine` /
> `ModelVersionManager`）与已有 `service_facade.py`、`settings_dialog.py`、
> `event_bus.py`、`config.json`、`application.py` 对接。
>
> **原则：不直接修改 `service_facade.py`（205KB）与 `settings_dialog.py`（28KB）的现有逻辑**，
> 只在指定位置**新增调用 / 新增 Tab**，所有新逻辑都封装在三个新服务里，失败即被 try/except 吞掉，
> 不影响现有 989 个测试与主流程。

---

## 0. 新增 / 变更文件清单

| 文件 | 动作 | 说明 |
|---|---|---|
| `services/training_data_collector.py` | 新建 | 数据收集器 |
| `services/model_version_manager.py` | 新建 | LoRA 版本管理 |
| `services/training_engine.py` | 扩展 | 保留旧 6 方法，新增 LoRA 训练 |
| `app/widgets/training_settings_widget.py` | 新建 | 设置面板 QWidget |
| `tests/test_training_feedback.py` | 新建 | 单元测试 |
| `app/service_facade.py` | **按本文新增少量方法/调用** | 见 §1、§2 |
| `app/widgets/settings_dialog.py` | **按本文新增一个 Tab** | 见 §3 |
| `services/event_bus.py` | **按本文新增 2 个事件常量** | 见 §4 |
| `config.json` | **按本文新增 `training` 段** | 见 §5 |
| `application.py`（启动装配处） | **按 §6 初始化三个服务** | 见 §6 |

---

## 1. `service_facade.py` 需要新增的方法

在 `ServiceFacade` 类内（建议放在 `analyze_chat` 附近，约 949 行之后）新增以下薄封装。
它们全部是**纯转发**，内部 try/except，失败不影响主链路。

### 1.1 属性懒加载（与现有 `self._mood` / `self._codex_adapt` 风格一致）

```python
# 文件顶部 import 区新增（建议放在已有 services import 附近）：
from services.training_data_collector import TrainingDataCollector, FEEDBACK_USED
from services.training_engine import TrainingEngine
from services.model_version_manager import ModelVersionManager
```

```python
# 在 ServiceFacade.__init__ 末尾（约 line 28 之后）或懒加载方法中新增：
@property
def _train_collector(self) -> TrainingDataCollector | None:
    return self.services.get("training_data_collector")

@property
def _train_engine(self) -> TrainingEngine | None:
    return self.services.get("training_engine")

@property
def _train_versions(self) -> ModelVersionManager | None:
    return self.services.get("model_version_manager")
```

### 1.2 新增公开方法（签名 + 功能 + 调用关系）

| 方法签名 | 功能 | 内部调用 |
|---|---|---|
| `record_reply_interaction(self, user_msg: str, ai_reply: str, feedback: str = "used", metadata: dict = None) -> dict` | 用户采用/修改/拒绝某条 AI 回复时记录一条训练样本 | `self._train_collector.collect_interaction(...)` |
| `record_reply_choice(self, chosen_reply: str, all_options: list, person_id: str, user_msg: str) -> dict` | 用户从 3 条候选里选了一条发送（= feedback=used），同时走旧的 A/B 学习 | `collector.collect_interaction(feedback="used")` + `self._train_engine.ab_learning(...)` |
| `record_user_correction(self, user_msg: str, corrected_reply: str, person_id: str) -> dict` | 用户没用 AI 回复，自己手动改/重写了一条（feedback=corrected） | `collector.collect_interaction(feedback="corrected", metadata={"modified_reply": corrected_reply})` |
| `get_training_data_stats(self) -> dict` | 返回收集统计（供设置面板/UI 读取） | `collector.get_stats()` |
| `export_training_dataset(self, fmt: str = "alpaca") -> str` | 导出数据集，返回路径 | `collector.export_dataset(fmt)` |
| `clear_training_data(self) -> dict` | 清空数据（UI 已二次确认） | `collector.clear_data()` |
| `start_lora_training(self, overrides: dict = None) -> dict` | 手动/自动触发训练 | `self._train_engine.start_training(overrides)` |
| `get_training_status(self) -> dict` | 训练状态/进度/loss | `self._train_engine.get_training_status()` |
| `cancel_lora_training(self) -> dict` | 取消训练 | `self._train_engine.cancel_training()` |
| `list_model_versions(self) -> list` | 版本列表 | `self._train_versions.list_versions()` |
| `rollback_model_version(self, version_id: str) -> dict` | 回滚 | `self._train_versions.rollback(version_id)` |
| `check_auto_training_trigger(self) -> dict` | 后台周期检查是否该自动训练 | `self._train_engine.check_auto_trigger()` |

**全部方法模板**（以 `record_reply_interaction` 为例，其余照搬此 try/except 骨架）：

```python
def record_reply_interaction(self, user_msg, ai_reply, feedback="used", metadata=None):
    """记录一条人机交互训练样本（本地，不上传）。"""
    try:
        if self._train_collector is None:
            return {"ok": False, "reason": "training_data_collector 未初始化"}
        return self._train_collector.collect_interaction(
            user_msg, ai_reply, feedback_type=feedback, metadata=metadata or {})
    except Exception as e:  # 绝不影响主流程
        logger.debug("记录训练样本失败: %s", e)
        return {"ok": False, "reason": str(e)}
```

---

## 2. `analyze_chat()` 中数据收集的插入点

现状（`service_facade.py` line 930–944）：

```python
def _worker():
    try:
        result = self._run_analysis(chat_text, pid)
        self.signals.analysis_ready.emit(result)
        self._update_affection_from_analysis(pid, chat_text, result)   # <- 现有最后一行
    except Exception as ex:
        ...
```

**插入点**：在 `self._update_affection_from_analysis(...)` **之后**、同一 `_worker` 内，
追加一行（包在独立 try 里，失败静默）：

```python
        self._update_affection_from_analysis(pid, chat_text, result)
        # ↓↓↓ 新增：分析产出回复候选后，登记为待反馈的训练样本（不阻塞）↓↓↓
        self._maybe_collect_reply_candidates(chat_text, pid, result)
```

新增辅助方法（约 line 949 后）：

```python
def _maybe_collect_reply_candidates(self, chat_text, person_id, result):
    """分析产出 AI 回复候选后，先存一条 pending 样本；
    用户真正采用/修改/拒绝时再由 record_reply_choice 补 feedback。
    这里只做“轻量登记”，候选回复作为 ai_reply，feedback 暂记为 used。"""
    try:
        if self._train_collector is None:
            return
        candidates = (result or {}).get("reply_candidates") or \
                     (result or {}).get("data", {}).get("reply_candidates") or []
        if not candidates:
            return
        # 以第一条候选作为默认 ai_reply 先登记（后续用户选择会覆盖 feedback）
        first = candidates[0] if isinstance(candidates[0], str) \
            else (candidates[0].get("text") or candidates[0].get("reply") or "")
        if first:
            self._train_collector.collect_interaction(
                user_msg=chat_text, ai_reply=first,
                feedback_type="used",          # 默认先记 used；真实采用/修改走 record_reply_choice
                metadata={"person_id": person_id, "source": "analyze_chat",
                          "conversation_id": (result or {}).get("conversation_id")})
    except Exception as e:
        logger.debug("自动收集训练样本失败: %s", e)
```

> 说明：真正的 A/B 反馈（用户选了 A 还是 B、是否手改）发生在“发送回复”那一步，
> 应在**发送回复**的业务函数里调用 `record_reply_choice(...)`；`analyze_chat`
> 这里只做最基础的“用户消息 ↔ AI 候选回复”配对登记。

---

## 3. `settings_dialog.py` 新增 Tab「AI 训练」

**不改现有 6 个 Tab 的构建逻辑**，只在 `_build_ui()` 的 `tabs.addTab(...)` 序列末尾追加一行。

现状（`settings_dialog.py` line 49–55）：

```python
tabs.addTab(self._build_llm_tab(), "大模型")
tabs.addTab(self._build_wechat_tab(), "微信")
tabs.addTab(self._build_proxy_tab(), "代理")
tabs.addTab(self._build_plugin_tab(), "插件")
tabs.addTab(self._build_general_tab(), "通用")
tabs.addTab(self._build_privacy_tab(), "隐私与数据")
# ↓↓↓ 新增这一行 ↓↓↓
tabs.addTab(self._build_training_tab(), "AI 训练")
```

新增方法 `_build_training_tab`（直接复用新控件，不重写 UI）：

```python
def _build_training_tab(self):
    # 延迟 import，避免在无训练服务时报错
    try:
        from app.widgets.training_settings_widget import TrainingSettingsWidget
        # facade 可能持有 collector/engine/version_manager，透传进去
        collector = getattr(self._facade, "_train_collector", None) if self._facade else None
        engine = getattr(self._facade, "_train_engine", None) if self._facade else None
        versions = getattr(self._facade, "_train_versions", None) if self._facade else None
        self._training_widget = TrainingSettingsWidget(
            collector=collector, engine=engine, version_manager=versions, parent=self)
        # 控件的 config_changed 信号 -> 转发到 settings_saved（由现有保存流程持久化）
        self._training_widget.config_changed.connect(self._on_training_config_changed)
        return self._training_widget
    except Exception as e:
        logger.warning("AI 训练 Tab 加载失败: %s", e)
        w = QLabel("训练模块暂不可用（未安装依赖）")
        return w

def _on_training_config_changed(self, key, value):
    """把 training.enabled / training.frequency / ... 写进 self._config 待保存。"""
    try:
        # key 形如 "training.enabled"，按点号拆层写入嵌套 dict
        parts = key.split(".")
        node = self._config
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value
    except Exception:
        logger.debug("写入训练配置失败", exc_info=True)
```

> 控件样式已内置与 `settings_dialog.py` 一致的黑金主题（`#1a1a2e` / `#d4af37`），
> 无需再调色板。高风险操作（清空数据、删除版本）控件内部已用 `QMessageBox` 二次确认。

---

## 4. `event_bus.py` 新增事件类型

在现有事件常量区（line 26–41）追加两个字符串常量，并加入 `ALL_EVENTS` 元组。

```python
# 在 MEMORY_UPDATED = "memory_updated" 之后新增：
TRAINING_DATA_COLLECTED = "training_data_collected"
TRAINING_COMPLETED = "training_completed"
```

```python
ALL_EVENTS = (
    EMOTION_UPDATED, PROFILE_UPDATED, AFFECTION_CHANGED, STAGE_CHANGED,
    STRATEGY_CHANGED, TASKS_COMPLETED, FINANCIAL_UPDATED,
    LOSS_AVERSION_UPDATED, TWIN_PREDICTION_UPDATED, MEMORY_UPDATED,
    TRAINING_DATA_COLLECTED, TRAINING_COMPLETED,   # ← 新增
)
```

同时在 `EventBus` 类属性区（line 56–65）暴露：

```python
    TRAINING_DATA_COLLECTED = TRAINING_DATA_COLLECTED
    TRAINING_COMPLETED = TRAINING_COMPLETED
```

**事件负载约定**：
- `training_data_collected`：`{"count": int, "feedback": "used|modified|rejected|corrected"}`
- `training_completed`：`{"version": "v2", "style_score": float, "recommended_action": None|"rollback"}`

UI 可订阅 `training_completed`：若 `recommended_action == "rollback"`，弹窗提示用户是否回滚。

---

## 5. `config.json` 新增配置项结构

在现有 JSON 顶层（与 `llm` / `wechat` / `privacy` 同级）新增 `training` 段：

```json
{
  "training": {
    "enabled": false,
    "frequency": "manual",
    "auto_threshold": 500,
    "learning_rate": 1e-4,
    "batch_size": 4,
    "num_epochs": 3,
    "lora_r": 8,
    "lora_alpha": 16,
    "max_seq_length": 512,
    "local_model_path": "",
    "last_training_ts": null
  }
}
```

字段说明：

| key | 类型 | 默认 | 说明 |
|---|---|---|---|
| `enabled` | bool | **false** | 训练总开关，默认关闭，用户手动开启（硬约束） |
| `frequency` | str | `"manual"` | realtime/daily/weekly/monthly/manual |
| `auto_threshold` | int | `500` | 积累多少条高质量样本后自动触发 |
| `learning_rate` | float | `1e-4` | LoRA 学习率 |
| `batch_size` | int | `4` | 批大小 |
| `num_epochs` | int | `3` | 训练轮数 |
| `lora_r` | int | `8` | LoRA 秩 |
| `lora_alpha` | int | `16` | LoRA alpha |
| `max_seq_length` | int | `512` | 最大序列长度 |
| `local_model_path` | str | `""` | 本地基础模型目录；空则训练功能返回“未配置本地模型” |
| `last_training_ts` | float/null | `null` | 最近一次训练 epoch 时间（自动触发频率间隔用） |

> 注意：`local_model_path` 与 `llm.local.l2.gguf_path` 是**不同**概念——
> 训练需要一个可被 transformers 加载的 HF 格式模型目录；GGUF 推理模型不用于反向微调。

---

## 6. 初始化顺序（`application.py` / 服务装配处）

在组装 `services: dict`（传给 `ServiceFacade(services, signals)` 的那个 dict）时，
按以下顺序新增三个服务。**必须在 `ServiceFacade` 构造之前完成**：

```python
# 伪代码（放在已有的 db / llm / style_learner 初始化之后）：
from services.training_data_collector import TrainingDataCollector
from services.training_engine import TrainingEngine
from services.model_version_manager import ModelVersionManager

# 1) 事件总线（若已有 event_bus 实例则复用）
event_bus = services.get("event_bus")

# 2) 数据收集器（依赖：event_bus）
train_collector = TrainingDataCollector(event_bus=event_bus)

# 3) 版本管理器（基础模型路径只读，从 config 读）
version_mgr = ModelVersionManager(
    base_model_path=config.get("training", {}).get("local_model_path") or None)

# 4) 训练引擎（注入上面三者 + config）
train_engine = TrainingEngine(
    config=config,
    data_collector=train_collector,
    version_manager=version_mgr,
    event_bus=event_bus,
)

# 5) 注册进 services dict，供 facade 懒加载取用
services["training_data_collector"] = train_collector
services["training_engine"] = train_engine
services["model_version_manager"] = version_mgr
```

**顺序约束**：`collector → version_manager → engine`，因为 engine 依赖前两者。

**后台自动触发检查**：在已有的后台调度器（`background` 配置段驱动的周期任务）里，
每个周期追加一行：

```python
# 后台循环中（不阻塞）：
try:
    trig = train_engine.check_auto_trigger()
    if trig.get("triggered"):
        train_engine.start_training()   # 后台线程，立即返回
except Exception:
    logger.debug("自动训练触发检查失败", exc_info=True)
```

---

## 7. 优雅降级对照表（无 torch / 无 GPU / 无本地模型）

| 场景 | 用户看到的行为 |
|---|---|
| 未安装 torch/peft/transformers | 点“立即训练”弹窗提示“未安装训练依赖…”；其余功能（数据收集、版本管理、风格评估）正常 |
| 未配置本地模型路径 | 点训练提示“未配置本地模型，无法训练” |
| GPU 显存 / 内存不足 | 预检返回明确提示，不启动线程 |
| 训练进行中关软件 | daemon 线程随进程结束；版本不会登记，下次重新跑即可 |
| 风格评分 < 60 | 训练结果里 `recommended_action="rollback"`，UI 提示回滚 |

---

## 8. 验证清单（集成后自测）

- [ ] `from services.training_engine import TrainingEngine` 不报错；旧 6 方法签名不变。
- [ ] `TrainingDataCollector` 收集 1 条 used 样本 → `get_stats()["positive_samples"] == 1`。
- [ ] 导出 alpaca / sharegpt 两个 JSON，结构符合格式。
- [ ] `ModelVersionManager.create_version` 后 `list_versions()` 有 1 条；`delete` 当前版本被拒绝。
- [ ] 未配置本地模型时 `start_training` 返回 `ok=False, reason="未配置本地模型..."`。
- [ ] `check_auto_trigger()` 在 `enabled=False` 时永不触发。
- [ ] 设置面板出现「AI 训练」Tab，清空数据有二次确认弹窗。
- [ ] 现有 989 个测试不受影响（新增服务全部 try/except 隔离）。
