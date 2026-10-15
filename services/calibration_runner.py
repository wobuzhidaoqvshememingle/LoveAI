# -*- coding: utf-8 -*-
"""services/calibration_runner.py — 校准闭环演示/批处理包装模块（自有，不改共享源码）。

作用：
- 调用 services.twin_sim_pipeline.TwinSimPipeline 拿到每个场景的预测结局分布 P(Y1..Y7)、
  主类(argmax)、置信度、情绪均值；
- 用一个固定 seed 的"合成真值生成器"从 Q = α·P + (1-α)·style_bias 里抽样模拟实际结局，
  分布与冷启动先验 P 有意不同，用以体现"预测→回填→再校准"的意义；
- 真实计算命中率（argmax 预测 vs 模拟实际）与 ECE（按置信度等宽 10 桶加权）；
- 可选把每条预测/回填写入 services.review_loop.ReviewLoop（临时 SQLite），观察分批回填后
  rolling_validity / calibration 状态随迭代更新；
- 导出 calibration_result.json / calibration_result.md，全程带"合成/模拟回填，非真人真值"横幅。

注意：本模块所有"实际结局"均为合成抽样，不得当作真人命中率；真人回填闭环待真实数据接入。
"""
from __future__ import annotations

import json
import logging
import os
import random
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from services.twin_sim_pipeline import (
    TwinSimPipeline, Y_KEYS, Y_LABELS,
)

# --- 合成真值与预测先验有意不同的"真实偏差"向量（按人格风格） ---
# 这些向量是人为设定的"事后真实分布倾向"，用来制造校准误差，演示闭环；
# 不是任何真人数据。
_STYLE_TRUE_BIAS: Dict[str, Dict[str, float]] = {
    # 安全型：实际比先验更冷淡一点（Y4/Y6 偏高），演示"过度自信于 Y1/Y2"
    "secure":  {"Y1": 0.10, "Y2": 0.20, "Y3": 0.20, "Y4": 0.25, "Y5": 0.05, "Y6": 0.15, "Y7": 0.05},
    # 回避型：实际比先验更暖一点（Y2 偏高），演示"过度悲观"
    "avoid":   {"Y1": 0.15, "Y2": 0.30, "Y3": 0.20, "Y4": 0.15, "Y5": 0.05, "Y6": 0.10, "Y7": 0.05},
    # 焦虑型：实际比先验更冷静一点（Y3/Y4 偏高），演示"过度预测 Y1/Y5"
    "anxious": {"Y1": 0.20, "Y2": 0.15, "Y3": 0.25, "Y4": 0.15, "Y5": 0.10, "Y6": 0.10, "Y7": 0.05},
}

# 把 Y 类映射到 ReviewLoop 的情绪/方向/回复，用于真实写入闭环
_Y_TO_EMOTION: Dict[str, str] = {
    "Y1": "开心", "Y2": "开心", "Y3": "平静",
    "Y4": "冷淡", "Y5": "愤怒", "Y6": "", "Y7": "",
}
_Y_TO_REPLY: Dict[str, str] = {
    "Y1": "好啊，那咱们就这么定了，周末见。",
    "Y2": "嗯，听到了，挺好的。",
    "Y3": "哦，知道了。",
    "Y4": "再说吧，最近挺忙的。",
    "Y5": "你别老这么逼我行不行，我很烦。",
    "Y6": "",   # 已读不回
    "Y7": "",   # 未读
}

# 默认固定场景（每人设若干条我方消息，覆盖 invite/emotion/conflict/daily）
_DEFAULT_SCENARIOS: Dict[str, List[str]] = {
    "t_healthy": [
        "周末要不要一起吃饭？",
        "今天上班好累，心里有点烦。",
        "你最近是不是在躲着我？我有点失望。",
        "给你带了爱喝的那款咖啡。",
    ],
    "t_npd": [
        "周末要不要一起吃饭？",
        "你今天怎么没回我消息？",
        "你到底什么意思？我对你太失望了！",
        "在忙吗，想跟你说个事。",
    ],
    "t_bpd": [
        "周末要不要一起吃饭？",
        "我今天心情特别差，你能陪陪我吗？",
        "你是不是想跟我分手？你说话啊！",
        "对不起，刚才是我语气急了。",
    ],
    "p_slowwarm": [
        "周末一起看纪录片？",
        "昨天是我语气急了，对不起。",
        "给你带了爱喝的那款。",
        "在忙吗，想约你吃个饭。",
    ],
}

