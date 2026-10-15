# -*- coding: utf-8 -*-
"""R3.3-80 B 类：关系里程碑事件链（纯服务层，不 import PySide6）。

冻结 schema（见 r3380_spec.md）：
    MILESTONE_TASKS: dict[str, dict]
        每项: stage_gate(int) / difficulty / steps(list[str]) / reward(float) / script(str)
    class MilestoneChain:
        STAGE_ORDER: list[str]
        tasks_for_stage(stage) -> list[dict]
        next_milestones(current_stage) -> list[dict]
        create_milestone_tasks(task_engine, person_id, stage) -> list[dict]
        plan_breakup_cycle_intervention(rebound_pattern) -> list[dict]

铁律：
- 任务落库只走 task_engine.add_custom_task(person_id, title, source, affection_reward)；
  同标题去重由 add_custom_task 保证（返回 None 视为已存在/失败）。
- 彩礼/嫁妆只做关系场景下的沟通策略与边界提示，不输出任何违法/教唆内容。
- D 类循环干预强调止损与自我观察，不教唆复合或报复。
"""
from __future__ import annotations

from typing import Any, Optional

# 与 services/batch7_group1.py 的 LEVELS11 对齐（纯名字，不 import，避免耦合）。
STAGE_ORDER: list[str] = [
    "相识", "相知", "好友", "密友", "暧昧",
    "对象", "未婚夫妻", "夫妻", "伴侣", "孩子父母", "终身伴侣",
]

# 难度口径与 services/task_engine.py 的 TASK_DIFFICULTY_XP 对齐。
TASK_DIFFICULTY_XP = {"简单": 10, "普通": 25, "困难": 50, "地狱": 100}


# --------------------------------------------------------------------------- #
# 里程碑模板表（6 类核心 + 3 类沟通）
# --------------------------------------------------------------------------- #
# stage_gate: 在 STAGE_ORDER 中的下标，建议在该阶段及之后解锁。
MILESTONE_TASKS: dict[str, dict] = {
    # ---------------- 沟通类（婚前资源/关系谈判） ----------------
    "见家长流程": {
        "stage_gate": 5,  # 对象
        "difficulty": "普通",
        "steps": [
            "与对象对齐双方父母的基本情况（职业、性格、忌讳话题）",
            "商定首次见面的场合与时长（建议轻松饭局，不超过 3 小时）",
            "准备一份得体伴手礼（不贵重、有心意）",
            "见面后当晚与对象复盘：哪句话得体、哪件事踩雷",
        ],
        "reward": 6.0,
        "script": "见家长不是面试，是让对方家人看见你真实、稳定、尊重他们的样子。",
    },
    "彩礼沟通": {
        "stage_gate": 5,  # 对象
        "difficulty": "困难",
        "steps": [
            "先和对象私下对齐双方家庭的心理预期区间，不越位替家长报价",
            "明确这笔钱的用途与归属（是小家庭启动金、还是走个过场带回）",
            "把数字摊在桌面谈一轮，记录分歧点而不是当场压价",
            "涉及财产署名/共同负债时，只做合规风险提示，必要时咨询专业人士",
        ],
        "reward": 8.0,
        "script": "彩礼谈的不是面子，是两个家庭对这段婚姻的诚意与边界——能坐下来谈清楚，比谈成多少更重要。",
    },
    "嫁妆沟通": {
        "stage_gate": 5,  # 对象
        "difficulty": "困难",
        "steps": [
            "了解女方家庭愿意陪嫁的大致方向（现金/实物/房产）",
            "与对象确认嫁妆的使用边界：小家庭共有、还是女方个人资产",
            "不主动要求、不攀比，把沟通重点放在新家庭如何启动",
            "涉及大额财产混同时，仅做合规风险提示，不替任何一方做决定",
        ],
        "reward": 8.0,
        "script": "嫁妆是对方家庭的心意，不是你应得的筹码；尊重边界，关系才走得远。",
    },
    # ---------------- 6 类核心里程碑 ----------------
    "求婚": {
        "stage_gate": 6,  # 未婚夫妻
        "difficulty": "普通",
        "steps": [
            "确认双方对婚姻的共识（不是试探，是明确聊过一次）",
            "选定一个对两人有记忆点的场合（不追求排场，追求真诚）",
            "准备戒指或信物，预算在双方舒适区内",
            "开口前先深呼吸，说清楚你想和这个人走下去的具体理由",
        ],
        "reward": 10.0,
        "script": "求婚不是演给朋友圈看的，是两个人之间一个郑重的承诺。",
    },
    "订婚": {
        "stage_gate": 6,  # 未婚夫妻
        "difficulty": "普通",
        "steps": [
            "与双方家长敲定订婚日期与形式（家宴/仪式皆可）",
            "互赠信物并告知至亲好友，形成公开承诺",
            "商定婚后大致居住安排与近期生活节奏",
            "订婚后一周内，一起列一张「婚姻准备清单」",
        ],
        "reward": 10.0,
        "script": "订婚是把口头约定变成双方家庭都知道的事，仪式感不必贵，但要真。",
    },
    "领证": {
        "stage_gate": 7,  # 夫妻
        "difficulty": "简单",
        "steps": [
            "提前查好双方户籍所在民政局的预约要求与所需证件",
            "领证当天穿得整齐、带齐身份证与户口本原件",
            "领证后 24 小时内，一起吃一顿安静的饭，聊聊接下来的打算",
            "把结婚证收好，并告知最亲近的 1-2 位家人",
        ],
        "reward": 12.0,
        "script": "红本子是法律的开始，日子才是答案。",
    },
    "婚礼": {
        "stage_gate": 7,  # 夫妻
        "difficulty": "地狱",
        "steps": [
            "先定预算上限，再倒推宾客人数与场地，不为面子超支",
            "列出必须到场的人，其余按精力取舍",
            "婚礼前一周把流程、联系人、应急方案写成一页纸",
            "当天给自己留 10 分钟独处时间，记住这是两个人的日子",
        ],
        "reward": 15.0,
        "script": "婚礼是一天，婚姻是很多年；别让一天的疲惫透支很多年的感情。",
    },
    "买房": {
        "stage_gate": 7,  # 夫妻
        "difficulty": "地狱",
        "steps": [
            "两人先对齐「买在哪、多大、月供上限」三个数字",
            "算清楚首付来源、贷款资格与未来 5 年的现金流压力",
            "产权署名、出资比例、共同负债等事项，只做合规风险提示，必要时咨询专业人士",
            "签约前冷静 72 小时，避免冲动下单",
        ],
        "reward": 15.0,
        "script": "房子是住人的，不是绑架两个人的；月供别压到喘不过气。",
    },
    "备孕": {
        "stage_gate": 9,  # 孩子父母
        "difficulty": "困难",
        "steps": [
            "双方做一次基础体检，了解彼此身体状况",
            "一起调整作息、戒烟限酒、补充叶酸（遵医嘱）",
            "聊清楚育儿分工、经济准备与双方老人参与边界",
            "记录 1-2 个月基础身体数据，再决定下一步",
        ],
        "reward": 12.0,
        "script": "备孕是两个人的事，不是一个人的任务；先把日子过稳，再迎接新成员。",
    },
}


