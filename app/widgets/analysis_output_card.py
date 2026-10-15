# -*- coding: utf-8 -*-
"""
R3.3-22 单轮分析输出面板（悬浮卡 / 分析总览卡）。

六类字段全部落地，缺一显示「—」：
  意图   <- emotion_analyzer / no_reply_detector 输出（result["intent"]）
  把握   <- confidence_cap.cap_score 封顶后的数字（result["confidence"]）
           UI 措辞固定为「预测有效度」（数字诚实规范：历史命中率 ≠ 对方照做的概率）
  策略   <- six_dim_strategy / strategy_advisor（result["strategy"]）
  3候选  <- strategy_advisor（result["reply_candidates"]，最多3条）
  风险   <- deception_detector / safety_filter（result["risk"]）
  预测   <- digital_twin.predict_reaction（result["prediction"]）

纯 UI 层：不 import 失败时安全降级；offscreen 可实例化。
"""
from __future__ import annotations

try:
    from PySide6.QtWidgets import QWidget, QVBoxLayout, QGroupBox, QLabel, QFormLayout
    from PySide6.QtCore import Qt
    _HAS_QT = True
except Exception:  # offscreen 兜底
    _HAS_QT = False
    QWidget = object

# 把握度统一走 confidence_cap（数字诚实：永不 100%）；导入失败时退化为原样显示
try:
    from services.confidence_cap import cap_score as _cap_score
except Exception:  # pragma: no cover
    def _cap_score(value, evidence_n: int = 0):  # type: ignore[misc]
        try:
            v = float(value)
        except (TypeError, ValueError):
            return 0.0
        return min(max(v, 0.0), 0.99)


# 六字段的展示名（key -> 中文 label）。测试断言这些 label 都出现。
FIELD_LABELS = {
    "intent": "意图",
    "confidence": "预测有效度",
    "strategy": "策略",
    "candidates": "候选话术",
    "risk": "风险",
    "prediction": "预测",
}

_EMPTY = "—"