_SLOWWARM_OWN_MESSAGES = [
    "你好，嗯，每周跑两三次", "江边绿道，人少", "吃完饭没",
    "楼下便利店买个饭团先垫垫", "这周末我看下排班，明早回你",
]

SYNTHETIC_BANNER = "合成/模拟回填，非真人真值；真人回填闭环待真实数据接入"


@dataclass
class ScenarioPrediction:
    person_id: str
    user_message: str
    style: str
    P: Dict[str, float]
    main_class: str
    confidence: float
    emotion_mean: float
    emotion_range: List[float]
    # 合成真值
    synth_actual: str = ""
    synth_actual_emotion: str = ""
    synth_actual_reply: str = ""
    no_reply: bool = False
    hit: int = 0


@dataclass
class BatchSnapshot:
    batch_index: int
    n: int
    hit_rate: float
    ece: float
    calib_factor: float
    review_loop_validity: Optional[float] = None
    review_loop_band: Optional[str] = None
    review_loop_sim_weight: Optional[float] = None


class CalibrationRunner:
    """校准闭环演示运行器（自有模块，不修改 services.review_loop / twin_sim_pipeline）。"""

    def __init__(self, data_dir: str, seed: int = 20261009,
                 scenarios: Optional[Dict[str, List[str]]] = None,
                 mix_alpha: float = 0.55, n_bins: int = 10,
                 use_review_loop: bool = True,
                 memory_service: Any = None) -> None:
        self.data_dir = data_dir
        self.seed = int(seed)
        self.scenarios = scenarios or dict(_DEFAULT_SCENARIOS)
        self.mix_alpha = float(mix_alpha)
        self.n_bins = int(n_bins)
        self.use_review_loop = bool(use_review_loop)
        self.rng = random.Random(self.seed)
        # R3.3-34：可选的 MemoryService。提供后，校准回灌经
        # guarded_write_calibration()（先过 MemoryGate 去噪）写入记忆，
        # 而不是直接 add_memory。None 时（默认/单测）不写记忆。
        self.memory_service = memory_service
        # 临时 ReviewLoop DB（不落盘到工程库）
        self._tmp_db = None
        self._rl = None

    # ------------------------------------------------------------------ #
    # 公共入口
    # ------------------------------------------------------------------ #
    def run(self) -> Dict[str, Any]:
        preds = self._collect_predictions()
        self._synthesize_truth(preds)
        # 可选：把预测/回填写入 ReviewLoop，按批次观察状态更新
        batch_snaps: List[BatchSnapshot] = []
        if self.use_review_loop:
            self._open_review_loop_db()
            try:
                batch_snaps = self._run_loop_batches(preds)
            finally:
                self._close_review_loop_db()
        else:
            # 纯内存分批快照
            batch_snaps = self._memory_batches(preds)

        agg = self._aggregate(preds, batch_snaps)
        result = self._build_result(preds, batch_snaps, agg)
        return result

    def write_outputs(self, result: Dict[str, Any], out_dir: str) -> Tuple[str, str]:
        os.makedirs(out_dir, exist_ok=True)
        json_path = os.path.join(out_dir, "calibration_result.json")
        md_path = os.path.join(out_dir, "calibration_result.md")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(self._render_md(result))
        return json_path, md_path

    # ------------------------------------------------------------------ #
    # 1) 调 twin_sim_pipeline 取预测
    # ------------------------------------------------------------------ #
    def _collect_predictions(self) -> List[ScenarioPrediction]:
        out: List[ScenarioPrediction] = []
        for pid, msgs in self.scenarios.items():
            if pid == "p_slowwarm":
                pipe = TwinSimPipeline(
                    pid, persona_profile={}, own_messages=_SLOWWARM_OWN_MESSAGES)
            else:
                pipe = TwinSimPipeline(pid, data_dir=self.data_dir)
            state = pipe.new_state()
            for msg in msgs:
                r = pipe.run_turn(pid, user_message=msg,
                                  event={"type": "user_message", "elapsed_minutes": 20},
                                  state=state)
                state = r["state"]
                rd = r["reaction_distribution"]
                P = dict(rd["P"])
                main_class = max(P.items(), key=lambda kv: kv[1])[0]
                out.append(ScenarioPrediction(
                    person_id=pid,
                    user_message=msg,
                    style=pipe.spec["style"],
                    P=P,
                    main_class=main_class,
                    confidence=round(float(P[main_class]), 4),
                    emotion_mean=float(rd["emotion"]["mean"]),
                    emotion_range=[float(x) for x in rd["emotion"]["range"]],
                ))
        return out

    # ------------------------------------------------------------------ #
    # 2) 合成真值生成器（固定 seed，与先验 P 有意不同）
    # ------------------------------------------------------------------ #
    def _synthesize_truth(self, preds: List[ScenarioPrediction]) -> None:
        for p in preds:
            bias = _STYLE_TRUE_BIAS.get(p.style, _STYLE_TRUE_BIAS["secure"])
            # Q = α·P + (1-α)·bias
            q = {k: self.mix_alpha * p.P[k] + (1.0 - self.mix_alpha) * bias.get(k, 0.0)
                 for k in Y_KEYS}
            total = sum(q.values())
            q = {k: v / total for k, v in q.items()}
            # 按累计分布抽样（确定性：rng 由 seed 播种）
            x = self.rng.random()
            acc = 0.0
            picked = Y_KEYS[-1]
            for k in Y_KEYS:
                acc += q[k]
                if x <= acc:
                    picked = k
                    break
            p.synth_actual = picked
            p.synth_actual_emotion = _Y_TO_EMOTION[picked]
            p.synth_actual_reply = _Y_TO_REPLY[picked]
            p.no_reply = picked in ("Y6", "Y7")
            p.hit = 1 if picked == p.main_class else 0

    # ------------------------------------------------------------------ #
    # 3) ReviewLoop 真实闭环（临时 DB）
    # ------------------------------------------------------------------ #
    def _open_review_loop_db(self) -> None:
        # 延迟导入，避免在不使用时拉起 models.database
        from models.database import Database  # noqa: WPS433
        from services.review_loop import ReviewLoop  # noqa: WPS433
        self._tmp_db_dir = tempfile.mkdtemp(prefix="loveai_calib_demo_")
        db_path = os.path.join(self._tmp_db_dir, "calib.db")
        self._tmp_db = Database(db_path)
        self._tmp_db.init_tables()
        # 插入 personas 记录（prediction_records 外键/查询需要）
        for pid in self.scenarios.keys():
            try:
                self._tmp_db.execute(
                    "INSERT OR IGNORE INTO persons (id, nickname, gender) "
                    "VALUES (:id,:n,:g)",
                    {"id": pid, "n": pid, "g": "未知"})
            except Exception:
                pass
        self._rl = ReviewLoop(self._tmp_db)

    def _close_review_loop_db(self) -> None:
        try:
            if self._tmp_db is not None:
                eng = getattr(self._tmp_db, "engine", None)
                if eng is not None:
                    eng.dispose()
        except Exception:
            pass
        self._tmp_db = None
        self._rl = None

    def _run_loop_batches(self, preds: List[ScenarioPrediction],
                          batch_size: int = 4) -> List[BatchSnapshot]:
        snaps: List[BatchSnapshot] = []
        seen: List[ScenarioPrediction] = []
        for i, p in enumerate(preds):
            # 写预测（pending）
            pred_payload = {
                "emotion": p.synth_actual_emotion or "平静",
                "possible_replies": [p.synth_actual_reply or "（无）"],
                "action": p.synth_actual_reply,
                "confidence": p.confidence,
                "main_class": p.main_class,
                "P": p.P,
            }
            self._rl.record_prediction(p.person_id, p.user_message, pred_payload,
                                       turn_ref=f"t{i+1}")
            # 回填（用合成真值）
            actual = ({"no_reply": True} if p.no_reply else
                      {"emotion": p.synth_actual_emotion,
                       "reply": p.synth_actual_reply})
            self._rl.settle_latest(p.person_id, actual, turn_ref=f"t{i+1}")
            seen.append(p)
            # 每 batch_size 条或最后一条，打一次快照（跨所有人设聚合）
            if (i + 1) % batch_size == 0 or (i + 1) == len(preds):
                snaps.append(self._snapshot(i + 1, seen))
                # R3.3-34：本批校准回灌经 MemoryGate 去噪后写入记忆
                self._flush_calibration_to_memory(seen)
        return snaps

    def _flush_calibration_to_memory(self, seen: List[ScenarioPrediction]) -> None:
        """把本批校准观察经 guarded_write_calibration() 写入记忆（不再直接 add_memory）。

        - 未注入 memory_service（默认/单测）→ 空操作；
        - 逐条构造 raw_item（text/source/confidence/timestamp/speaker），
          交给 MemoryService.guarded_write_calibration 先过 MemoryGate 去噪再分层写。
        全程 try/except：回灌失败绝不影响校准主流程。
        """
        if self.memory_service is None:
            return
        try:
            from datetime import datetime as _dt
            items_by_pid: Dict[str, List[dict]] = {}
            for p in seen:
                if p.no_reply:
                    outcome = "已读不回"
                else:
                    outcome = (f"{Y_LABELS.get(p.synth_actual, p.synth_actual)}"
                               f"({p.synth_actual_emotion or '无情绪'})")
                text = (f"校准观察[{p.style}]：我方说「{p.user_message}」→ "
                        f"实际{outcome}；预测主类={p.main_class}"
                        f"({'命中' if p.hit else '偏差'})")
                items_by_pid.setdefault(p.person_id, []).append({
                    "text": text,
                    "source": "calibration_runner",
                    "confidence": p.confidence,
                    "timestamp": _dt.now().isoformat(timespec="seconds"),
                    "speaker": p.person_id,
                })
            for pid, raw_items in items_by_pid.items():
                fn = getattr(self.memory_service, "guarded_write_calibration", None)
                if fn is None:
                    # 兜底：旧接口不存在时退回 add_memory（不硬失败）
                    fn2 = getattr(self.memory_service, "add_memory", None)
                    if fn2 is None:
                        continue
                    for it in raw_items:
                        fn2(pid, it.get("text", ""))
                    continue
                result = fn(pid, raw_items)
                logger.info(
                    "calibration_runner 回灌记忆（过闸门）person=%s accepted=%s rejected=%s",
                    pid, result.get("accepted"), result.get("rejected"))
        except Exception as e:  # noqa: BLE001 - 回灌失败不影响校准闭环
            logger.warning("calibration_runner 回灌记忆失败（忽略）: %s", e)

    def _memory_batches(self, preds: List[ScenarioPrediction],
                        batch_size: int = 4) -> List[BatchSnapshot]:
        snaps: List[BatchSnapshot] = []
        seen: List[ScenarioPrediction] = []
        for i, p in enumerate(preds):
            seen.append(p)
            if (i + 1) % batch_size == 0 or (i + 1) == len(preds):
                snaps.append(self._snapshot(i + 1, seen))
        return snaps

    def _snapshot(self, idx: int, seen: List[ScenarioPrediction]) -> BatchSnapshot:
        hr, ece, cf = self._metrics(seen)
        rl_val = None
        rl_band = None
        rl_sw = None
        if self._rl is not None:
            # 跨所有人设聚合：取每个 persona 的 rolling_validity 再平均
            vals, bands, sws = [], [], []
            for pid in self.scenarios.keys():
                st = self._rl.rolling_validity(pid, window=50)
                if st.get("validity") is not None:
                    vals.append(st["validity"])
                cal = self._rl.calibration(pid)
                if cal.get("available"):
                    bands.append(cal.get("band"))
                    sws.append(cal.get("sim_weight"))
            if vals:
                rl_val = round(sum(vals) / len(vals), 4)
            if bands:
                # 取众数
                from collections import Counter
                rl_band = Counter(bands).most_common(1)[0][0]
            if sws:
                rl_sw = round(sum(sws) / len(sws), 4)
        return BatchSnapshot(
            batch_index=idx, n=len(seen), hit_rate=hr, ece=ece,
            calib_factor=cf, review_loop_validity=rl_val,
            review_loop_band=rl_band, review_loop_sim_weight=rl_sw,
        )

    # ------------------------------------------------------------------ #
    # 4) 指标：命中率 + ECE
    # ------------------------------------------------------------------ #
    def _metrics(self, preds: List[ScenarioPrediction]
                 ) -> Tuple[float, float, float]:
        n = len(preds)
        if n == 0:
            return 0.0, 0.0, 1.0
        hit_rate = sum(p.hit for p in preds) / n
        # ECE：按置信度等宽分桶
        edges = [i / self.n_bins for i in range(self.n_bins + 1)]
        bins = [{"lo": edges[i], "hi": edges[i + 1],
                 "conf_sum": 0.0, "hit_sum": 0, "count": 0}
                for i in range(self.n_bins)]
        for p in preds:
            c = min(max(p.confidence, 0.0), 1.0 - 1e-9)
            b = min(int(c * self.n_bins), self.n_bins - 1)
            bins[b]["conf_sum"] += p.confidence
            bins[b]["hit_sum"] += p.hit
            bins[b]["count"] += 1
        ece = 0.0
        for b in bins:
            if b["count"] == 0:
                continue
            avg_conf = b["conf_sum"] / b["count"]
            acc = b["hit_sum"] / b["count"]
            ece += (b["count"] / n) * abs(avg_conf - acc)
        # 校准因子：观测准确率 / 平均置信度（>1 说明模型偏保守，<1 说明过度自信）
        avg_conf_all = sum(p.confidence for p in preds) / n
        calib_factor = round(hit_rate / avg_conf_all, 4) if avg_conf_all > 0 else 1.0
        return round(hit_rate, 4), round(ece, 4), calib_factor

    def _aggregate(self, preds: List[ScenarioPrediction],
                   snaps: List[BatchSnapshot]) -> Dict[str, Any]:
        hr, ece, cf = self._metrics(preds)
        # 每类（Y）指标
        per_class: Dict[str, Dict[str, Any]] = {}
        for k in Y_KEYS:
            sub = [p for p in preds if p.synth_actual == k]
            if not sub:
                continue
            per_class[k] = {
                "label": Y_LABELS[k],
                "n": len(sub),
                "hit_rate": round(sum(p.hit for p in sub) / len(sub), 4),
                "avg_confidence": round(sum(p.confidence for p in sub) / len(sub), 4),
            }
        # 按置信度 bin 统计（最终态）
        edges = [i / self.n_bins for i in range(self.n_bins + 1)]
        bin_stats = []
        for i in range(self.n_bins):
            lo, hi = edges[i], edges[i + 1]
            sub = [p for p in preds
                   if lo <= p.confidence < hi or (i == self.n_bins - 1 and p.confidence >= hi)]
            if not sub:
                bin_stats.append({"bin": i, "range": [round(lo, 2), round(hi, 2)],
                                  "count": 0, "avg_confidence": None, "accuracy": None})
                continue
            bin_stats.append({
                "bin": i, "range": [round(lo, 2), round(hi, 2)],
                "count": len(sub),
                "avg_confidence": round(sum(p.confidence for p in sub) / len(sub), 4),
                "accuracy": round(sum(p.hit for p in sub) / len(sub), 4),
            })
        # 重校准建议
        if cf > 1.15:
            suggestion = ("模型整体偏保守（预测置信度低于实际准确率），可对 P 主类置信度"
                          "做上探 / 提高模拟实测分权重。")
        elif cf < 0.85:
            suggestion = ("模型整体过度自信（预测置信度高于实际准确率），应对 P 主类置信度"
                          "做收缩（shrinkage）/ 降低模拟实测分权重。")
        else:
            suggestion = "整体校准尚可，维持当前权重；按 ECE 分桶做局部收缩。"
        return {
            "n": len(preds),
            "hit_rate": hr,
            "ece": ece,
            "calib_factor": cf,
            "per_class": per_class,
            "bin_stats": bin_stats,
            "recalibration_suggestion": suggestion,
            "final_snapshot": asdict(snaps[-1]) if snaps else None,
        }

    # ------------------------------------------------------------------ #
    # 5) 组装 JSON
    # ------------------------------------------------------------------ #
    def _build_result(self, preds: List[ScenarioPrediction],
                      snaps: List[BatchSnapshot],
                      agg: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "banner": SYNTHETIC_BANNER,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "seed": self.seed,
            "mix_alpha": self.mix_alpha,
            "n_bins": self.n_bins,
            "note": ("本结果中所有 actual 结局均由固定 seed 的合成真值生成器从 "
                     f"Q = {self.mix_alpha}·P + (1-{self.mix_alpha})·style_bias 抽样得到，"
                     "用于演示预测→回填→再校准闭环；不代表任何真人命中率。"),
            "scenario_summary": {
                "person_ids": list(self.scenarios.keys()),
                "messages_per_person": {k: len(v) for k, v in self.scenarios.items()},
            },
            "per_scenario": [
                {
                    "person_id": p.person_id,
                    "user_message": p.user_message,
                    "style": p.style,
                    "P": p.P,
                    "main_class": p.main_class,
                    "main_label": Y_LABELS[p.main_class],
                    "confidence": p.confidence,
                    "emotion_mean": p.emotion_mean,
                    "emotion_range": p.emotion_range,
                    "synth_actual": p.synth_actual,
                    "synth_actual_label": Y_LABELS[p.synth_actual],
                    "synth_actual_emotion": p.synth_actual_emotion,
                    "synth_actual_reply": p.synth_actual_reply,
                    "no_reply": p.no_reply,
                    "hit": p.hit,
                } for p in preds
            ],
            "closed_loop_batches": [asdict(s) for s in snaps],
            "aggregate": agg,
        }

    # ------------------------------------------------------------------ #
    # 6) 渲染 Markdown
    # ------------------------------------------------------------------ #
    @staticmethod
    def _render_md(result: Dict[str, Any]) -> str:
        lines: List[str] = []
        lines.append(f"> **⚠️ {result['banner']}**")
        lines.append("")
        lines.append("# 校准闭环演示报告（合成/模拟回填）")
        lines.append("")
        lines.append(f"- 生成时间：{result['generated_at']}")
        lines.append(f"- 随机 seed：`{result['seed']}`（同 seed 两次运行结果一致）")
        lines.append(f"- 混合系数 α（Q = α·P + (1-α)·style_bias）：{result['mix_alpha']}")
        lines.append(f"- ECE 分桶数：{result['n_bins']}（等宽）")
        lines.append("")
        lines.append("## 总体指标")
        agg = result["aggregate"]
        lines.append("")
        lines.append(f"- 样本数 n：**{agg['n']}**")
        lines.append(f"- 命中率（argmax vs 模拟实际）：**{agg['hit_rate']:.4f}**")
        lines.append(f"- ECE（期望校准误差）：**{agg['ece']:.4f}**")
        lines.append(f"- 校准因子（hit_rate / avg_confidence）：**{agg['calib_factor']}**")
        lines.append(f"- 重校准建议：{agg['recalibration_suggestion']}")
        lines.append("")
        lines.append("## 闭环状态随批次更新")
        lines.append("")
        lines.append("| 批次(累计样本) | 命中率 | ECE | 校准因子 | RL 命中率 | RL band | RL sim_weight |")
        lines.append("|---:|---:|---:|---:|---:|:---:|---:|")
        for b in result["closed_loop_batches"]:
            lines.append(
                f"| {b['batch_index']} | {b['hit_rate']:.4f} | {b['ece']:.4f} | "
                f"{b['calib_factor']} | "
                f"{b['review_loop_validity'] if b['review_loop_validity'] is not None else '-'} | "
                f"{b['review_loop_band'] or '-'} | "
                f"{b['review_loop_sim_weight'] if b['review_loop_sim_weight'] is not None else '-'} |")
        lines.append("")
        lines.append("## ECE 分桶（最终态）")
        lines.append("")
        lines.append("| bin | 置信度区间 | 计数 | 平均置信度 | 桶准确率 |")
        lines.append("|---:|:---|---:|---:|---:|")
        for b in agg["bin_stats"]:
            if b["count"] == 0:
                lines.append(f"| {b['bin']} | {b['range'][0]:.1f}-{b['range'][1]:.1f} | 0 | - | - |")
            else:
                lines.append(f"| {b['bin']} | {b['range'][0]:.1f}-{b['range'][1]:.1f} | "
                             f"{b['count']} | {b['avg_confidence']:.4f} | {b['accuracy']:.4f} |")
        lines.append("")
        lines.append("## 每类（Y）命中情况")
        lines.append("")
        lines.append("| Y | 标签 | 模拟实际数 | 命中率 | 平均预测置信度 |")
        lines.append("|:---:|:---|---:|---:|---:|")
        for k, v in agg["per_class"].items():
            lines.append(f"| {k} | {v['label']} | {v['n']} | "
                         f"{v['hit_rate']:.4f} | {v['avg_confidence']:.4f} |")
        lines.append("")
        lines.append("## 逐场景明细")
        lines.append("")
        for s in result["per_scenario"]:
            mark = "✅" if s["hit"] else "❌"
            lines.append(
                f"### {mark} [{s['person_id']}/{s['style']}] {s['user_message']}")
            lines.append("")
            lines.append(f"- 预测主类：**{s['main_class']} {s['main_label']}**"
                         f"（置信度 {s['confidence']:.4f}）")
            lines.append(f"- 合成实际：**{s['synth_actual']} {s['synth_actual_label']}**"
                         f"（情绪={s['synth_actual_emotion'] or '(无)'}，"
                         f"no_reply={s['no_reply']}）")
            P = s["P"]
            lines.append("- P(Y)：" + " ".join(f"{k}={P[k]:.3f}" for k in Y_KEYS))
            lines.append("")
        lines.append("---")
        lines.append(f"*{result['banner']}*")
        lines.append("")
        return "\n".join(lines)


