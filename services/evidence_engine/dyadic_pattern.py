# -*- coding: utf-8 -*-
"""第5层 二元互动层（dyadic_pattern）。

在消息序列上识别双方行为互锁模式（二元关系是两个认知-情感系统的互锁，
Zayas/Shoda/Ayduk），输出模式及证据，而不只是单方画像：
  - 追逃 pursue-withdraw：我追问/冲突 → 对方回避/冷淡/长延迟；
  - 推拉 push-pull：对方先理想化(夸) 紧接 抛弃威胁/贬低，反复摆荡；
  - 修复循环 repair cycle：冲突后 对方修复(道歉/共识) vs 冷战失败；
  - 理想化→贬低→冷战/Hoovering：NPD 式关系弧（先捧→后踩→冷→回头撩）。

诚实：以上为确定性规则识别，输出模式名 + 证据(idx/ts) + 计数。
"""
from datetime import datetime

_PURSUE = ("为什么不回", "怎么不回", "你到底", "说清楚", "你说话", "怎么没回",
           "怎么不回我")
_WITHDRAW = ("算了", "行吧", "随便", "我累了", "不想说", "你说什么就是什么",
             "跟你说不清楚", "看情况吧", "无所谓")
_IDEALIZE = ("特别", "最好", "从没遇到", "安心", "唯一", "你最好了")
_DEVALUE = ("你太敏感", "你就是", "控制欲", "掉价", "侦探", "敏感", "不配",
            "不在乎")
_ABANDON = ("离开", "活不下去", "不要我", "喜欢上别人", "走了")
_REPAIR = ("对不起", "我也有不对", "是我不好", "我错了", "好好沟通", "聊聊")


class DyadicPattern:
    def detect(self, msgs: list) -> list:
        if not msgs:
            return []
        patterns = []
        pw = self._pursue_withdraw(msgs)
        if pw["count"] > 0:
            patterns.append(pw)
        pp = self._push_pull(msgs)
        if pp["count"] > 0:
            patterns.append(pp)
        rc = self._repair_cycle(msgs)
        patterns.append(rc)  # 修复循环总是输出（成败都有信息量）
        arc = self._npd_arc(msgs)
        if arc["count"] > 0:
            patterns.append(arc)
        return patterns

    def _pursue_withdraw(self, msgs):
        evidence, count = [], 0
        for i in range(1, len(msgs)):
            if msgs[i - 1]["direction"] == "sent" and msgs[i]["direction"] == "received":
                mine, hers = msgs[i - 1]["content"], msgs[i]["content"]
                if any(w in mine for w in _PURSUE) and any(w in hers for w in _WITHDRAW):
                    count += 1
                    if len(evidence) < 3:
                        evidence.append({"idx": msgs[i]["idx"], "ts": msgs[i]["ts"],
                                         "mine": mine, "hers": hers})
        return {"pattern": "追逃模式(我追对方逃)", "count": count, "evidence": evidence}

    def _push_pull(self, msgs):
        evidence, count = [], 0
        recv = [m for m in msgs if m["direction"] == "received" and m["content"]]
        for i in range(1, len(recv)):
            prev, cur = recv[i - 1]["content"], recv[i]["content"]
            is_pull = any(w in prev for w in _IDEALIZE) and \
                (any(w in cur for w in _ABANDON) or any(w in cur for w in _DEVALUE))
            is_push = any(w in prev for w in _DEVALUE) and \
                any(w in cur for w in _IDEALIZE)
            if is_pull or is_push:
                count += 1
                if len(evidence) < 3:
                    evidence.append({"idx": recv[i]["idx"], "ts": recv[i]["ts"],
                                     "prev": prev, "cur": cur})
        return {"pattern": "推拉模式(理想化↔抛弃威胁摆荡)", "count": count, "evidence": evidence}

    def _repair_cycle(self, msgs):
        success, fail, ev = 0, 0, []
        for m in msgs:
            if m["direction"] != "received":
                continue
            if any(w in m["content"] for w in _REPAIR):
                success += 1
                if len(ev) < 2:
                    ev.append({"idx": m["idx"], "ts": m["ts"], "content": m["content"],
                               "outcome": "修复"})
            elif any(w in m["content"] for w in _WITHDRAW):
                fail += 1
                if len(ev) < 3:
                    ev.append({"idx": m["idx"], "ts": m["ts"], "content": m["content"],
                               "outcome": "冷战"})
        return {"pattern": "修复循环(修复vs冷战)", "repair_success": success,
                "repair_fail": fail, "evidence": ev}

    def _npd_arc(self, msgs):
        recv = [m["content"] for m in msgs if m["direction"] == "received" and m["content"]]
        joined = " ".join(recv)
        has_ideal = any(w in joined for w in _IDEALIZE)
        has_deval = any(w in joined for w in _DEVALUE)
        has_cold = any(w in joined for w in _WITHDRAW)
        count = sum([has_ideal, has_deval, has_cold])
        evidence = []
        for m in msgs:
            if m["direction"] == "received" and \
               (any(w in m["content"] for w in _IDEALIZE + _DEVALUE + _WITHDRAW)):
                evidence.append({"idx": m["idx"], "ts": m["ts"], "content": m["content"]})
                if len(evidence) >= 4:
                    break
        return {"pattern": "理想化→贬低→冷战(NPD式关系弧)", "count": count,
                "evidence": evidence}
