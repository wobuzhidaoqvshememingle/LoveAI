# -*- coding: utf-8 -*-
"""
CHARACTER FILE 角色档案页渲染器

直接复刻用户参考图的暗色高级 UI：
  - 关系阶段标签 + 平台/在线状态
  - 好感度双轨（系统心形 0-100 + 用户星级 0-10）
  - 近 N 次互动情绪趋势折线（SVG）
  - 当前策略卡（策略名 + 起始日 + 依据）
  - 情绪基线时间线（日期/升温平稳降温/分析/情绪分）
  - 近期互动 + 危险信号归档

纯 HTML+CSS+内联SVG，QWebEngine 可直接加载，无外部依赖。
"""
from __future__ import annotations

import html
from datetime import datetime
from typing import Optional

_STAGE_COLORS = {
    "初识": "#8b9dc3", "暧昧": "#e879a8", "热恋": "#ff6b8a",
    "稳定": "#6bc5a8", "矛盾": "#f0a040", "倦怠": "#9a9a9a",
}


def render_character_file(profile: dict,
                          emotion_series: list = None,
                          recent_messages: list = None,
                          strategy: dict = None,
                          user_rating: int = 6,
                          platform: str = "QQ",
                          online: str = "ACTIVE",
                          affection: int = None,
                          diagnostic_report: dict = None) -> str:
    """渲染完整 CHARACTER FILE HTML。

    Args:
        profile: PersonaAnalyzer.analyze() 的输出。
        emotion_series: [{"date":"10-07","score":4.7}, ...] 近N次情绪分。
        recent_messages: [{"direction":"received","content":"...","time":"..."}]。
        strategy: {"name":"维持","start":"2026-10-08","basis":"..."}。
        user_rating: 用户手动星级 0-10。
        affection: 系统好感度 0-100；缺省从 profile 推导。
    """
    stage = (profile.get("relationship_stage") or {}).get("stage", "未知")
    stage_color = _STAGE_COLORS.get(stage, "#e879a8")

    if affection is None:
        adj = profile.get("affection_adjustment") or {}
        affection = int(adj.get("score", 50)) if isinstance(adj, dict) else 50
    affection = max(0, min(100, int(affection)))

    # 情绪基线
    emotion_pattern = profile.get("emotion_pattern") or {}
    baseline_items = _build_baseline(emotion_pattern, emotion_series)

    # 危险信号
    risks = (profile.get("risk_warning") or {}).get("risks", [])

    # 当前策略
    if not strategy:
        strategy = {
            "name": _derive_strategy_name(profile),
            "start": datetime.now().strftime("%Y-%m-%d"),
            "basis": (profile.get("summary") or "")[:120],
        }

    # 近期互动
    recent = recent_messages or []

    diag_block = render_diagnostic_block(diagnostic_report)

    return _TEMPLATE.format(
        stage=html.escape(stage),
        stage_color=stage_color,
        platform=html.escape(platform),
        online=html.escape(online),
        hearts=_hearts(affection),
        affection=affection,
        stars=_stars(user_rating),
        user_rating=user_rating,
        emotion_chart=_svg_line(emotion_series or []),
        date_range=_date_range(emotion_series),
        strategy_name=html.escape(strategy.get("name", "维持")),
        strategy_start=html.escape(strategy.get("start", "")),
        strategy_basis=html.escape(strategy.get("basis", "")),
        baseline_items="".join(baseline_items),
        recent_items="".join(_recent_html(recent)),
        risk_items="".join(_risk_html(risks)),
        summary=html.escape(profile.get("summary", "")[:300]),
        msg_count=profile.get("message_count", 0),
        other_count=profile.get("other_message_count", 0),
        my_count=profile.get("my_message_count", 0),
        attachment=html.escape(str((profile.get("attachment") or {}).get("type", "未知"))),
        emotion_count=len(emotion_series or []),
        diag_block=diag_block,
    )


