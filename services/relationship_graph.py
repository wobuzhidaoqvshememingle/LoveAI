# -*- coding: utf-8 -*-
"""关系网络图（光遇风格网状图）数据模型+自动提取。纯服务层，不 import PySide6。

R3.3-79 活关系星图：NPC 次级网络（NPC↔NPC 边）+ 家庭/伦理关系建模。
伦理关系只做分析标记与中性风险/机会提示，不做价值判断。
"""
from __future__ import annotations
import re, logging, time, math, random
from dataclasses import dataclass, field
logger = logging.getLogger(__name__)

REL_COLORS = {"朋友":"#3b82f6","家人":"#10b981","同事":"#6b7280",
              "前任":"#ef4444","暧昧对象":"#ec4899","其他":"#a855f7"}
APPELLATIONS = ["我妈","我爸","我姐","我哥","我弟","我妹","我朋友","我同事","我闺蜜","我老婆","我老公"]
EMOTION_WORDS = ["喜欢","讨厌","生气","开心","想","失望","关心","信任"]

# R3.3-79：按关系类型给节点赋默认立场（对"我方与目标在一起"的立场，-1..1）
STANCE_MAP = {"家人": 0.4, "朋友": 0.5, "暧昧对象": -0.3,
              "前任": -0.6, "同事": 0.0, "其他": 0.0}


@dataclass
class FamilyContext:
    """目标对象（center）的家庭背景。缺失一律保持'未知'，不臆造。"""
    family_structure: str = "未知"   # 单亲/双亲/重组/独居/其他/未知
    parents_status: str = "未知"     # 和睦/紧张/分居/离婚中/离异/未知
    siblings: list = field(default_factory=list)   # 如 ["姐姐","哥哥"]
    atmosphere: str = "未知"
    evidence: list = field(default_factory=list)   # 聊天原文摘录，最长 8 条
    manual: bool = False             # True=手工录入优先

    def to_dict(self) -> dict:
        return {
            "family_structure": self.family_structure,
            "parents_status": self.parents_status,
            "siblings": list(self.siblings),
            "atmosphere": self.atmosphere,
            "evidence": list(self.evidence)[:8],
            "manual": bool(self.manual),
        }


@dataclass
class EthicalMark:
    """伦理关系分析标记：中性提示，不做价值判断。"""
    flag: str = ""                   # 如 sibling_affection / ex_network_active / cross_gen_interest
    label: str = ""                  # 中文标签，如 兄弟姐妹好感 / 前任网络活跃
    risk_hint: str = ""              # 中性风险提示
    opportunity_hint: str = ""       # 中性机会提示
    evidence: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "flag": self.flag,
            "label": self.label,
            "risk_hint": self.risk_hint,
            "opportunity_hint": self.opportunity_hint,
            "evidence": list(self.evidence),
        }


def _family_is_unknown(fam: FamilyContext) -> bool:
    return (fam.family_structure == "未知" and fam.parents_status == "未知"
            and not fam.siblings and fam.atmosphere == "未知" and not fam.evidence)


@dataclass
class RelationshipNode:
    id: str; name: str; relation_type: str = "其他"
    intimacy: float = 30.0; mention_count: int = 1
    emotion_intensity: float = 0.3; last_mentioned: float = field(default_factory=time.time)
    x: float = 0.5; y: float = 0.5; z: float = 0.0
    # R3.3-79 新增
    faction: str = ""                       # 派系 id（compute_factions 写入）
    family: FamilyContext = field(default_factory=FamilyContext)
    ethical_marks: list = field(default_factory=list)   # list[EthicalMark]
    stance: float = 0.0                     # -1..1 节点立场


@dataclass
class RelationshipEdge:
    source_id: str; target_id: str; strength: float = 0.5; relation_type: str = "朋友"
    stance: float = 0.0                     # -1..1 对"我方与目标在一起"的立场