# ====================================================================== #
# P-02 校准科学口径（纯函数 / 离线确定性，补 T）
# ---------------------------------------------------------------------- #
# 设计口径（见 v3.1 加固补丁 P-02）：
#   - 对账同时给 可靠性图 / ECE / 多类 Brier，并做 Murphy 分解
#     （可靠性 reliability / 分辨度 resolution / 不确定性 uncertainty）；
#   - 只校准而【无分辨度】（不能区分高/低风险场景）的输出判为无效；
#   - 只在"用户已实际发送"条件下校准；未发送候选的模拟分永不进准确率报告；
#   - ECE 双线：0.15 告警/重标定，0.10 稳态达标。
# 本段落为新增纯函数，不改动上方 CalibrationRunner 既有逻辑。
# ====================================================================== #

ECE_ALERT_LINE = 0.15     # 0.15：告警 / 重标定触发线
ECE_STEADY_LINE = 0.10    # 0.10：稳态达标线


def multiclass_brier(probs: Dict[str, float], actual: str) -> float:
    """多类 Brier 分数：sum_k (p_k - 1{k==actual})^2，越小越好。

    Args:
        probs: 各类预测概率 dict（不必预先归一，内部会归一）。
        actual: 实际发生的类键。

    Returns:
        float: Brier 分数；无有效概率时返回 1.0（最坏）。
    """
    if not probs:
        return 1.0
    total = sum(probs.values())
    if total <= 0:
        return 1.0
    s = 0.0
    for k, p in probs.items():
        pk = float(p) / total
        ik = 1.0 if k == actual else 0.0
        s += (pk - ik) ** 2
    return round(s, 6)