# --------------------------------------------------------------------------- #
# D 类复合循环干预任务模板（cycle_count>=2 时触发）
# --------------------------------------------------------------------------- #
_BREAKUP_CYCLE_TEMPLATES: list[dict] = [
    {
        "title": "记录一次分手冲动前的真实触发事件",
        "difficulty": "简单",
        "steps": [
            "下一次想提分手/复合时，先停笔 5 分钟",
            "写下：刚才那句话/那个动作，具体戳到了我哪个旧伤口",
            "区分「这件事本身」和「它让我联想到的过去」",
        ],
        "reward": 4.0,
        "script": "分手冲动往往不是当下这件事有多大，而是它按到了旧按钮。",
    },
    {
        "title": "设 72 小时冷静期再决定是否联系前任",
        "difficulty": "普通",
        "steps": [
            "想发消息的当下，把输入框内容存草稿但不发送",
            "72 小时后再看一遍草稿，问自己：这条消息是解决问题，还是缓解焦虑",
            "如果仍要发，只发一条事实性的、不带情绪的消息",
        ],
        "reward": 5.0,
        "script": "冷静期不是压抑，是给自己一个不被情绪牵着走的窗口。",
    },
    {
        "title": "列出这段关系里「重复出现的 3 个模式」",
        "difficulty": "普通",
        "steps": [
            "回忆近 3 次分手/和好的节点，写出各自导火索",
            "标出其中重复出现的台词、场景、人物",
            "只做观察记录，不急着下结论或怪对方",
        ],
        "reward": 5.0,
        "script": "模式被看见的那一刻，循环就已经松了一环。",
    },
    {
        "title": "为本周安排一件「只属于自己」的事",
        "difficulty": "简单",
        "steps": [
            "选一件不需要对方参与、自己一个人就能完成的事",
            "固定时间去做，不拍照、不报备、不分享",
            "事后记录做这件事时的情绪状态",
        ],
        "reward": 3.0,
        "script": "止损不是离开谁，是先把自己的生活支点捡回来。",
    },
    {
        "title": "梳理分手后反复纠缠的成本清单",
        "difficulty": "普通",
        "steps": [
            "写下最近 1 个月因反复拉扯消耗的时间/睡眠/工作状态",
            "写下同样的精力投入到自己身上，能做成哪 1 件小事",
            "把这张纸放在显眼处，下次冲动时再看一遍",
        ],
        "reward": 4.0,
        "script": "不是劝你立刻断，是让你看见自己一直在为什么付费。",
    },
]