class RelationshipGraph:
    def __init__(self, center_id: str = "me"):
        self.center_id = center_id
        self.nodes: dict[str, RelationshipNode] = {}
        self.edges: list[RelationshipEdge] = []
        # R3.3-79 新增
        self.family_context = FamilyContext()   # 图级家庭背景（缺失字段保持"未知"）
        self.factions: dict = {}                # compute_factions 结果缓存

    def add_node(self, name: str, relation_type: str = "其他", intimacy: float = 30.0) -> str:
        nid = f"n_{len(self.nodes)}_{hash(name) & 0xffff}"
        self.nodes[nid] = RelationshipNode(id=nid, name=name, relation_type=relation_type, intimacy=intimacy)
        return nid

    def delete_node(self, nid: str):
        self.nodes.pop(nid, None)
        self.edges = [e for e in self.edges if e.source_id != nid and e.target_id != nid]

    def add_edge(self, s: str, t: str, strength: float = 0.5, rt: str = "朋友"):
        self.edges.append(RelationshipEdge(s, t, strength, rt))

    def set_relation_type(self, nid: str, rt: str):
        if nid in self.nodes: self.nodes[nid].relation_type = rt

    # ---------------- R3.3-79 NPC 次级网络 ---------------- #
    def add_npc_edge(self, source_id: str, target_id: str, strength: float = 0.5,
                     stance: float = 0.0, relation_type: str = "其他") -> None:
        """NPC↔NPC 边（不连 center），写入 self.edges。"""
        self.edges.append(RelationshipEdge(source_id, target_id, strength, relation_type, stance))

    # ---------------- R3.3-79 家庭背景 ---------------- #
    def set_family(self, nid, family: FamilyContext) -> None:
        if not nid:
            self.family_context = family
        elif nid in self.nodes:
            self.nodes[nid].family = family

    def set_family_dict(self, nid, d: dict) -> None:
        d = d or {}
        fam = FamilyContext(
            family_structure=d.get("family_structure", "未知") or "未知",
            parents_status=d.get("parents_status", "未知") or "未知",
            siblings=list(d.get("siblings") or []),
            atmosphere=d.get("atmosphere", "未知") or "未知",
            evidence=list(d.get("evidence") or [])[:8],
            manual=bool(d.get("manual", False)),
        )
        if not nid:
            self.family_context = fam
        elif nid in self.nodes:
            self.nodes[nid].family = fam

    def get_target_family(self, nid=None) -> FamilyContext:
        """节点 family 非全'未知'优先；否则返回图级 family_context；都未知返回全'未知'实例。"""
        if nid and nid in self.nodes:
            f = self.nodes[nid].family
            if not _family_is_unknown(f):
                return f
        if not _family_is_unknown(self.family_context):
            return self.family_context
        return FamilyContext()

    # ---------------- R3.3-79 伦理标记 ---------------- #
    def mark_ethical(self, nid, flag: str, label: str,
                     risk_hint: str = "", opportunity_hint: str = "",
                     evidence=None) -> None:
        node = self.nodes.get(nid)
        if node is None:
            return
        node.ethical_marks.append(EthicalMark(
            flag=flag, label=label, risk_hint=risk_hint,
            opportunity_hint=opportunity_hint,
            evidence=list(evidence or [])))

    # ---------------- R3.3-79 立场 ---------------- #
    def adjust_stance(self, nid, delta) -> float:
        n = self.nodes.get(nid)
        if n is None:
            return 0.0
        n.stance = max(-1.0, min(1.0, n.stance + float(delta)))
        return n.stance

    # ---------------- R3.3-79 派系聚类（ALG-06） ---------------- #
    def compute_factions(self, min_strength: float = 0.35) -> dict:
        """只基于 NPC↔NPC 边 + 节点 stance 聚类，不把 center 算入。
        返回 {f_id: faction_dict}，并写入 self.factions。
        """
        # a) 连接：两端都不是 center、strength 达标
        adj: dict[str, list] = {nid: [] for nid in self.nodes}
        for e in self.edges:
            if e.source_id == self.center_id or e.target_id == self.center_id:
                continue
            if e.source_id not in self.nodes or e.target_id not in self.nodes:
                continue
            if e.strength < min_strength:
                continue
            adj.setdefault(e.source_id, []).append(e.target_id)
            adj.setdefault(e.target_id, []).append(e.source_id)

        # b) 并查集连通分量
        parent = {n: n for n in self.nodes}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for s, ts in adj.items():
            rs = find(s)
            for t in ts:
                rt_ = find(t)
                if rs != rt_:
                    parent[rt_] = rs

        comps: dict[str, list] = {}
        for n in self.nodes:
            comps.setdefault(find(n), []).append(n)

        for n in self.nodes.values():
            n.faction = ""

        factions: dict = {}
        fid = 0
        for members in comps.values():
            if len(members) < 2:      # 孤立 NPC 不成派系
                continue
            stances = [self.nodes[m].stance for m in members]
            comp_stance = sum(stances) / len(stances)
            # c) label 按分量立场
            if comp_stance >= 0.2:
                label = "助攻派"
            elif comp_stance <= -0.2:
                label = "反对派"
            else:
                label = "中立派"
            # name：取 intimacy 最高的成员名
            lead_name = max(members, key=lambda m: self.nodes[m].intimacy)
            name = f"{label}·{self.nodes[lead_name].name}"
            # d) 意见领袖：|stance|*(连通度+1) 最大，平局取 intimacy 高者
            def _score(m):
                deg = len(adj.get(m, []))
                return (abs(self.nodes[m].stance) * (deg + 1), self.nodes[m].intimacy)
            ol = max(members, key=_score)
            risk_hint = "" if comp_stance > -0.2 else "反对派立场：需要差异化应对，不硬碰硬"
            opp_hint = "" if comp_stance < 0.2 else "助攻派立场：可借力放大正向声音"
            fid_key = f"f_{fid}"
            factions[fid_key] = {
                "id": fid_key, "name": name, "label": label,
                "stance": round(comp_stance, 4),
                "members": list(members), "opinion_leader": ol,
                "size": len(members),
                "risk_hint": risk_hint, "opportunity_hint": opp_hint,
            }
            for m in members:
                self.nodes[m].faction = fid_key
            fid += 1
        self.factions = factions
        return factions

    def get_factions(self) -> dict:
        return self.factions

    # ---------------- R3.3-79 关键人物+次级网络 ---------------- #
    def influential_with_network(self, person_id=None) -> list:
        """在 get_influential_people 语义上扩展，每个 NPC dict 增加派系/家庭/伦理标记。"""
        out = []
        for n in self.nodes.values():
            info = INFLUENCE_MAP.get(n.relation_type)
            if not info:
                continue
            fdict = self.factions.get(n.faction) if n.faction else None
            out.append({
                "name": n.name, "relation_type": n.relation_type,
                "intimacy": n.intimacy,
                "influence_on_strategy": info["strategy_hint"],
                "tag": info["tag"], "affection_delta": info["affection_delta"],
                "faction": n.faction or "",
                "faction_label": fdict["label"] if fdict else "",
                "faction_stance": fdict["stance"] if fdict else 0.0,
                "family": n.family.to_dict(),
                "ethical_marks": [m.to_dict() for m in n.ethical_marks],
            })
        return out

    def to_dict(self) -> dict:
        nodes_out = []
        for n in self.nodes.values():
            d = dict(vars(n))
            d["family"] = n.family.to_dict()
            d["ethical_marks"] = [m.to_dict() for m in n.ethical_marks]
            d["faction_stance"] = (self.factions.get(n.faction, {}).get("stance", 0.0)
                                   if n.faction else 0.0)
            nodes_out.append(d)
        return {"center": self.center_id,
                "nodes": nodes_out,
                "edges": [vars(e) for e in self.edges],
                "colors": REL_COLORS,
                "family_context": self.family_context.to_dict(),
                "factions": list(self.factions.values())}

    def relayout(self, iterations: int = 200, seed: int | None = None,
                 dims: int = 3) -> dict:
        """对当前图跑一次纯 Python 力导向布局，把坐标写回节点，返回 to_dict()。

        Args:
            iterations: 力导向迭代轮数（默认 200，纯 Python 足够收敛）。
            seed: 随机种子；传入固定值可复现布局。
            dims: 2=平面(x,y)；3=带 z 的 3D 感坐标。

        Returns:
            dict: 与 to_dict() 同构，但每个节点已带布局后的 x/y(/z)/size/color。
        """
        laid = layout_graph(self.to_dict(), iterations=iterations,
                            seed=seed, dims=dims)
        for nd in laid["nodes"]:
            nid = nd["id"]
            if nid in self.nodes:
                self.nodes[nid].x = nd["x"]
                self.nodes[nid].y = nd["y"]
                self.nodes[nid].z = nd.get("z", 0.0)
        return laid


