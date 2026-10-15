# -*- coding: utf-8 -*-
"""桥：活关系星图/NPC ↔ 任务线（双向）。

方向 A（NPC 状态 → 任务决策）：收到 NPC_INFLUENCE_CHANGED，把关键人物（前任/暧昧对象
    = 竞争预警；闺蜜/朋友/家人 = 助攻）作为活数据注入任务决策——
    对竞争型 NPC 落一条"差异化反制/竞争预警"任务，对助攻型 NPC 落一条"借势助攻"任务。
方向 B（任务结果 → 回写星图）：任务完成后，把该对象节点的亲密度/边强度真实回写到
    RelationshipGraph（任务推进 = 关系星图节点亲密度上涨）。

仲裁：NPC 影响是"软策略提示"，优先级与罗盘同级（PRI_NPC=PRI_COMPASS=3），
    不允许覆盖任务线主结构与好感硬值。
"""
from __future__ import annotations

import logging

from services.bridges.base import Bridge
from services.bridges import events as ev
from services.bridges.arbiter import PRI_COMPASS

logger = logging.getLogger(__name__)

PRI_NPC = PRI_COMPASS  # NPC 软提示与罗盘同级，文档化

# 关系类型 → 派生任务模板（None 表示不派生任务）
_NPC_TASK_TEMPLATE = {
    "前任": ("竞争预警：前任仍在联系，做差异化反制（不纠缠、不降质）", 3.0),
    "暧昧对象": ("存在其他暧昧对象：拉差异、不摊牌，稳住自身节奏", 3.0),
    "朋友": ("借势助攻：争取她闺蜜/朋友的正面口碑", 2.0),
    "家人": ("经营她家人的好感（长期节奏，不冒进）", 2.0),
}