def render_diagnostic_block(report: Optional[dict]) -> str:
    """把 18 部分诊断报告摘要渲染成一个暗色卡片（嵌入 CHARACTER FILE）。"""
    if not report:
        return ""
    act = report.get("actionable", {}) or {}
    labels = act.get("core_labels", []) or []
    dr = (report.get("section_map", {}).get("dark_risk", {}) or {}).get("content", {}) or {}
    risk_level = dr.get("risk_level", 0)
    risk_color = ("#6bc5a8" if risk_level <= 1 else
                  "#f0a040" if risk_level <= 3 else "#ff6b6b")
    label_html = "".join(
        f'<span class="diag-tag">{html.escape(str(t))}</span>' for t in labels
    ) or '<span class="diag-tag">待观察</span>'
    portrait = html.escape((report.get("portrait_plain", "") or "")[:140])
    works = "；".join(act.get("what_works", [])[:2])
    return f'''
<div class="card">
  <div class="card-label">FULL DIAGNOSTIC REPORT · 完整诊断</div>
  <div class="diag-row"><span class="diag-risk" style="background:{risk_color}22;color:{risk_color};border-color:{risk_color}">风险 {risk_level}/5</span>
    <span class="diag-labels">{label_html}</span></div>
  <div class="diag-portrait">{portrait}……</div>
  <div class="diag-works">吃哪套：{html.escape(works)}</div>
  <div class="diag-more">完整 18 部分报告见应用内「查看完整诊断报告」</div>
</div>'''


