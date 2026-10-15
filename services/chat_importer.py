# -*- coding: utf-8 -*-
"""
聊天记录导入器（不依赖微信密钥的数据进门路径）

支持把用户手动粘贴/导出的聊天记录解析成结构化消息，写入 chat_messages 表，
随后 PersonaAnalyzer.analyze(person_id) 即可直接跑五层侧写。

支持格式：
  1. 纯文本标记行：  我: 内容  /  对方: 内容  /  她: / 他: / 你:
  2. 带时间戳：      2024-01-01 12:00 昵称: 内容
  3. 微信导出风格：   昵称 12:00\n内容（空行分隔）
  4. JSON 数组：     [{"speaker":"me"|"other","content":"...","time":"..."}]
  5. CSV：           speaker,time,content
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# 说话人别名 → direction
_ME_ALIASES = {"我", "自己", "me", "i", "my", "本人", "咱", "老子"}
_OTHER_ALIASES = {"对方", "她", "他", "你", "ta", "other", "target", "目标", "对象"}

# 时间戳前缀：2024-01-01 12:00 或 2024/1/1 12:00 或 12:00
_TS_RE = re.compile(
    r"^(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+)?\d{1,2}:\d{2}(:\d{2})?\s+(.+)$"
)


class ChatImporter:
    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def import_text(self, text: str, person_id: str,
                    person_nickname: str = "对方",
                    my_aliases: set = None,
                    other_aliases: set = None) -> dict:
        """解析纯文本聊天记录并入库。返回 {parsed, me, other, skipped}。"""
        msgs = self.parse_text(text, my_aliases, other_aliases)
        return self._save(person_id, person_nickname, msgs)

    def import_json(self, data, person_id: str,
                    person_nickname: str = "对方") -> dict:
        """导入 JSON 数组或 JSON 字符串。元素字段：speaker(me/other), content, time。"""
        if isinstance(data, str):
            data = json.loads(data)
        msgs = []
        for i, item in enumerate(data):
            sp = str(item.get("speaker", "")).lower()
            direction = "sent" if sp in ("me", "my", "i", "自己", "我") else "received"
            ts = item.get("time") or item.get("created_at") or datetime.now().isoformat()
            msgs.append({"direction": direction, "content": str(item.get("content", "")),
                         "created_at": str(ts), "msg_type": item.get("msg_type", "text")})
        return self._save(person_id, person_nickname, msgs)

    def import_file(self, path: str, person_id: str,
                    person_nickname: str = "对方") -> dict:
        """按扩展名自动识别 .txt/.json/.csv。"""
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
        if path.lower().endswith(".json"):
            return self.import_json(raw, person_id, person_nickname)
        if path.lower().endswith(".csv"):
            return self.import_csv(raw, person_id, person_nickname)
        return self.import_text(raw, person_id, person_nickname)

    def import_csv(self, text: str, person_id: str,
                   person_nickname: str = "对方") -> dict:
        msgs = []
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            sp = str(row.get("speaker", "")).lower()
            direction = "sent" if sp in ("me", "my", "i", "自己", "我") else "received"
            msgs.append({"direction": direction,
                         "content": str(row.get("content", "")),
                         "created_at": str(row.get("time", "") or datetime.now().isoformat()),
                         "msg_type": str(row.get("msg_type", "text"))})
        return self._save(person_id, person_nickname, msgs)

    # ------------------------------------------------------------------ #
    # 纯文本解析
    # ------------------------------------------------------------------ #
    def parse_text(self, text: str,
                   my_aliases: set = None,
                   other_aliases: set = None) -> list[dict]:
        me_set = _ME_ALIASES | (my_aliases or set())
        other_set = _OTHER_ALIASES | (other_aliases or set())

        msgs: list[dict] = []
        cur_direction: Optional[str] = None
        cur_content: list[str] = []
        cur_time = datetime.now().isoformat()

        def flush():
            nonlocal cur_content, cur_direction
            if cur_direction and cur_content:
                body = "\n".join(cur_content).strip()
                if body:
                    msgs.append({"direction": cur_direction, "content": body,
                                 "created_at": cur_time, "msg_type": "text"})
            cur_content = []

        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            if not line.strip():
                flush()
                cur_direction = None
                continue

            # 格式1/2：说话人: 内容  或  时间戳 说话人: 内容
            m = re.match(r"^(?:(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)\s+)?"
                         r"([^:：]{1,20})[:：]\s*(.*)$", line)
            if m:
                ts_str, speaker, body = m.group(1), m.group(2).strip(), m.group(3)
                direction = self._classify(speaker, me_set, other_set)
                if direction:
                    flush()
                    cur_direction = direction
                    if ts_str:
                        cur_time = self._norm_ts(ts_str)
                    if body.strip():
                        cur_content = [body.strip()]
                    continue

            # 格式3：微信导出  昵称 12:00（单独一行，下一行是内容）
            ts_m = _TS_RE.match(line)
            if ts_m and len(line) < 40:
                flush()
                speaker = ts_m.group(3).strip()
                cur_direction = self._classify(speaker, me_set, other_set) or "received"
                cur_time = self._norm_ts(line)
                continue

            # 续行：追加到当前消息
            if cur_direction:
                cur_content.append(line.strip())
            else:
                # 无说话人标记的孤立行，默认归为对方
                cur_direction = "received"
                cur_content = [line.strip()]

        flush()
        return msgs

    @staticmethod
    def _classify(speaker: str, me_set: set, other_set: set) -> Optional[str]:
        s = speaker.strip().lower()
        if s in {x.lower() for x in me_set}:
            return "sent"
        if s in {x.lower() for x in other_set}:
            return "received"
        return None

    @staticmethod
    def _norm_ts(ts: str) -> str:
        ts = ts.strip().replace("/", "-")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M:%S", "%H:%M"):
            try:
                dt = datetime.strptime(ts, fmt)
                if dt.year == 1900:
                    dt = dt.replace(year=datetime.now().year)
                return dt.isoformat()
            except ValueError:
                continue
        return datetime.now().isoformat()

    # ------------------------------------------------------------------ #
    # 入库
    # ------------------------------------------------------------------ #
    def _save(self, person_id: str, person_nickname: str,
              msgs: list[dict]) -> dict:
        if not msgs:
            return {"parsed": 0, "me": 0, "other": 0, "skipped": 0}

        # 确保 person 存在
        self.db.execute(
            "INSERT OR IGNORE INTO persons (id, nickname) VALUES (:id, :nickname)",
            {"id": person_id, "nickname": person_nickname})

        inserted = 0
        me_count = other_count = 0
        for m in msgs:
            content = (m.get("content") or "").strip()
            if not content:
                continue
            direction = m.get("direction", "received")
            # 去重：同人同方向同内容即视为重复（聊天记录里同一句话重复出现概率极低）
            existing = self.db.fetch_one(
                "SELECT id FROM chat_messages WHERE person_id=:pid AND content=:content "
                "AND direction=:dir LIMIT 1",
                {"pid": person_id, "content": content, "dir": direction})
            if existing:
                continue
            self.db.execute(
                "INSERT INTO chat_messages (person_id, direction, content, msg_type, created_at) "
                "VALUES (:pid, :dir, :content, :mtype, :ts)",
                {"pid": person_id, "dir": direction, "content": content,
                 "mtype": m.get("msg_type", "text"),
                 "ts": m.get("created_at", "") or datetime.now().isoformat()})
            inserted += 1
            if direction == "sent":
                me_count += 1
            else:
                other_count += 1

        logger.info("导入完成: person=%s 插入=%d (我=%d 对方=%d)",
                    person_id, inserted, me_count, other_count)
        return {"parsed": len(msgs), "inserted": inserted,
                "me": me_count, "other": other_count,
                "skipped": len(msgs) - inserted}