class RelationshipTaskBridge(Bridge):
    name = "npc_task"

    def __init__(self, bus, graph, task_engine, metrics=None, arbiter=None) -> None:
        super().__init__(bus, metrics, arbiter)
        self.graph = graph          # RelationshipGraph 实例
        self.task_engine = task_engine
        self.last_influence: list[dict] = []
        # R3.3-79：方向 A 创建任务 → NPC nid 的映射（供方向 B 完成后回写立场匹配）
        self._created: dict[str, dict] = {}     # title -> {"npc_name","nid"}
        self._last_stance_changes: list[dict] = []
        self._last_factions: list = []

    def subscribe_events(self) -> list[str]:
        return [
            ev.NPC_INFLUENCE_CHANGED,
            ev.TASKS_COMPLETED,
            ev.TASK_COMPLETED_BRIDGE,
            # R3.3-79：新订阅——仅做轻量记录/透传，不在此再发事件（防环）
            ev.NPC_STANCE_CHANGED,
            ev.NPC_FACTION_UPDATED,
            ev.FAMILY_CONTEXT_UPDATED,
        ]

    def translate_in(self, data: dict):
        et = data.get("_event_type")
        pid = data.get("person_id")
        if not pid:
            return None
        if et == ev.NPC_INFLUENCE_CHANGED:
            return {"dir": "npc_to_task", "person_id": pid,
                    "influential": data.get("influential") or []}
        if et in (ev.TASKS_COMPLETED, ev.TASK_COMPLETED_BRIDGE):
            return {"dir": "task_to_graph", "person_id": pid,
                    "title": data.get("title", "")}
        # R3.3-79：自身发出/外部同步的 NPC 元事件——仅记录，不派生、不再发（防环）
        if et == ev.NPC_STANCE_CHANGED:
            return {"dir": "npc_meta", "person_id": pid, "kind": "stance", "data": data}
        if et == ev.NPC_FACTION_UPDATED:
            return {"dir": "npc_meta", "person_id": pid, "kind": "factions", "data": data}
        if et == ev.FAMILY_CONTEXT_UPDATED:
            return {"dir": "npc_meta", "person_id": pid, "kind": "family", "data": data}
        return None

    def constrain(self, translated):
        return translated

    # ------------------------------------------------------------------ #
    # R3.3-79 辅助：按节点 name 找 nid； richer 图接口经 duck-typing 防御调用
    # ------------------------------------------------------------------ #
    def _nid_by_name(self, name: str):
        if not name:
            return None
        for nid, n in self.graph.nodes.items():
            if getattr(n, "name", None) == name:
                return nid
        return None

    @staticmethod
    def _field(obj, name: str, default=None):
        """dataclass 与 dict 双态取字段（生产图是 dataclass，测试夹具可能是 dict）。"""
        v = getattr(obj, name, None)
        if v is None and isinstance(obj, dict):
            v = obj.get(name)
        return v if v is not None else default

    def _soft_task(self, pid: str, title: str, source: str, reward: float,
                   npc_name: str | None = None, nid: str | None = None) -> bool:
        """落一条 NPC 软任务并登记 self._created；去重返回 None 时不算新建。"""
        self.claim("task_engine", "npc_hint", title, PRI_NPC,
                   meta={"source": source, "npc": npc_name})
        created = self.task_engine.add_custom_task(
            pid, title, source=source, affection_reward=reward)
        if created:
            self._created[title] = {"npc_name": npc_name, "nid": nid}
            return True
        return False

    def handle(self, t: dict, raw: dict) -> bool:
        pid = t["person_id"]

        # R3.3-79：NPC 元事件——仅记录，不再派生/不再发事件（防环）
        if t["dir"] == "npc_meta":
            d = t.get("data") or {}
            if t.get("kind") == "stance":
                self._last_stance_changes.append({
                    "npc_name": d.get("npc_name"),
                    "before": d.get("stance_before"),
                    "after": d.get("stance_after"),
                    "delta": d.get("delta"),
                })
            elif t.get("kind") == "factions":
                facs = d.get("factions") or []
                # 生产 get_factions() 返回 {fid: faction_dict}；统一成 list 保持输出稳定
                if isinstance(facs, dict):
                    facs = list(facs.values())
                self._last_factions = facs
            # family 透传：这里不派生任务，避免与方向 A 互相触发
            return True

        if t["dir"] == "npc_to_task":
            influential = t["influential"] or []
            self.last_influence = influential
            acted = False
            for npc in influential:
                rt = npc.get("relation_type", "")
                npc_name = npc.get("name") or ""
                nid = self._nid_by_name(npc_name)
                tpl = _NPC_TASK_TEMPLATE.get(rt)
                if tpl:
                    title, reward = tpl
                    if self._soft_task(pid, title, f"bridge_npc_{rt}", reward,
                                       npc_name=npc_name, nid=nid):
                        acted = True

                # ---- R3.3-79 方向 A：派系立场活数据（richer 图接口，缺失则跳过）----
                get_factions = getattr(self.graph, "get_factions", None)
                if nid and callable(get_factions):
                    try:
                        factions = get_factions() or []
                    except Exception:
                        factions = []
                    # 生产 get_factions() 返回 {fid: faction_dict}；list 形态也兼容
                    fac_list = list(factions.values()) if isinstance(factions, dict) else list(factions)
                    for fac in fac_list:
                        members = self._field(fac, "members") or []
                        if nid not in members:
                            continue
                        fstance = float(self._field(fac, "stance", 0.0) or 0.0)
                        if fstance <= -0.2:
                            ftitle = ("反对派立场：" + npc_name +
                                      "及小团体持负面立场，差异化应对、不硬碰硬")
                            if self._soft_task(pid, ftitle, "bridge_npc_faction", 3.0,
                                               npc_name=npc_name, nid=nid):
                                acted = True

                # ---- R3.3-79 方向 A：关系伦理/机会标记（node.ethical_marks）----
                node = self.graph.nodes.get(nid) if nid else None
                ethics = getattr(node, "ethical_marks", None) if node is not None else None
                if ethics:
                    for mark in ethics:
                        # 生产 EthicalMark dataclass：risk_hint/opportunity_hint/label；
                        # 测试夹具可能是 dict（含 legacy "kind"）。统一双态取值。
                        risk = self._field(mark, "risk_hint")
                        opp = self._field(mark, "opportunity_hint")
                        label = self._field(mark, "label", "") or ""
                        if not risk and isinstance(mark, dict) and mark.get("kind") == "risk_hint":
                            risk = label or "关系风险提示"
                        if not opp and isinstance(mark, dict) and mark.get("kind") == "opportunity_hint":
                            opp = label or "关系机会"
                        if risk:
                            # 冻结规格：标题以 label 短标签为主，hint 作括注解释；
                            # label 为空退回 hint；二者相同时不重复括注（legacy 夹具即此情形）
                            risk_s, label_s = str(risk), str(label)
                            shown = label_s or risk_s
                            if label_s and risk_s and risk_s != label_s:
                                shown = label_s + "（" + risk_s + "）"
                            etitle = "该关系带风险提示：" + shown + "，明确边界、不模糊关系"
                            if self._soft_task(pid, etitle, "bridge_npc_ethics", 2.0,
                                               npc_name=npc_name, nid=nid):
                                acted = True
                        elif opp:
                            opp_s, label_s = str(opp), str(label)
                            shown = label_s or opp_s
                            if label_s and opp_s and opp_s != label_s:
                                shown = label_s + "（" + opp_s + "）"
                            etitle = "可利用机会：" + shown + "，顺势但不冒进"
                            if self._soft_task(pid, etitle, "bridge_npc_ethics", 2.0,
                                               npc_name=npc_name, nid=nid):
                                acted = True

            # ---- R3.3-79 方向 A：图级目标家庭（一次影响事件只派生一次，中性提示）----
            get_family = getattr(self.graph, "get_target_family", None)
            if callable(get_family):
                try:
                    fam = get_family() or {}
                except Exception:
                    fam = {}
                # 生产 get_target_family() 返回 FamilyContext dataclass；dict 夹具也兼容
                ps = self._field(fam, "parents_status")
                fs = self._field(fam, "family_structure")
                if ps in ("离婚中", "分居", "紧张"):
                    ftitle = "她父母关系状态" + str(ps) + "：不卷入、稳住自身节奏、不评价"
                    if self._soft_task(pid, ftitle, "bridge_family", 2.0):
                        acted = True
                if fs == "单亲":
                    ftitle = "单亲家庭背景：尊重边界、不过度介入她与父母的关系"
                    if self._soft_task(pid, ftitle, "bridge_family", 2.0):
                        acted = True

            return acted or len(influential) > 0  # 有影响即算一次联动（去重返回 None 也认）

        # 方向 B：任务完成 → 回写星图（中心对象节点亲密度上涨）
        try:
            # ---- R3.3-79：按 title 匹配方向 A 创建的任务 → 回写对应 NPC 立场 ----
            title = t.get("title") or ""
            rec = self._created.get(title)
            if rec and rec.get("nid"):
                nid = rec["nid"]
                adj = getattr(self.graph, "adjust_stance", None)
                node = self.graph.nodes.get(nid)
                before = getattr(node, "stance", 0.0) if node is not None else 0.0
                if callable(adj):
                    try:
                        adj(nid, 0.15)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("adjust_stance 失败: %s", e)
                after = getattr(node, "stance", before + 0.15) if node is not None else before + 0.15
                # 发布前 reset_pass 防重复触发（参照现有测试用法）
                self.bus.reset_pass()
                self.bus.publish(ev.NPC_STANCE_CHANGED, {
                    "person_id": pid, "npc_name": rec.get("npc_name") or "",
                    "stance_before": before, "stance_after": after, "delta": 0.15})
                compute = getattr(self.graph, "compute_factions", None)
                if callable(compute):
                    try:
                        compute()
                    except Exception:  # noqa: BLE001
                        pass
                getf = getattr(self.graph, "get_factions", None)
                factions = getf() if callable(getf) else []
                self.bus.publish(ev.NPC_FACTION_UPDATED, {
                    "person_id": pid, "factions": factions})

            # 找中心对象节点（pid 对应节点）；没有则建一个
            center_node = None
            for n in self.graph.nodes.values():
                if n.name == pid:
                    center_node = n
                    break
            if center_node is None:
                self.graph.add_node(pid, relation_type="暧昧对象", intimacy=40.0)
            else:
                center_node.intimacy = min(100.0, center_node.intimacy + 2.0)
            return True
        except Exception as e:  # 星图回写异常不炸业务
            logger.warning("星图回写失败: %s", e)
            return False

    # ------------------------------------------------------------------ #
    def network_summary(self) -> dict:
        """可观测：本桥最近一次 NPC 影响、已派生任务数、立场变化与最新派系。"""
        return {
            "last_influence": self.last_influence,
            "created_tasks": len(self._created),
            "last_stance_changes": list(self._last_stance_changes),
            "last_factions": self._last_factions,
        }