# R3.3-79：本地证据式家庭线索兜底（persona_analyzer.extract_family_context 不可用时）
# 只提取聊天原文中直接出现的证据，无证据返回空 dict，绝不臆造。
_SIBLING_PATTERNS = [("我姐", "姐姐"), ("姐姐", "姐姐"), ("我哥", "哥哥"), ("哥哥", "哥哥"),
                     ("我弟", "弟弟"), ("弟弟", "弟弟"), ("我妹", "妹妹"), ("妹妹", "妹妹")]
_PARENT_STATUS_PATTERNS = [
    ("闹离婚", "离婚中"), ("在离婚", "离婚中"), ("分居", "分居"),
    ("已经离婚", "离异"), ("离了", "离异"), ("闹矛盾", "紧张"),
    ("关系紧张", "紧张"), ("吵架", "紧张"), ("很和睦", "和睦"),
]


def _local_family_context(messages: list) -> dict:
    sibs: list = []
    status = ""
    ev: list = []
    for m in messages:
        t = m.get("content", "") if isinstance(m, dict) else str(m)
        if not t:
            continue
        hit = False
        for kw, label in _SIBLING_PATTERNS:
            if kw in t and label not in sibs:
                sibs.append(label)
                hit = True
        for kw, label in _PARENT_STATUS_PATTERNS:
            if kw in t and not status:
                status = label
                hit = True
        if hit:
            ev.append(t)
    if not sibs and not status:
        return {}
    return {"siblings": sibs, "parents_status": status or "未知", "evidence": ev[:8]}