# ---------------------------------------------------------------------- #
# 子组件
# ---------------------------------------------------------------------- #
def _hearts(n: int, total: int = 100, per: int = 10) -> str:
    filled = round(n / per)
    return "".join(
        f'<span class="heart {"on" if i < filled else ""}">♥</span>'
        for i in range(total // per))


def _stars(n: int, total: int = 10) -> str:
    return "".join(
        f'<span class="star {"on" if i < n else ""}">★</span>'
        for i in range(total))


def _svg_line(series: list, w: int = 560, h: int = 120) -> str:
    if not series:
        return f'<svg width="{w}" height="{h}" class="chart-empty"><text x="{w//2}" y="{h//2}" text-anchor="middle" fill="#555">情绪数据不足</text></svg>'
    pts = [(i, max(0, min(10, s.get("score", 5)))) for i, s in enumerate(series)]
    n = len(pts)
    if n == 1:
        pts.append((1, pts[0][1]))
        series = list(series) + [dict(series[0])]  # 同步复制 series，避免 labels 索引越界
        n = 2
    pad = 20
    sx = (w - 2 * pad) / (n - 1)
    sy = (h - 2 * pad) / 10
    coords = [(pad + i * sx, h - pad - v * sy) for i, v in pts]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"{pad},{h - pad} {poly} {w - pad},{h - pad}"
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#e879a8"/>'
        for x, y in coords)
    labels = "".join(
        f'<text x="{coords[i][0]:.1f}" y="{h - 4}" text-anchor="middle" '
        f'fill="#666" font-size="10">{html.escape(str(series[i].get("date","")))}</text>'
        for i in range(0, n, max(1, n // 5)))
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<polygon points="{area}" fill="url(#grad)" opacity="0.25"/>'
            f'<polyline points="{poly}" fill="none" stroke="#e879a8" stroke-width="2"/>'
            f'{dots}{labels}'
            f'<defs><linearGradient id="grad" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0%" stop-color="#e879a8"/><stop offset="100%" stop-color="#e879a8" stop-opacity="0"/>'
            f'</linearGradient></defs></svg>')


def _date_range(series: list) -> str:
    if not series:
        return "暂无数据"
    return f'{series[0].get("date", "")} → {series[-1].get("date", "")}'


def _build_baseline(emotion_pattern: dict, series: list) -> list[str]:
    items = []
    if series:
        for s in series[-4:]:
            score = s.get("score", 5)
            tag = "升温" if score >= 6 else ("降温" if score <= 4 else "平稳")
            tag_color = "#6bc5a8" if tag == "升温" else ("#f0a040" if tag == "平稳" else "#ff6b6b")
            items.append(f'''
            <div class="baseline-row">
                <div class="baseline-date">{html.escape(str(s.get("date","")))}</div>
                <span class="baseline-tag" style="background:{tag_color}22;color:{tag_color};border-color:{tag_color}">{tag}</span>
                <div class="baseline-text">{html.escape(str(s.get("note","情绪波动")))[:80]}</div>
                <div class="baseline-bar"><div class="baseline-fill" style="width:{score*10}%"></div></div>
                <div class="baseline-score">{score:.1f}/10</div>
            </div>''')
    if not items:
        stability = emotion_pattern.get("stability", "中等")
        items.append(f'''
        <div class="baseline-row">
            <div class="baseline-date">{datetime.now().strftime("%Y-%m-%d")}</div>
            <span class="baseline-tag" style="background:#f0a04022;color:#f0a040;border-color:#f0a040">平稳</span>
            <div class="baseline-text">情绪稳定性：{html.escape(str(stability))}；需更多互动数据建立基线</div>
            <div class="baseline-bar"><div class="baseline-fill" style="width:50%"></div></div>
            <div class="baseline-score">5.0/10</div>
        </div>''')
    return items


def _recent_html(messages: list) -> list[str]:
    out = []
    for m in messages[-6:]:
        direction = m.get("direction", "received")
        who = "对方" if direction == "received" else "我"
        color = "#6ba3ff" if direction == "received" else "#e879a8"
        content = html.escape(str(m.get("content", ""))[:60])
        time = html.escape(str(m.get("time", m.get("created_at", "")))[5:16])
        out.append(f'''
        <div class="recent-row">
            <span class="recent-who" style="color:{color}">{who}</span>
            <span class="recent-content">{content}</span>
            <span class="recent-time">{time}</span>
        </div>''')
    if not out:
        out.append('<div class="recent-row"><span class="recent-content" style="color:#666">暂无近期互动记录</span></div>')
    return out


def _risk_html(risks: list) -> list[str]:
    out = []
    for r in risks[:5]:
        level = r.get("level", "低")
        color = {"低": "#6bc5a8", "中": "#f0a040", "高": "#ff6b6b"}.get(level, "#999")
        out.append(f'''
        <div class="risk-row">
            <span class="risk-level" style="background:{color}22;color:{color};border-color:{color}">{html.escape(str(level))}</span>
            <span class="risk-type">{html.escape(str(r.get("type","")))}</span>
            <span class="risk-signal">{html.escape(str(r.get("signals",""))[:80])}</span>
        </div>''')
    if not out:
        out.append('<div class="risk-row"><span class="risk-level" style="background:#6bc5a822;color:#6bc5a8;border-color:#6bc5a8">低</span><span class="risk-type">无明显风险</span></div>')
    return out


def _derive_strategy_name(profile: dict) -> str:
    stage = (profile.get("relationship_stage") or {}).get("stage", "")
    if stage in ("暧昧",):
        return "维持张力"
    if stage in ("热恋",):
        return "深化绑定"
    if stage in ("矛盾", "倦怠"):
        return "修复引导"
    return "维持"


# ---------------------------------------------------------------------- #
# HTML 模板
# ---------------------------------------------------------------------- #
_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#0b0d13; color:#e0e0e0; font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif; padding:24px; }}
.wrap {{ max-width:640px; margin:0 auto; }}
.title-sub {{ color:#e879a8; font-size:13px; letter-spacing:3px; margin-bottom:4px; }}
.title-main {{ font-size:32px; font-weight:700; margin-bottom:20px; }}
.card {{ background:#151821; border:1px solid #232735; border-radius:14px; padding:18px 20px; margin-bottom:16px; }}
.card-label {{ color:#6ba3ff; font-size:12px; letter-spacing:2px; margin-bottom:12px; }}
.row {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }}
.tag {{ display:inline-block; padding:3px 12px; border-radius:20px; font-size:12px; border:1px solid; }}
.platform {{ color:#aaa; font-size:14px; }}
.hearts {{ font-size:18px; letter-spacing:2px; }}
.heart {{ color:#333; }} .heart.on {{ color:#ff5a7a; }}
.stars {{ font-size:16px; letter-spacing:1px; }}
.star {{ color:#333; }} .star.on {{ color:#f5c542; }}
.score-num {{ color:#e879a8; font-size:20px; font-weight:600; }}
.chart {{ margin-top:8px; }}
.chart-empty text {{ font-size:13px; }}
.strategy-name {{ font-size:20px; font-weight:600; margin-bottom:4px; }}
.strategy-start {{ color:#888; font-size:13px; margin-bottom:8px; }}
.strategy-basis {{ color:#bbb; font-size:13px; line-height:1.6; }}
.baseline-row {{ display:flex; align-items:center; gap:10px; padding:8px 0; border-bottom:1px solid #1f2330; }}
.baseline-row:last-child {{ border-bottom:none; }}
.baseline-date {{ color:#888; font-size:12px; width:80px; flex-shrink:0; }}
.baseline-tag {{ padding:2px 8px; border-radius:10px; font-size:11px; border:1px solid; flex-shrink:0; }}
.baseline-text {{ flex:1; font-size:12px; color:#bbb; line-height:1.4; }}
.baseline-bar {{ width:80px; height:6px; background:#232735; border-radius:3px; flex-shrink:0; overflow:hidden; }}
.baseline-fill {{ height:100%; background:linear-gradient(90deg,#6ba3ff,#e879a8); border-radius:3px; }}
.baseline-score {{ color:#aaa; font-size:12px; width:48px; text-align:right; flex-shrink:0; }}
.recent-row {{ display:flex; align-items:center; gap:8px; padding:6px 0; border-bottom:1px solid #1f2330; font-size:13px; }}
.recent-row:last-child {{ border-bottom:none; }}
.recent-who {{ width:36px; font-weight:600; flex-shrink:0; }}
.recent-content {{ flex:1; color:#ccc; }}
.recent-time {{ color:#666; font-size:11px; flex-shrink:0; }}
.risk-row {{ display:flex; align-items:center; gap:10px; padding:6px 0; font-size:13px; }}
.risk-level {{ padding:2px 10px; border-radius:10px; font-size:11px; border:1px solid; flex-shrink:0; }}
.risk-type {{ color:#ddd; font-weight:600; width:100px; flex-shrink:0; }}
.risk-signal {{ color:#999; font-size:12px; flex:1; }}
.summary-box {{ color:#aaa; font-size:13px; line-height:1.7; margin-top:8px; }}
.meta {{ color:#666; font-size:12px; margin-top:12px; }}
.diag-row {{ display:flex; align-items:center; gap:10px; margin-bottom:10px; flex-wrap:wrap; }}
.diag-risk {{ padding:2px 10px; border-radius:10px; font-size:12px; border:1px solid; }}
.diag-tag {{ display:inline-block; padding:2px 10px; border-radius:12px; font-size:12px; background:#e879a822; color:#e879a8; border:1px solid #e879a855; margin-right:6px; }}
.diag-portrait {{ color:#ccc; font-size:13px; line-height:1.7; margin-bottom:8px; }}
.diag-works {{ color:#6bc5a8; font-size:12px; margin-bottom:6px; }}
.diag-more {{ color:#666; font-size:12px; }}
</style></head><body><div class="wrap">
<div class="title-sub">CHARACTER FILE</div>
<div class="title-main">路线详情</div>

<div class="card">
  <div class="row">
    <span class="platform">{platform} · {online}</span>
    <span class="tag" style="color:{stage_color};border-color:{stage_color};background:{stage_color}15">{stage}</span>
  </div>
  <div class="row" style="margin-top:10px;">
    <span>好感度</span>
    <span><span class="hearts">{hearts}</span> <span class="score-num">{affection}</span>/100</span>
  </div>
  <div class="row">
    <span>我的评分</span>
    <span><span class="stars">{stars}</span> <span class="score-num" style="color:#f5c542">{user_rating}</span>/10</span>
  </div>
  <div class="chart">{emotion_chart}</div>
  <div style="color:#666;font-size:12px;margin-top:4px;">{date_range} · 近{emotion_count}次互动情绪走势</div>
</div>

<div class="card">
  <div class="card-label">CURRENT STRATEGY</div>
  <div class="strategy-name">{strategy_name}</div>
  <div class="strategy-start">起始：{strategy_start}</div>
  <div class="strategy-basis">依据：{strategy_basis}</div>
</div>

<div class="card">
  <div class="card-label">EMOTION BASELINE</div>
  {baseline_items}
</div>

<div class="card">
  <div class="card-label">RECENT INTERACTION</div>
  {recent_items}
</div>

<div class="card">
  <div class="card-label">RISK SIGNALS · 危险信号归档</div>
  {risk_items}
  <div class="summary-box">{summary}</div>
  <div class="meta">消息总数 {msg_count}（对方 {other_count} / 我 {my_count}）· 依恋类型 {attachment}</div>
</div>

{diag_block}

</div></body></html>"""
