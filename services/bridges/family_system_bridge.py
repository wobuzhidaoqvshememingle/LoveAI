# -*- coding: utf-8 -*-
"""R3.3-80 A 类桥：家庭系统复杂度画像 ↔ 任务线（单向真写入）。

订阅 COMPLEXITY_PROFILE_UPDATED（由 complexity_publisher 发布），translate_in 提取
profile["family_system"]，handle 时按 A 类检测结果落真实任务（走 task_engine 公开 API
add_custom_task，铁律：不改 task_engine.py）：

  - 祖辈话语权 detected → "见家长支线：先经营祖辈（奶奶/姥姥/爷爷）认可度"类任务；
  - 继子女 detected     → "继子女接受度经营"任务；
  - 家庭负债 / 养老压力 detected → "关系节奏调整提示"任务（只提示节奏适配，不评判）。

可选星图接线：构造时若传入 graph 实例（RelationshipGraph），运行时调用其 add_node 把
祖辈/继子女作为 NPC 节点写进星图（运行时调用，绝不改 relationship_graph.py 文件）；
graph 为 None 时本桥仍落任务，只是不接星图。

软写入口径：与 relationship_task_bridge 一致，走 self.claim(..., PRI_COMPASS)（软提示，
不允许覆盖任务线主结构与好感硬值）。
"""
from __future__ import annotations

import logging

from services.bridges.base import Bridge
from services.bridges import events as ev
from services.bridges.arbiter import PRI_COMPASS

logger = logging.getLogger(__name__)

PRI_FAMILY = PRI_COMPASS  # 家庭系统软提示与罗盘同级，文档化


class FamilySystemBridge(Bridge):
    name = "family_system"

    def __init__(self, bus, task_engine, graph=None,
                 metrics=None, arbiter=None) -> None:
        super().__init__(bus, metrics, arbiter)
        self.task_engine = task_engine
        self.graph = graph                # RelationshipGraph 实例，可为 None
        self.last_family_system: dict = {}
        self.created_titles: list[str] = []
        self.graph_nodes_added: list[str] = []

    def subscribe_events(self) -> list[str]:
        return [ev.COMPLEXITY_PROFILE_UPDATED]

    # ------------------------------------------------------------------ #
    def translate_in(self, data: dict):
        pid = data.get("person_id")
        if not pid:
            return None
        # 优先取事件载荷里直接带的 family_system；没有则从 profile 里取
        family = data.get("family_system")
        if family is None:
            profile = data.get("profile") or {}
            family = profile.get("family_system")
        if not family:
            return None
        return {"person_id": pid, "family_system": family}

    def constrain(self, translated):
        return translated

    # ------------------------------------------------------------------ #
    def _soft_task(self, pid: str, title: str, source: str, reward: float) -> bool:
        """落一条家庭系统软任务并登记；先 claim 软写入（与罗盘同级）。"""
        self.claim("task_engine", "family_hint", title, PRI_FAMILY,
                   meta={"source": source})
        created = self.task_engine.add_custom_task(
            pid, title, source=source, affection_reward=reward)
        if created:
            self.created_titles.append(title)
            return True
        return False

    def _maybe_add_graph_node(self, name: str, relation_type: str,
                              intimacy: float = 35.0) -> None:
        """运行时把祖辈/继子女写进星图节点；graph 缺失或异常一律不炸业务。"""
        if self.graph is None or not name:
            return
        try:
            # 已存在同名节点则不重复建（与 relationship_task_bridge 的查法一致）
            for n in self.graph.nodes.values():
                if getattr(n, "name", None) == name:
                    return
            nid = self.graph.add_node(name, relation_type=relation_type,
                                      intimacy=intimacy)
            self.graph_nodes_added.append(nid)
        except Exception as e:  # noqa: BLE001
            logger.warning("family_system 写星图节点失败(%s): %s", name, e)

    # ------------------------------------------------------------------ #
    def handle(self, t: dict, raw: dict) -> bool:
        pid = t["person_id"]
        fam = t["family_system"] or {}
        self.last_family_system = fam
        acted = False

        # ---- 祖辈话语权 → 见家长支线：先经营祖辈认可度 ----
        grand = fam.get("grandparent_influence") or {}
        if grand.get("detected"):
            who = grand.get("who") or []
            who_txt = "/".join(who) if who else "祖辈"
            stance = grand.get("stance", "未知")
            title = (f"见家长支线：先经营祖辈（{who_txt}）认可度"
                     f"（当前立场：{stance}）")
            if self._soft_task(pid, title, "bridge_family_grandparent", 3.0):
                acted = True
            for g in (who or []):
                self._maybe_add_graph_node(g, relation_type="家人", intimacy=35.0)

        # ---- 继子女 → 接受度经营（接受度低时不强行改口，先做平行朋友）----
        step = fam.get("stepchildren") or {}
        if step.get("detected"):
            lvl = step.get("acceptance_level", "未知")
            title = (f"继子女接受度经营（当前接受度：{lvl}）："
                     f"先做平行朋友，不强行要求改口")
            if self._soft_task(pid, title, "bridge_family_stepchild", 3.0):
                acted = True
            self._maybe_add_graph_node("对方的孩子", relation_type="家人",
                                       intimacy=25.0)

        # ---- 家庭负债 / 养老压力 → 关系节奏调整提示（只提示节奏适配，不评判）----
        debt = fam.get("family_debt") or {}
        care = fam.get("caregiving_pressure") or {}
        rhythm_hints: list[str] = []
        if debt.get("detected"):
            rhythm_hints.append(
                f"存在家庭负债信号（{debt.get('level', '未知')}）：金钱相关节奏放慢，"
                f"涉财产仅做合规风险提示，不评判对方")
        if care.get("detected"):
            rhythm_hints.append(
                f"存在养老/照料压力（{care.get('level', '未知')}）：任务节奏避开对方"
                f"奔波照料期，长期规划预留讨论位，不催促")
        for hint in rhythm_hints:
            title = "关系节奏调整提示：" + hint
            if self._soft_task(pid, title, "bridge_family_rhythm", 2.0):
                acted = True

        return acted or bool(fam)  # 有家庭系统画像即算一次联动（去重返回 False 也认）

    # ------------------------------------------------------------------ #
    def network_summary(self) -> dict:
        return {
            "last_family_system_detected": {
                "grandparent": bool((self.last_family_system.get("grandparent_influence") or {}).get("detected")),
                "stepchildren": bool((self.last_family_system.get("stepchildren") or {}).get("detected")),
                "family_debt": bool((self.last_family_system.get("family_debt") or {}).get("detected")),
                "caregiving": bool((self.last_family_system.get("caregiving_pressure") or {}).get("detected")),
            },
            "created_tasks": list(self.created_titles),
            "graph_nodes_added": list(self.graph_nodes_added),
        }