def filter_sent_records(records: List[dict]) -> List[dict]:
    """P-02 口径：只保留"用户已实际发送"的记录。

    未发送候选的反应不可验证，其模拟分永不进入准确率报告。
    每条记录须带 actually_sent: bool；缺省视为未发送（保守排除）。
    """
    return [r for r in records if r.get("actually_sent") is True]


def ece_from_records(records: List[dict], n_bins: int = 10) -> float:
    """对 {conf, hit} 记录计算 ECE（等宽分桶加权）。空样本返回 0.0。"""
    n = len(records)
    if n == 0:
        return 0.0
    bins = [{"cs": 0.0, "hs": 0, "c": 0} for _ in range(n_bins)]
    for r in records:
        c = min(max(float(r["conf"]), 0.0), 1.0 - 1e-9)
        b = min(int(c * n_bins), n_bins - 1)
        bins[b]["cs"] += c
        bins[b]["hs"] += int(r["hit"])
        bins[b]["c"] += 1
    ece = 0.0
    for b in bins:
        if b["c"] == 0:
            continue
        avg_conf = b["cs"] / b["c"]
        acc = b["hs"] / b["c"]
        ece += (b["c"] / n) * abs(avg_conf - acc)
    return round(ece, 6)


def murphy_decomposition(records: List[dict], n_bins: int = 10) -> Dict[str, Any]:
    """Murphy 分解：reliability / resolution / uncertainty。

    基于可靠性分桶（{conf, hit}），满足恒等式：
        score(=该二分类框架下的均方误差) = reliability - resolution + uncertainty

    Args:
        records: list of {conf: float(0-1), hit: 0/1}。
        n_bins: 等宽桶数。

    Returns:
        dict: {n, reliability, resolution, uncertainty, score,
               overall_accuracy, nonempty_bins}
    """
    n = len(records)
    if n == 0:
        return {"n": 0, "reliability": 0.0, "resolution": 0.0,
                "uncertainty": 0.0, "score": 0.0,
                "overall_accuracy": 0.0, "nonempty_bins": 0}
    bins = [{"cs": 0.0, "hs": 0, "c": 0} for _ in range(n_bins)]
    for r in records:
        c = min(max(float(r["conf"]), 0.0), 1.0 - 1e-9)
        b = min(int(c * n_bins), n_bins - 1)
        bins[b]["cs"] += c
        bins[b]["hs"] += int(r["hit"])
        bins[b]["c"] += 1

    overall_acc = sum(int(r["hit"]) for r in records) / n
    reliability = 0.0
    resolution = 0.0
    nonempty = 0
    for b in bins:
        if b["c"] == 0:
            continue
        nonempty += 1
        m_k = b["cs"] / b["c"]          # 该桶平均预测置信
        f_k = b["hs"] / b["c"]          # 该桶实际频率
        w = b["c"] / n
        reliability += w * (m_k - f_k) ** 2
        resolution += w * (f_k - overall_acc) ** 2
    uncertainty = overall_acc * (1.0 - overall_acc)
    score = reliability - resolution + uncertainty
    return {
        "n": n,
        "reliability": round(reliability, 6),
        "resolution": round(resolution, 6),
        "uncertainty": round(uncertainty, 6),
        "score": round(score, 6),
        "overall_accuracy": round(overall_acc, 6),
        "nonempty_bins": nonempty,
    }