class MilestoneChain:
    """关系里程碑事件链：模板查询 + 真实落库 + D 类循环干预规划。"""

    STAGE_ORDER: list[str] = STAGE_ORDER

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def _stage_index(self, stage: str) -> int:
        try:
            return self.STAGE_ORDER.index(stage)
        except ValueError:
            # 未知阶段名：保守按最低级处理，返回空模板
            return -1

    def tasks_for_stage(self, stage: str) -> list[dict]:
        """返回在该阶段及之后可解锁的里程碑模板（按 stage_gate 升序）。"""
        idx = self._stage_index(stage)
        if idx < 0:
            return []
        out = []
        for name, tpl in MILESTONE_TASKS.items():
            if tpl["stage_gate"] <= idx:
                item = {"name": name, **tpl}
                out.append(item)
        out.sort(key=lambda x: (x["stage_gate"], x["reward"]))
        return out

    def next_milestones(self, current_stage: str) -> list[dict]:
        """返回当前阶段之后（不含当前已解锁）的下一批里程碑。"""
        idx = self._stage_index(stage := current_stage)
        if idx < 0:
            return []
        out = []
        for name, tpl in MILESTONE_TASKS.items():
            if tpl["stage_gate"] == idx + 1:
                out.append({"name": name, **tpl})
        out.sort(key=lambda x: x["reward"])
        return out

    # ------------------------------------------------------------------ #
    # 真实落库（只走 task_engine.add_custom_task）
    # ------------------------------------------------------------------ #
    def create_milestone_tasks(self, task_engine: Any, person_id: str,
                               stage: str) -> list[dict]:
        """把该阶段可解锁的里程碑模板逐条调 add_custom_task 落库。

        返回实际创建成功的任务 dict 列表；同标题去重由 add_custom_task 保证。
        """
        created: list[dict] = []
        for tpl in self.tasks_for_stage(stage):
            title = f"[里程碑]{tpl['name']}"
            t = task_engine.add_custom_task(
                person_id=person_id,
                title=title,
                source="milestone_chain",
                affection_reward=float(tpl["reward"]),
            )
            if t is not None:
                # 把模板元数据附在返回 dict 上，便于上层展示/桥接消费
                t = dict(t)
                t["milestone"] = tpl["name"]
                t["difficulty"] = tpl["difficulty"]
                t["steps"] = tpl["steps"]
                t["script"] = tpl["script"]
                created.append(t)
        return created

    # ------------------------------------------------------------------ #
    # D 类：复合循环干预
    # ------------------------------------------------------------------ #
    def plan_breakup_cycle_intervention(self, rebound_pattern: dict) -> list[dict]:
        """根据复杂度画像的 rebound_pattern 输出干预任务模板列表。

        输入 dict（冻结 schema）:
            cycle_count: int
            entangled: bool
            ex_new_partner: bool
        输出：list[dict]，每项含 title/difficulty/steps/reward/script。
        cycle_count < 2 时返回空列表（无需干预）。
        """
        cycle_count = int(rebound_pattern.get("cycle_count", 0) or 0)
        if cycle_count < 2:
            return []

        entangled = bool(rebound_pattern.get("entangled", False))
        ex_new = bool(rebound_pattern.get("ex_new_partner", False))

        # 基础 5 条全给
        picks = [dict(t) for t in _BREAKUP_CYCLE_TEMPLATES]

        # 叠加层：纠缠深 → 加一条边界梳理
        if entangled:
            picks.append({
                "title": "梳理与前任的现实纠缠（物品/钱/社交圈）",
                "difficulty": "困难",
                "steps": [
                    "列出还挂在对方身上的物品、转账、共同账号",
                    "一件一件列出归还/结清/解绑方案",
                    "不追求一次了断，本周先处理最容易的那一件",
                ],
                "reward": 6.0,
                "script": "物理边界不清，情绪边界就立不住。",
            })

        # 叠加层：前任已有新伴侣 → 加一条自我锚定
        if ex_new:
            picks.append({
                "title": "停止视奸前任动态，替换为一次自我复盘",
                "difficulty": "普通",
                "steps": [
                    "今天先把相关账号静音/取关 7 天",
                    "想刷的那个瞬间，换成写 3 行自我状态记录",
                    "7 天后回看，评估焦虑是否下降",
                ],
                "reward": 4.0,
                "script": "对方的新故事不是你的判决书，你的注意力才是你自己的。",
            })

        return picks


# 模块级单例，方便桥/上层直接 import
chain = MilestoneChain()