def extract_from_chats(person_id: str, messages: list) -> dict:
    """从聊天消息中自动识别人名/称谓，统计提及频率与情感强度。"""
    g = RelationshipGraph(center_id=person_id)
    counts: dict[str, int] = {}
    emo: dict[str, float] = {}
    for m in messages:
        text = m.get("content","") if isinstance(m, dict) else str(m)
        for app in APPELLATIONS:
            if app in text:
                counts[app] = counts.get(app, 0) + 1
                emo[app] = emo.get(app, 0) + sum(1 for w in EMOTION_WORDS if w in text) / 10
    # 常见中文姓名模式（2-3字，姓氏开头）
    for m in messages:
        text = m.get("content","") if isinstance(m, dict) else str(m)
        for name in re.findall(r"(?<![一-龥])([王李张刘陈杨赵黄周吴][一-龥]{1,2})(?![一-龥])", text)[:5]:
            if len(name) >= 2:
                counts[name] = counts.get(name, 0) + 1
    for name, c in sorted(counts.items(), key=lambda kv: -kv[1])[:20]:
        rt = "其他"
        nid = g.add_node(name, rt, min(100, 20 + c * 10 + emo.get(name, 0) * 20))
        g.nodes[nid].stance = STANCE_MAP.get(rt, 0.0)
        g.add_edge(person_id, nid, min(1.0, c / 5))
    # R3.3-79：家庭背景提取（惰性 import，失败时本地证据兜底；无证据保持"未知"）
    try:
        from services.persona_analyzer import extract_family_context
        ctx = extract_family_context(messages) or {}
    except Exception:
        ctx = _local_family_context(messages)
    if ctx:
        g.set_family_dict(None, ctx)
    return g.to_dict()


# ============ 策略联动：关键人物影响 ============
INFLUENCE_MAP = {
    "朋友": {"tag":"闺蜜/朋友认可","strategy_hint":"可生成\"先搞定闺蜜\"支线","affection_delta": +2},
    "前任": {"tag":"前任仍在联系","strategy_hint":"竞争预警+差异化反制","affection_delta": -5},
    "家人": {"tag":"家人态度","strategy_hint":"影响长期推进节奏","affection_delta": +1},
    "暧昧对象": {"tag":"存在其他暧昧对象","strategy_hint":"竞争分析+差异化","affection_delta": -3},
}

def get_influential_people(person_id: str, graph: dict | None = None) -> list[dict]:
    """返回关键人物列表（name/relation_type/intimacy/influence_on_strategy）。"""
    graph = graph or {"nodes": []}
    out = []
    for n in graph.get("nodes", []):
        rt = n.get("relation_type", "其他")
        info = INFLUENCE_MAP.get(rt)
        if info:
            out.append({"name": n["name"], "relation_type": rt,
                        "intimacy": n.get("intimacy", 30),
                        "influence_on_strategy": info["strategy_hint"],
                        "tag": info["tag"], "affection_delta": info["affection_delta"]})
    return out

