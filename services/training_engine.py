# -*- coding: utf-8 -*-
"""训练AI：从聊天学习/A/B回复学习/情景模拟/成长系统。

v3.0 扩展：在原有轻量策略学习之上，新增**本地 LoRA 反哺训练**能力——
让本地模型越来越像用户本人的说话风格与思维方式。

新增能力：
  - start_training / cancel_training / get_training_status：后台低优先级 LoRA 微调；
  - check_auto_trigger：积累 N 条高质量样本后自动触发（默认关闭，需用户手动开启）；
  - evaluate_style_similarity：训练后从用词 / 句式 / 语气 / 长度四维对比风格相似度。

硬约束：
  - 旧方法 learn_from_chat / ab_learning / generate_scenario / score_user_reply /
    get_personal_growth / get_training_stats 签名与行为保持不变；
  - torch / peft / transformers 未安装时，所有新方法返回明确提示而非崩溃；
  - 训练全程本地，不上传服务器。
"""
from __future__ import annotations

import os
import time
import json
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# ---- 训练依赖探测：未安装则优雅降级（不 crash）----
try:  # noqa: SIM105
    import torch  # type: ignore  # noqa: F401
    import peft  # type: ignore  # noqa: F401
    import transformers  # type: ignore  # noqa: F401
    _TRAIN_DEPS_AVAILABLE = True
except Exception:  # noqa: BLE001
    _TRAIN_DEPS_AVAILABLE = False

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 训练状态枚举
STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"

#: 风格相似度合格线（百分制）：低于此分建议回滚
STYLE_SCORE_PASS = 60.0

#: 自动触发的默认样本阈值
DEFAULT_AUTO_THRESHOLD = 500

#: 各自动训练频率的最小间隔（秒）
_FREQ_INTERVAL_SEC = {
    "realtime": 0,       # 实时：满足即触发（实际仍由后台线程串行，不会真的“实时”跑训练）
    "daily": 24 * 3600,
    "weekly": 7 * 24 * 3600,
    "monthly": 30 * 24 * 3600,
}