def ece_status(ece: float) -> str:
    """ECE 双线判定：0.10 稳态达标 / 0.15 告警重标定 / 之间为观察。"""
    e = float(ece)
    if e <= ECE_STEADY_LINE:
        return "steady"      # 稳态达标
    if e >= ECE_ALERT_LINE:
        return "recalibrate"  # 告警 / 重标定
    return "watch"


def has_resolution(records: List[dict], n_bins: int = 10,
                   min_spread: float = 0.15, min_nonempty: int = 2) -> bool:
    """分辨度判定：模型能否区分高/低风险场景。

    判据：非空桶的实际频率极差(max-min) >= min_spread，且至少 min_nonempty 个桶。
    无分辨度（各桶命中率都差不多）→ 输出判无效。
    """
    n = len(records)
    if n == 0:
        return False
    bins = [{"hs": 0, "c": 0} for _ in range(n_bins)]
    for r in records:
        c = min(max(float(r["conf"]), 0.0), 1.0 - 1e-9)
        b = min(int(c * n_bins), n_bins - 1)
        bins[b]["hs"] += int(r["hit"])
        bins[b]["c"] += 1
    freqs = [b["hs"] / b["c"] for b in bins if b["c"] > 0]
    if len(freqs) < min_nonempty:
        return False
    spread = max(freqs) - min(freqs)
    return spread >= min_spread


def calibration_diagnosis(records: List[dict], n_bins: int = 10) -> Dict[str, Any]:
    """P-02 对账汇总：ECE 双线 + Murphy 分解 + 分辨度有效性。

    输入 records 应【已】经过 filter_sent_records 过滤（仅已发送）。
    返回是否 valid：无分辨度 → invalid。
    """
    murphy = murphy_decomposition(records, n_bins)
    ece = ece_from_records(records, n_bins)
    res_ok = has_resolution(records, n_bins)
    return {
        "n": len(records),
        "ece": ece,
        "ece_line": ece_status(ece),
        "murphy": murphy,
        "resolution_ok": res_ok,
        "valid": bool(res_ok) and len(records) > 0,
        "note": ("无分辨度（不能区分高/低风险场景）→ 该输出判无效，"
                 "不得作为置信证据") if not res_ok else "",
    }