def social_alert(influential: list[dict]) -> dict:
    """危机预警：检测前任/暧昧对象触发竞争预警。"""
    risks = [i for i in influential if i["relation_type"] in ("前任","暧昧对象")]
    level = "红" if any(i["relation_type"]=="前任" for i in risks) else ("橙" if risks else "无")
    return {"level": level, "risks": [i["name"] for i in risks]}


# ====================================================================== #
# R3.3-09 NRD Studio 关系星图：纯 Python 力导向布局（Fruchterman-Reingold）
# 节点=NPC/对象；边=关系（带强度/类型权重）。输出前端可直接用的节点/边坐标。
# 不依赖 numpy/networkx 等重型库；纯 math+random；seed 固定则布局可复现。
# R3.3-79：out_nodes/out_edges 透传新字段（family/ethical_marks/faction/faction_stance/stance）。
# ====================================================================== #
def layout_graph(graph: dict, iterations: int = 200, seed: int | None = None,
                 dims: int = 3, area: float = 10000.0) -> dict:
    """对一张关系图做力导向布局，返回带坐标的前端图数据。

    Args:
        graph: to_dict() 同构字典：{center, nodes:[{id,name,relation_type,intimacy,...}],
               edges:[{source_id,target_id,strength,relation_type,stance}]}。
        iterations: 迭代轮数（默认 200）。
        seed: 随机种子；None 则不固定。
        dims: 2=平面；3=额外算 z（3D 星图立体感）。
        area: 布局“画布面积”，k=sqrt(area/N) 控制节点间距。

    Returns:
        dict: {
          center, colors,
          nodes: [{id,name,relation_type,intimacy,x,y,z,size,color,
                   family,ethical_marks,faction,faction_stance,stance}],
          edges: [{source,target,strength,relation_type,width,stance}],
          layout: {dims, iterations, n_nodes, n_edges, converged}
        }
        - center 节点固定在原点 (0,0,0)；
        - 坐标为布局物理坐标（约 [-R,R]），前端自行缩放/居中。
    """
    nodes_in = list(graph.get("nodes", []) or [])
    edges_in = list(graph.get("edges", []) or [])
    center_id = graph.get("center", "")
    use_z = int(dims) >= 3
    rng = random.Random(seed)

    out_nodes: list[dict] = []
    pos: dict[str, list[float]] = {}
    n = len(nodes_in)
    k = math.sqrt(area / max(n, 1))          # 理想边长/斥力系数
    temp = math.sqrt(area) / 8.0             # 初始“温度”（最大单步位移）

    # ---- 1) 初始化坐标：中心在原点，其余均匀撒在一个圆上 ---- #
    for i, nd in enumerate(nodes_in):
        nid = nd["id"]
        if nid == center_id:
            pos[nid] = [0.0, 0.0, 0.0]
            continue
        ang = 2.0 * math.pi * (i / max(n, 1)) + rng.uniform(-0.2, 0.2)
        r = k * (1.2 + rng.random() * 0.6)
        z = rng.uniform(-0.5, 0.5) * k if use_z else 0.0
        pos[nid] = [math.cos(ang) * r, math.sin(ang) * r, z]

    # ---- 2) 规范化边（跳过悬空端点；去重） ---- #
    edges: list[tuple] = []
    seen = set()
    for e in edges_in:
        s = e.get("source_id") or e.get("source")
        t = e.get("target_id") or e.get("target")
        if s not in pos or t not in pos or s == t:
            continue
        key = tuple(sorted((s, t)))
        if key in seen:
            continue
        seen.add(key)
        strength = float(e.get("strength", 0.5) or 0.5)
        edges.append((s, t, strength, e.get("relation_type", "朋友"),
                      float(e.get("stance", 0.0) or 0.0)))

    ids = [nd["id"] for nd in nodes_in if nd["id"] in pos]
    disp: dict[str, list[float]] = {i: [0.0, 0.0, 0.0] for i in ids}

    # ---- 3) 力导向迭代 ---- #
    for it in range(max(1, int(iterations))):
        for d in disp.values():
            d[0] = d[1] = d[2] = 0.0
        # 节点两两斥力
        for a in range(len(ids)):
            ia = ids[a]
            pa = pos[ia]
            for b in range(a + 1, len(ids)):
                ib = ids[b]
                pb = pos[ib]
                dx = pa[0] - pb[0]
                dy = pa[1] - pb[1]
                dz = (pa[2] - pb[2]) if use_z else 0.0
                dist = math.sqrt(dx * dx + dy * dy + dz * dz) or 1e-6
                rep = (k * k) / dist            # Coulomb-like
                ux, uy, uz = dx / dist, dy / dist, (dz / dist) if use_z else 0.0
                disp[ia][0] += ux * rep; disp[ia][1] += uy * rep
                disp[ib][0] -= ux * rep; disp[ib][1] -= uy * rep
                if use_z:
                    disp[ia][2] += uz * rep; disp[ib][2] -= uz * rep
        # 边吸引力（强度越大拉得越紧）
        for s, t, strength, _rt, _st in edges:
            ps, pt = pos[s], pos[t]
            dx = ps[0] - pt[0]; dy = ps[1] - pt[1]
            dz = (ps[2] - pt[2]) if use_z else 0.0
            dist = math.sqrt(dx * dx + dy * dy + dz * dz) or 1e-6
            att = (dist * dist / k) * (0.5 + strength)   # Hooke-like，按强度加权
            ux, uy, uz = dx / dist, dy / dist, (dz / dist) if use_z else 0.0
            for frm, to in ((s, t), (t, s)):
                disp[frm][0] -= ux * att / 2.0
                disp[frm][1] -= uy * att / 2.0
                if use_z:
                    disp[frm][2] -= uz * att / 2.0
        # 向心引力（把游离节点拉回中心附近）+ 温度衰减位移上限
        decay = 1.0 - (it / max(1, int(iterations)))
        t = max(0.1, temp * decay)
        for i in ids:
            if i == center_id:
                continue
            p = pos[i]; d = disp[i]
            # 向心弱拉力
            d[0] += -p[0] * 0.02
            d[1] += -p[1] * 0.02
            if use_z:
                d[2] += -p[2] * 0.02
            # 限步长（不超过当前温度）
            dl = math.sqrt(d[0] * d[0] + d[1] * d[1] + (d[2] if use_z else 0.0) ** 2) or 1e-6
            step = min(dl, t) / dl
            p[0] += d[0] * step; p[1] += d[1] * step
            if use_z:
                p[2] += d[2] * step

    # ---- 4) 归一化到单位半径（最大半径 -> 1，中心恒在原点） ---- #
    max_r = 0.0
    for p in pos.values():
        r = math.sqrt(p[0] * p[0] + p[1] * p[1]
                      + ((p[2] * p[2]) if use_z else 0.0))
        if r > max_r:
            max_r = r
    scale = (1.0 / max_r) if max_r > 1e-6 else 1.0
    for p in pos.values():
        p[0] *= scale; p[1] *= scale
        if use_z:
            p[2] *= scale

    # ---- 5) 组装前端输出 ---- #
    for nd in nodes_in:
        nid = nd["id"]
        if nid not in pos:
            continue
        p = pos[nid]
        intimacy = float(nd.get("intimacy", 30) or 30)
        rt = nd.get("relation_type", "其他")
        out_nodes.append({
            "id": nid,
            "name": nd.get("name", nid),
            "relation_type": rt,
            "intimacy": intimacy,
            "x": round(p[0], 3),
            "y": round(p[1], 3),
            "z": round(p[2], 3) if use_z else 0.0,
            "size": round(6.0 + (intimacy / 100.0) * 18.0, 2),
            "color": REL_COLORS.get(rt, "#a855f7"),
            # R3.3-79 透传
            "family": nd.get("family", {}),
            "ethical_marks": nd.get("ethical_marks", []),
            "faction": nd.get("faction", ""),
            "faction_stance": nd.get("faction_stance", 0.0),
            "stance": nd.get("stance", 0.0),
        })

    out_edges = [{
        "source": s, "target": t, "strength": round(strength, 3),
        "relation_type": rt, "width": round(0.5 + strength * 2.5, 2),
        "stance": st,
    } for (s, t, strength, rt, st) in edges]

    return {
        "center": center_id,
        "colors": REL_COLORS,
        "nodes": out_nodes,
        "edges": out_edges,
        "layout": {
            "dims": 3 if use_z else 2,
            "iterations": int(iterations),
            "n_nodes": len(out_nodes),
            "n_edges": len(out_edges),
            "converged": True,
        },
    }