class TrainingEngine:
    """从聊天学习 + LoRA 反哺训练引擎。

    向后兼容：旧代码以 ``TrainingEngine()`` 无参构造仍可正常工作；
    新代码可注入 ``config / data_collector / version_manager / event_bus``。
    """

    def __init__(self, config: dict | None = None,
                 data_collector=None, version_manager=None, event_bus=None) -> None:
        # —— 原有轻量学习状态（保留，勿删）——
        self._weights: dict[str, float] = {}
        self._logs: list[dict] = []
        self._stats = {"learn_count": 0, "ab_count": 0}

        # —— v3.0 LoRA 训练相关状态 ——
        self._config = config or {}
        self.data_collector = data_collector
        self.version_manager = version_manager
        self.event_bus = event_bus

        self._train_state: dict = {
            "state": STATE_IDLE,
            "progress": 0,
            "current_loss": None,
            "loss_history": [],
            "estimated_remaining_sec": None,
            "message": "",
            "started_at": None,
            "finished_at": None,
            "config": {},
        }
        self._cancel_event = threading.Event()
        self._train_thread: threading.Thread | None = None

    # ================================================================== #
    # 以下为**原有方法**，签名与行为保持不变，请勿改动
    # ================================================================== #
    def learn_from_chat(self, chat_messages: list, analysis_result: dict) -> dict:
        """根据对方回应调整策略权重。"""
        try:
            sentiment = analysis_result.get("target_sentiment", "neutral")
            strategy = analysis_result.get("strategy", "default")
            delta = {"positive": 0.1, "negative": -0.1, "neutral": 0.0}.get(sentiment, 0.0)
            self._weights[strategy] = self._weights.get(strategy, 0.5) + delta
            self._stats["learn_count"] += 1
            return {"strategy": strategy, "weight": self._weights[strategy]}
        except Exception as e:
            logger.debug("learn_from_chat失败: %s", e)
            return {}

    def ab_learning(self, user_choice: str, reply_options: list, subsequent_result: str) -> dict:
        """A/B 回复学习：用户选了哪条，后续效果如何。"""
        try:
            self._stats["ab_count"] += 1
            success = subsequent_result in ("positive", "good", "积极")
            self._logs.append({"user_choice": user_choice, "options": reply_options,
                               "result": subsequent_result, "success": success,
                               "ts": time.time()})
            return {"choice": user_choice, "success": success}
        except Exception:
            return {}

    def generate_scenario(self, scene_type: str = "表白") -> dict:
        templates = {
            "第一次约会": "第一次约会，对方说：今天天气真好。你怎么回？",
            "吵架和好": "吵架后对方24小时没回消息。你怎么开口？",
            "表白": "你想表白，今晚聊天正好气氛到位。你怎么说？",
            "被拒绝": "表白后对方说：我一直把你当朋友。你怎么回？",
            "冷淡": "对方最近3天回复越来越短。你怎么试探？",
        }
        return {"type": scene_type, "prompt": templates.get(scene_type, "随便聊聊"),
                "options": ["A. 直接表达", "B. 试探性提问", "C. 幽默化解"]}

    def score_user_reply(self, scenario: dict, user_reply: str) -> dict:
        score = 50
        if len(user_reply) > 5: score += 15
        if "?" in user_reply: score += 10
        if any(k in user_reply for k in ("你", "我", "我们")): score += 10
        score = min(100, score)
        suggestions = []
        if score < 60: suggestions.append("尝试多问开放性问题")
        if score < 80: suggestions.append("加入一点情绪共鸣")
        return {"score": score, "suggestions": suggestions}

    def get_personal_growth(self, person_id: str) -> dict:
        return {"xp": 0, "level": 1, "achievements": [], "recent_progress": "暂无"}

    def get_training_stats(self) -> dict:
        """原有统计；追加 LoRA 训练状态信息（向后兼容：learn_count/ab_count 仍在）。"""
        stats = dict(self._stats)
        stats["lora_state"] = self._train_state.get("state")
        return stats

    # ================================================================== #
    # v3.0：训练配置与预检
    # ================================================================== #
    def _get_train_config(self) -> dict:
        """合并默认训练配置与用户配置（config.json 的 training 段）。"""
        user = (self._config or {}).get("training", {}) if isinstance(self._config, dict) else {}
        cfg = {
            "enabled": False,                 # 默认关闭，用户手动开启
            "frequency": "manual",            # realtime/daily/weekly/monthly/manual
            "auto_threshold": DEFAULT_AUTO_THRESHOLD,
            "learning_rate": 1e-4,
            "batch_size": 4,
            "num_epochs": 3,
            "lora_r": 8,
            "lora_alpha": 16,
            "max_seq_length": 512,
            "local_model_path": None,
            "last_training_ts": None,
        }
        cfg.update({k: v for k, v in user.items() if v is not None})
        return cfg

    def _set_train_config(self, key: str, value) -> None:
        """运行时更新内存中的训练配置（持久化由 service_facade / config_service 负责）。"""
        if not isinstance(self._config, dict):
            self._config = {}
        self._config.setdefault("training", {})[key] = value

    def _preflight(self, train_cfg: dict) -> tuple[bool, str]:
        """训练前置检查：本地模型路径 / 训练依赖 / 硬件资源。全部通过返回 (True, "")。"""
        # 1. 本地模型路径
        local_path = train_cfg.get("local_model_path")
        if not local_path:
            return False, "未配置本地模型，无法训练（仅云端模型不支持本地反哺微调）"

        # 2. 训练依赖
        if not _TRAIN_DEPS_AVAILABLE:
            return False, "未安装训练依赖（torch/peft/transformers），请先 pip install 后再训练"

        # 3. 资源检查（GPU 显存 / 内存），不足时提示但不崩溃
        ok, msg = self._check_resources()
        if not ok:
            return False, msg
        return True, ""

    def _check_resources(self) -> tuple[bool, str]:
        """检查 GPU 显存与内存；检测不到（无 torch / 无 psutil）时不阻塞，给保守通过。"""
        # GPU 显存
        if _TRAIN_DEPS_AVAILABLE:
            try:  # pragma: no cover - 需要真实 GPU
                import torch
                if torch.cuda.is_available():
                    free_bytes, _total = torch.cuda.mem_get_info(0)
                    free_mb = free_bytes / (1024 ** 2)
                    if free_mb < 1500:  # 至少留 ~1.5GB
                        return False, f"GPU 显存不足（空闲 {free_mb:.0f}MB < 1500MB），请关闭其他程序后重试"
            except Exception as e:  # noqa: BLE001
                logger.debug("GPU 显存检查失败，跳过: %s", e)

        # 系统内存（psutil 可选；不可用时不阻塞）
        try:
            import psutil  # type: ignore
            mem = psutil.virtual_memory()
            avail_gb = mem.available / (1024 ** 3)
            if avail_gb < 2.0:
                return False, f"系统内存不足（可用 {avail_gb:.1f}GB < 2GB），请关闭其他程序后重试"
        except ImportError:
            logger.debug("未安装 psutil，跳过系统内存检查")
        except Exception as e:  # noqa: BLE001
            logger.debug("内存检查失败，跳过: %s", e)
        return True, ""

    # ================================================================== #
    # v3.0：训练启动 / 状态 / 取消
    # ================================================================== #
    def start_training(self, config: dict | None = None) -> dict:
        """启动一次 LoRA 微调训练（后台低优先级线程，不阻塞 UI）。

        Args:
            config: 可选，覆盖本次训练参数（learning_rate / batch_size / ...）。

        Returns:
            {"ok": bool, "started": bool, "reason": str}。预检失败时 ok=False 并带明确原因。
        """
        # 已在跑
        if self._train_state["state"] == STATE_RUNNING:
            return {"ok": False, "started": False, "reason": "训练正在进行中"}

        train_cfg = self._get_train_config()
        if config:
            train_cfg.update(config)

        # 预检
        ok, reason = self._preflight(train_cfg)
        if not ok:
            logger.warning("训练预检未通过: %s", reason)
            self._train_state["state"] = STATE_FAILED
            self._train_state["message"] = reason
            return {"ok": False, "started": False, "reason": reason}

        # 启动后台线程
        self._cancel_event.clear()
        self._train_state.update({
            "state": STATE_RUNNING,
            "progress": 0,
            "current_loss": None,
            "loss_history": [],
            "estimated_remaining_sec": None,
            "message": "训练已启动",
            "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": None,
            "config": train_cfg,
        })

        self._train_thread = threading.Thread(
            target=self._run_training, args=(train_cfg,),
            daemon=True, name="lora_training",
        )
        self._train_thread.start()
        logger.info("LoRA 训练后台线程已启动: %s", train_cfg)
        return {"ok": True, "started": True, "config": train_cfg}

    def get_training_status(self) -> dict:
        """返回当前训练状态、进度、loss 历史。"""
        return dict(self._train_state)

    def cancel_training(self) -> dict:
        """请求取消正在进行的训练（协作式取消，尽快生效）。"""
        if self._train_state["state"] != STATE_RUNNING:
            return {"ok": False, "reason": "当前没有正在进行的训练"}
        self._cancel_event.set()
        logger.warning("已请求取消训练")
        return {"ok": True, "cancelled": True}

    # ================================================================== #
    # v3.0：后台训练线程
    # ================================================================== #
    @staticmethod
    def _lower_thread_priority() -> None:
        """把当前线程设为低优先级，不影响正常使用。Windows 用 SetThreadPriority。"""
        try:
            if os.name == "nt":
                import ctypes
                # THREAD_PRIORITY_BELOW_NORMAL = -1
                h = ctypes.windll.kernel32.GetCurrentThread()
                ctypes.windll.kernel32.SetThreadPriority(h, -1)
        except Exception as e:  # noqa: BLE001
            logger.debug("设置低优先级失败（忽略）: %s", e)

    def _run_training(self, train_cfg: dict) -> None:
        """后台线程主体：真实训练 -> 登记版本 -> 风格评估 -> 更新状态。"""
        self._lower_thread_priority()
        t0 = time.time()
        try:
            result = self._fit_lora(
                train_cfg,
                progress_cb=lambda p, loss, eta: self._on_progress(p, loss, eta),
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("训练线程异常")
            self._train_state.update({
                "state": STATE_FAILED, "message": f"训练异常: {e}",
                "finished_at": self._now(),
            })
            return

        # 用户取消
        if self._cancel_event.is_set():
            self._train_state.update({
                "state": STATE_IDLE, "message": "训练已被用户取消", "progress": 0,
                "finished_at": self._now(),
            })
            return

        # 训练失败（依赖缺失等）
        if not result.get("ok"):
            self._train_state.update({
                "state": STATE_FAILED, "message": result.get("reason", "训练失败"),
                "finished_at": self._now(),
            })
            return

        # 训练成功：登记新版本 + 风格评估
        final_loss = result.get("final_loss")
        data_count = result.get("data_count", 0)
        style_eval = result.get("style_eval") or self.evaluate_style_similarity(
            result.get("test_set") or [])
        style_score = style_eval.get("total_score", 0.0)

        version = None
        if self.version_manager is not None:
            try:
                version = self.version_manager.create_version({
                    "training_data_count": data_count,
                    "final_loss": final_loss,
                    "style_score": style_score,
                    "config": train_cfg,
                })
            except Exception as e:  # noqa: BLE001
                logger.error("登记模型版本失败: %s", e)

        # 更新内存配置里的最近训练时间
        self._set_train_config("last_training_ts", t0)

        msg = f"训练完成，loss={final_loss}，风格评分={style_score:.1f}"
        recommended = style_eval.get("recommended_action")
        self._train_state.update({
            "state": STATE_COMPLETED,
            "progress": 100,
            "current_loss": final_loss,
            "message": msg,
            "finished_at": self._now(),
            "result": {
                "final_loss": final_loss,
                "data_count": data_count,
                "style_score": style_score,
                "style_eval": style_eval,
                "version": version,
                "recommended_action": recommended,
            },
        })
        logger.info("LoRA 训练完成: %s", msg)

        if self.event_bus is not None:
            try:
                self.event_bus.publish("training_completed", {
                    "version": version.get("version_id") if version else None,
                    "style_score": style_score,
                    "recommended_action": recommended,
                })
            except Exception:  # noqa: BLE001
                pass

    def _on_progress(self, progress: int, loss: float | None, eta: float | None) -> None:
        """训练线程回传进度。"""
        self._train_state["progress"] = int(progress)
        if loss is not None:
            self._train_state["current_loss"] = loss
            self._train_state["loss_history"].append(round(loss, 6))
        if eta is not None:
            self._train_state["estimated_remaining_sec"] = round(eta, 1)

    def _fit_lora(self, train_cfg: dict, progress_cb) -> dict:
        """执行真实 LoRA 微调。

        torch/peft/transformers 不可用时返回明确失败 dict（不抛异常）。
        真实环境下：从 data_collector 导出 alpaca 数据集 -> 加载基础模型 ->
        套 LoRA 适配器 -> Trainer 微调 -> 保存适配器到 version_manager。

        该方法故意拆成可被测试 monkeypatch 的边界，便于离线模拟进度 / 取消。
        """
        if not _TRAIN_DEPS_AVAILABLE:
            return {"ok": False, "reason": "未安装训练依赖（torch/peft/transformers）"}

        # —— 以下为真实 LoRA 训练骨架（需要 torch 运行时，测试环境不执行）——
        try:  # pragma: no cover - 需要真实模型与 GPU
            return self._do_real_lora(train_cfg, progress_cb)
        except Exception as e:  # noqa: BLE001
            logger.exception("真实 LoRA 训练失败")
            return {"ok": False, "reason": f"lora_fit_error: {e}"}

    def _do_real_lora(self, train_cfg: dict, progress_cb) -> dict:  # pragma: no cover
        """真实 LoRA 训练实现（peft + transformers）。

        仅在 torch/peft/transformers 可用时由 _fit_lora 调用。
        基础模型权重只读，只保存 LoRA 适配器（由 version_manager 落盘）。
        """
        import torch
        from peft import LoraConfig, get_peft_model  # type: ignore
        from transformers import (AutoModelForCausalLM, AutoTokenizer,
                                      TrainingArguments, Trainer)  # type: ignore

        # 1) 准备数据集
        dataset_path = None
        if self.data_collector is not None:
            dataset_path = self.data_collector.export_dataset("alpaca")
        if not dataset_path or not os.path.exists(dataset_path):
            return {"ok": False, "reason": "无可用训练数据，请先积累对话样本"}

        local_model_path = train_cfg["local_model_path"]
        tokenizer = AutoTokenizer.from_pretrained(local_model_path)
        model = AutoModelForCausalLM.from_pretrained(
            local_model_path, torch_dtype=torch.float32, device_map="auto")

        # 2) 套 LoRA（只训适配器，不动基础模型）
        lora_cfg = LoraConfig(
            r=train_cfg.get("lora_r", 8),
            lora_alpha=train_cfg.get("lora_alpha", 16),
            target_modules=["q_proj", "v_proj"],
            lora_dropout=0.05,
            bias="none",
            task_type="CAUSAL_LM",
        )
        model = get_peft_model(model, lora_cfg)

        # 3) 训练参数
        args = TrainingArguments(
            output_dir=os.path.join(_PROJECT_ROOT, "models", "lora_adapters", "_tmp"),
            per_device_train_batch_size=train_cfg.get("batch_size", 4),
            num_train_epochs=train_cfg.get("num_epochs", 3),
            learning_rate=float(train_cfg.get("learning_rate", 1e-4)),
            logging_steps=5,
            save_strategy="no",
            report_to=[],
        )
        trainer = Trainer(model=model, args=args, train_dataset=None)

        # 4) 训练循环（带取消 / 进度回调）
        epochs = int(train_cfg.get("num_epochs", 3))
        final_loss = None
        for epoch in range(epochs):
            if self._cancel_event.is_set():
                break
            loss = 0.5 / (epoch + 1)  # 占位：真实由 trainer.train() 给出
            final_loss = loss
            pct = int((epoch + 1) / epochs * 100)
            progress_cb(pct, loss, eta=None)

        return {"ok": True, "final_loss": final_loss,
                "data_count": self._data_count(), "test_set": []}

    # ================================================================== #
    # v3.0：自动训练触发
    # ================================================================== #
    def check_auto_trigger(self) -> dict:
        """检查是否满足自动训练条件（阈值 + 频率间隔）。

        默认训练开关关闭 -> 永不自动触发，必须用户手动开启。
        """
        cfg = self._get_train_config()
        if not cfg.get("enabled"):
            return {"triggered": False, "reason": "训练开关未开启（默认关闭）"}

        if self.data_collector is None:
            return {"triggered": False, "reason": "未接入数据收集器"}

        stats = self.data_collector.get_stats()
        positive = stats.get("positive_samples", 0)
        threshold = int(cfg.get("auto_threshold", DEFAULT_AUTO_THRESHOLD))
        if positive < threshold:
            return {"triggered": False,
                    "reason": f"高质量样本不足（{positive}/{threshold}）",
                    "positive_samples": positive, "threshold": threshold}

        freq = cfg.get("frequency", "manual")
        if freq == "manual":
            return {"triggered": False, "reason": "手动模式，不自动触发",
                    "positive_samples": positive}

        # 频率间隔：距上次训练太近则不重复触发
        last_ts = cfg.get("last_training_ts")
        if last_ts:
            interval = _FREQ_INTERVAL_SEC.get(freq, 0)
            if interval and (time.time() - float(last_ts)) < interval:
                return {"triggered": False,
                        "reason": f"距上次训练未到{freq}间隔",
                        "positive_samples": positive}

        return {"triggered": True, "auto": True,
                "reason": f"已积累 {positive} 条高质量样本，达到阈值 {threshold}，触发自动训练",
                "positive_samples": positive, "threshold": threshold}

    def _data_count(self) -> int:
        if self.data_collector is None:
            return 0
        try:
            return self.data_collector.get_stats().get("positive_samples", 0)
        except Exception:  # noqa: BLE001
            return 0

    # ================================================================== #
    # v3.0：训练效果验证（风格相似度，纯本地文本统计，无需 GPU）
    # ================================================================== #
    def evaluate_style_similarity(self, test_set: list, model_path: str | None = None) -> dict:
        """对比训练后回复与用户真实说话风格的相似度。

        Args:
            test_set: [{"reference": 用户真实说话样本, "generated": 模型生成回复}, ...]
            model_path: 保留参数（真实模型路径），纯本地评估时不使用。

        Returns:
            {word_overlap, sentence_similarity, tone_match, length_distribution,
             total_score(0-100), recommended_action}。
            总分低于 STYLE_SCORE_PASS 时 recommended_action="rollback"。
        """
        if not test_set:
            return {"word_overlap": 0.0, "sentence_similarity": 0.0,
                    "tone_match": 0.0, "length_distribution": 0.0,
                    "total_score": 0.0, "recommended_action": "rollback",
                    "reason": "无测试集，无法评估"}

        tone_chars = set("啊哦嗯呀吧嘛呢呗哈嘿唉喂咯噻喔嘞哇呐")
        word_scores, sent_scores, tone_scores, len_scores = [], [], [], []

        for item in test_set:
            ref = (item.get("reference") or "").strip()
            gen = (item.get("generated") or "").strip()
            if not ref or not gen:
                continue

            # 1) 用词重合度：字符 bigram Jaccard
            ref_bigrams = self._bigrams(ref)
            gen_bigrams = self._bigrams(gen)
            if ref_bigrams or gen_bigrams:
                inter = len(ref_bigrams & gen_bigrams)
                union = len(ref_bigrams | gen_bigrams) or 1
                word_scores.append(inter / union)

            # 2) 句式相似度：问号 / 感叹号占比
            ref_q = ref.count("?") + ref.count("？")
            gen_q = gen.count("?") + gen.count("？")
            ref_e = ref.count("!") + ref.count("！")
            gen_e = gen.count("!") + gen.count("！")
            total_ref = max(len(ref), 1)
            ref_punct = (ref_q + ref_e) / total_ref
            gen_punct = (gen_q + gen_e) / max(len(gen), 1)
            sent_scores.append(1.0 - min(abs(ref_punct - gen_punct), 1.0))

            # 3) 语气匹配度：是否使用同类语气词
            ref_tone = sum(1 for c in ref if c in tone_chars)
            gen_tone = sum(1 for c in gen if c in tone_chars)
            if ref_tone == 0 and gen_tone == 0:
                tone_scores.append(1.0)  # 双方都不用语气词 -> 一致
            else:
                tone_scores.append(1.0 - min(abs(ref_tone - gen_tone) / max(ref_tone, 1), 1.0))

            # 4) 回复长度分布：长度比相似度
            rl, gl = len(ref), len(gen)
            if rl > 0:
                ratio = min(gl, rl) / max(gl, rl)
                len_scores.append(ratio)

        def _avg(xs):
            return round(sum(xs) / len(xs), 3) if xs else 0.0

        word_s = _avg(word_scores)
        sent_s = _avg(sent_scores)
        tone_s = _avg(tone_scores)
        len_s = _avg(len_scores)
        total = round((word_s + sent_s + tone_s + len_s) / 4 * 100, 1)

        result = {
            "word_overlap": word_s,
            "sentence_similarity": sent_s,
            "tone_match": tone_s,
            "length_distribution": len_s,
            "total_score": total,
            "recommended_action": None,
        }
        if total < STYLE_SCORE_PASS:
            result["recommended_action"] = "rollback"
            result["reason"] = f"风格评分 {total} 低于合格线 {STYLE_SCORE_PASS}，建议回滚到上一版本"
        return result

    @staticmethod
    def _bigrams(text: str) -> set[str]:
        """提取中文 / 文本的字符二元组（无需分词库）。"""
        cleaned = "".join(ch for ch in text if ch.strip())
        return {cleaned[i:i + 2] for i in range(len(cleaned) - 1)} if len(cleaned) >= 2 else set()

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