def _stringify(value) -> str:
    """把任意字段值压成单行展示文本；空值返回空串。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        parts = []
        for i, item in enumerate(value[:3]):
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("reply")
                                 or item.get("content") or item.get("strategy") or ""))
            else:
                parts.append(str(item))
        return "；".join(p for p in parts if p)
    if isinstance(value, dict):
        for k in ("summary", "label", "text", "direction", "advice", "name"):
            if value.get(k):
                return str(value[k])
        return "；".join(f"{k}:{v}" for k, v in list(value.items())[:4])
    return str(value)


def _flatten_result(raw: dict) -> dict:
    """把生产路径的嵌套 orchestration dict 平铺到卡片读的顶层键。

    验收官第三轮揪出的假修：生产入口 service_facade 把 orchestrate() 的返回
    整个塞进 data["orchestration"]（嵌套），而本卡片只读顶层 result["strategy"]/
    result["risk"]/result["prediction"]，对不上 → 五六个字段全显示「—」。

    这里在 set_result() 入口做一次只读平铺：仅当顶层缺该键时，才从
    orchestration 里补上来；已有的顶层值不覆盖。任何异常都不影响渲染。
    """
    if not isinstance(raw, dict):
        return {}
    out = dict(raw)
    orch = out.get("orchestration")
    if not isinstance(orch, dict):
        return out
    try:
        # 策略：orchestration["strategy"]["recommended"]（list）
        strat = orch.get("strategy")
        if not out.get("strategy") and isinstance(strat, dict):
            out["strategy"] = strat.get("recommended") or strat
        # 风险：orchestration["risk"]（拼好的人读串）
        if not out.get("risk") and not out.get("risk_warnings"):
            if orch.get("risk"):
                out["risk"] = orch.get("risk")
            elif orch.get("risk_warnings"):
                out["risk_warnings"] = orch.get("risk_warnings")
        # 预测：orchestration["prediction"]（dict）或顶层 twin_reaction
        if not out.get("prediction") and orch.get("prediction"):
            out["prediction"] = orch.get("prediction")
        # 候选话术：orchestration["candidates"]（list[str]）
        if not (out.get("reply_candidates") or out.get("replies")
                or out.get("suggested_replies")):
            cands = orch.get("candidates")
            if cands:
                out["reply_candidates"] = cands
        # 意图兜底：orchestration["personality"]["primary_personality"]
        if not (out.get("intent") or out.get("scene")):
            pers = orch.get("personality") or {}
            if isinstance(pers, dict) and pers.get("primary_personality"):
                out["intent"] = {"label": pers.get("primary_personality")}
        # 把握度：orchestration["scene"]["confidence"] 或顶层 confidence
        # （验收官第五轮揪出：confidence 嵌在 scene 子 dict 里，不是顶层键）
        if not out.get("confidence"):
            conf = orch.get("confidence") or orch.get("confidence_score")
            if conf is None:
                scene = orch.get("scene") or {}
                if isinstance(scene, dict):
                    conf = scene.get("confidence") or scene.get("confidence_score")
            if conf is not None:
                out["confidence"] = conf
    except Exception:  # noqa: BLE001
        pass
    return out


class AnalysisOutputCard(QWidget if _HAS_QT else object):
    """单轮分析输出卡片：六字段固定槽位，缺数据显示「—」。"""

    def __init__(self, parent=None):
        if _HAS_QT:
            super().__init__(parent)
        self._labels: dict[str, QLabel] = {}
        if _HAS_QT:
            self._build_ui()

    if _HAS_QT:
        def _build_ui(self) -> None:
            box = QGroupBox("单轮分析输出")
            box.setStyleSheet(
                "QGroupBox { color:#f4d03f; border:1px solid #8a7a20; border-radius:6px;"
                "margin-top:8px; padding-top:8px; }"
                "QGroupBox::title { left:10px; padding:0 4px; }")
            form = QFormLayout(box)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            form.setHorizontalSpacing(10)
            form.setVerticalSpacing(6)
            for key, zh in FIELD_LABELS.items():
                lbl = QLabel(_EMPTY)
                lbl.setWordWrap(True)
                lbl.setStyleSheet("color:#e0e0e0; background:transparent;")
                lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
                form.addRow(f"<b style='color:#f4d03f'>{zh}：</b>", lbl)
                self._labels[key] = lbl
            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.addWidget(box)

    # ------------------------------------------------------------------ #
    def set_result(self, result: dict) -> None:
        """渲染一份 analysis_result dict；任意字段缺失显示「—」。"""
        result = _flatten_result(result if isinstance(result, dict) else {})

        intent = _stringify(result.get("intent") or result.get("scene")
                             or result.get("emotion"))
        strategy = _stringify(result.get("strategy") or result.get("strategy_direction"))
        risk = _stringify(result.get("risk") or result.get("risk_warning")
                          or result.get("risk_warnings"))
        prediction = _stringify(result.get("prediction") or result.get("twin_prediction"))

        # 把握度：原始 confidence 走 cap_score 封顶；措辞用「预测有效度」
        evidence_n = result.get("evidence_n", 0)
        try:
            evidence_n = int(evidence_n or 0)
        except (TypeError, ValueError):
            evidence_n = 0
        raw_conf = result.get("confidence")
        if raw_conf in (None, "", []):
            conf_text = _EMPTY
        else:
            try:
                capped = _cap_score(raw_conf, evidence_n)
                conf_text = f"{capped * 100:.0f}%" + (f"（证据{evidence_n}条）" if evidence_n else "")
            except Exception:
                conf_text = _EMPTY

        # 3 候选话术：取列表前 3 条，编号排列
        cands = result.get("reply_candidates") or result.get("replies") \
            or result.get("suggested_replies") or []
        if isinstance(cands, str):
            cands = [cands]
        cand_lines = []
        for i, item in enumerate(list(cands)[:3]):
            txt = _stringify(item)
            if txt:
                cand_lines.append(f"{i + 1}. {txt}")
        cand_text = "\n".join(cand_lines) if cand_lines else _EMPTY

        values = {
            "intent": intent or _EMPTY,
            "confidence": conf_text,
            "strategy": strategy or _EMPTY,
            "candidates": cand_text,
            "risk": risk or _EMPTY,
            "prediction": prediction or _EMPTY,
        }
        for key, text in values.items():
            lbl = self._labels.get(key)
            if lbl is not None:
                lbl.setText(text)

    def field_text(self, key: str) -> str:
        """测试/调试用：读回某字段当前显示文本。"""
        lbl = self._labels.get(key)
        return lbl.text() if lbl is not None else ""
