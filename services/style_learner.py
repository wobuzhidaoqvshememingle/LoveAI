"""
LoveAI v3.0 - 用户说话风格学习服务（StyleLearner）

数据流（本地为主体，云端仅"可选增强"，结果最终回到本地）：
1. 本地采集：从 chat_messages 中 direction='sent'（用户本人发出）的消息，
   用纯本地规则 + 统计提取说话特征（口头禅 / 语气词 / 句长 / 标点 / 表情 / 句式 / 节奏），
   全程不依赖云端、离线可用。
2. 本地存储：风格档案写入 user_speech_style 表（单行 id=1），并写一份可读 data/user_style.md。
3. 云端增强（可选）：把采样消息送 L3 云端做更抽象的风格归纳，结果合并回本地 cloud_summary；
   无云端 / 无网络 / 超时则直接跳过，本地规则档案照常工作。
4. 回到本地使用：build_style_context() 供回复生成注入 system prompt，
   让 3 条回复候选都"像用户本人会说的话"。

服务层纯 Python，不 import PySide6。
"""
import os
import re
import json
import logging
import threading
from datetime import datetime
from collections import Counter

from sqlalchemy import text

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 语气词（句中 / 句尾）
_TONE_PARTICLES = list("啊哦嗯呀吧嘛呢呗哈嘿唉喂咯噻喔嘞哇呐吆")

# 纯功能 / 停用字（用于过滤无意义 n-gram）
_STOP_CHARS = set("的了是我你他她它也就都很不有在和吗呢吧啊哦嗯呀嘛呗哈嘿唉喂个们这那会要能想"
                  "知道说去来吃喝看给把被让上下来里为对与及或是但也而又就还再很最太么着过地得")

# 常见 emoji Unicode 区间（简化匹配，避免引入第三方 emoji 库）
_EMOJI_RE = re.compile(
    "[" 
    "\U0001F300-\U0001FAFF"   # 符号、表情、运动、食物等
    "\U00002600-\U000027BF"   # 杂项符号、装饰符号
    "\U0001F1E6-\U0001F1FF"   # 国旗字母
    "]+",
    flags=re.UNICODE,
)
# 微信表情包占位文字，如 [捂脸][偷笑]
_WX_STICKER_RE = re.compile(r"\[[\u4e00-\u9fa5A-Za-z]{1,8}\]")


class StyleLearner:
    """学习用户本人说话习惯，产出可注入回复生成的本地风格档案。"""

    #: 累计多少条新的本人消息后自动重算
    UPDATE_EVERY = 20
    #: 云端增强采样上限
    CLOUD_SAMPLE = 40
    #: 多少条消息做一次云端增强
    CLOUD_EVERY = 200

    def __init__(self, db, llm=None, data_dir: str = None):
        """
        Args:
            db: Database 实例
            llm: LLMGateway 实例（可选，仅云端增强时使用）
            data_dir: 可读风格档案 user_style.md 的输出目录
        """
        self.db = db
        self.llm = llm
        self.data_dir = data_dir or os.path.join(_PROJECT_ROOT, "data")

    # ------------------------------------------------------------------ #
    # 数据采集
    # ------------------------------------------------------------------ #
    def _fetch_self_messages(self, limit: int = 5000) -> list[dict]:
        """读取用户本人发出（direction='sent'）的文本消息。

        大样本下只统计最近 ``limit`` 条（按时间倒序取最新，再反转为正序），
        避免长期用户只学到初识期的旧风格（修复：原 ASC LIMIT 会永远偏向前 5000 条）。
        """
        sql = (
            "SELECT content, created_at, msg_type FROM chat_messages "
            "WHERE direction='sent' AND content IS NOT NULL AND content != '' "
            "ORDER BY created_at DESC, id DESC LIMIT :lim"
        )
        rows = self.db.fetch_all(sql, {"lim": limit})
        rows.reverse()  # 恢复时间正序，保证回复间隔/活跃时段统计正确
        return rows

    @staticmethod
    def _count_visible_len(s: str) -> int:
        """去掉空白后的可见长度。"""
        return len(re.sub(r"\s", "", s or ""))

    def _extract_features(self, rows: list[dict]) -> dict:
        """从本人消息集合做本地统计，返回风格特征 dict。"""
        texts = [r["content"] for r in rows]
        n = len(texts)

        lengths = [self._count_visible_len(t) for t in texts]
        avg_len = round(sum(lengths) / n, 1) if n else 0
        sorted_len = sorted(lengths)
        median_len = sorted_len[n // 2] if n else 0
        short_pct = round(sum(1 for x in lengths if x < 10) / n, 2) if n else 0
        medium_pct = round(sum(1 for x in lengths if 10 <= x <= 30) / n, 2) if n else 0
        long_pct = round(sum(1 for x in lengths if x > 30) / n, 2) if n else 0

        # 语气词
        tone_counter: Counter = Counter()
        for t in texts:
            for p in _TONE_PARTICLES:
                c = t.count(p)
                if c:
                    tone_counter[p] += c
        tone_top = dict(tone_counter.most_common(8))

        # 句尾字（去掉末尾标点 / 空白 / emoji 后最后一个字）
        ending_counter: Counter = Counter()
        for t in texts:
            tail = _EMOJI_RE.sub("", t)
            tail = _WX_STICKER_RE.sub("", tail)
            tail = re.sub(r"[\s\.,。！!？?~～…\-—_、,;:：]+$", "", tail)
            if tail:
                ending_counter[tail[-1]] += 1
        endings_top = dict(ending_counter.most_common(8))

        # 口头禅 / 标志性短语：跨 ≥3 条消息出现的 2-3 字片段
        gram_doc_freq: Counter = Counter()
        for t in texts:
            cleaned = _WX_STICKER_RE.sub("", _EMOJI_RE.sub("", t))
            cleaned = re.sub(r"[^\u4e00-\u9fa5A-Za-z]", "", cleaned)
            grams = set()
            L = len(cleaned)
            for size in (2, 3):
                for i in range(L - size + 1):
                    seg = cleaned[i:i + size]
                    # 过滤纯停用字组合
                    if any(ch not in _STOP_CHARS for ch in seg):
                        grams.add(seg)
            for g in grams:
                gram_doc_freq[g] += 1
        min_docs = 3 if n >= 30 else 2
        catchphrases = [g for g, c in gram_doc_freq.most_common(15) if c >= min_docs][:10]

        # 标点习惯（按消息条数计密度）
        exclaim = sum(t.count("!") + t.count("！") for t in texts)
        question = sum(t.count("?") + t.count("？") for t in texts)
        ellipsis = sum(t.count("…") + t.count("。..") + len(re.findall(r"\.{2,}", t)) for t in texts)
        tilde = sum(t.count("~") + t.count("～") for t in texts)

        # 表情 / emoji / 表情包
        emoji_hits = _EMOJI_RE.findall("".join(texts))
        emoji_counter: Counter = Counter()
        for hit in emoji_hits:
            for ch in hit:
                if ord(ch) > 0x2000:
                    emoji_counter[ch] += 1
        sticker_hits = _WX_STICKER_RE.findall("".join(texts))
        sticker_counter = Counter(sticker_hits)
        emoji_msgs = sum(1 for t in texts if _EMOJI_RE.search(t))
        sticker_msgs = sum(1 for t in texts if _WX_STICKER_RE.search(t))
        emoji_top = [x for x, _ in emoji_counter.most_common(6)]
        sticker_top = [x for x, _ in sticker_counter.most_common(6)]

        # 句式
        question_msgs = sum(
            1 for t in texts
            if ("?" in t or "？" in t or re.search(r"[吗呢么]\s*[。.！!？?]?$", t.strip()))
        )
        exclaim_msgs = sum(1 for t in texts if "!" in t or "！" in t)
        # 短句堆叠：一条消息里出现多个换行 / 分句且每句都很短
        stack_msgs = 0
        for t in texts:
            parts = [p for p in re.split(r"[\n，,。！!？?；;]+", t) if p.strip()]
            if len(parts) >= 3 and sum(self._count_visible_len(p) for p in parts) / len(parts) < 6:
                stack_msgs += 1

        # 回复节奏：活跃时段 + 相邻消息间隔
        hour_counter: Counter = Counter()
        gaps = []
        prev_dt = None
        for r in rows:
            dt = self._parse_dt(r.get("created_at"))
            if dt:
                hour_counter[dt.hour] += 1
                if prev_dt:
                    delta = (dt - prev_dt).total_seconds()
                    if 0 <= delta < 6 * 3600:  # 只统计 6 小时内的连续对话间隔
                        gaps.append(delta)
                prev_dt = dt
        active_hours = [h for h, _ in hour_counter.most_common(4)]
        avg_gap = round(sum(gaps) / len(gaps), 0) if gaps else None

        profile = {
            "sample_count": n,
            "length": {
                "avg": avg_len, "median": median_len,
                "short_pct": short_pct, "medium_pct": medium_pct, "long_pct": long_pct,
            },
            "tone_particles": tone_top,
            "endings": endings_top,
            "catchphrases": catchphrases,
            "punctuation": {
                "exclaim": exclaim, "question": question,
                "ellipsis": ellipsis, "tilde": tilde,
                "exclaim_per_msg": round(exclaim / n, 2) if n else 0,
                "question_per_msg": round(question / n, 2) if n else 0,
            },
            "emoji": {
                "emoji_top": emoji_top, "sticker_top": sticker_top,
                "emoji_msg_pct": round(emoji_msgs / n, 2) if n else 0,
                "sticker_msg_pct": round(sticker_msgs / n, 2) if n else 0,
            },
            "sentence": {
                "question_pct": round(question_msgs / n, 2) if n else 0,
                "exclaim_pct": round(exclaim_msgs / n, 2) if n else 0,
                "short_stack_pct": round(stack_msgs / n, 2) if n else 0,
            },
            "rhythm": {
                "active_hours": sorted(active_hours),
                "avg_reply_gap_sec": avg_gap,
            },
        }
        profile["traits"] = self._derive_traits(profile)
        return profile

    @staticmethod
    def _parse_dt(value) -> datetime | None:
        """解析 SQLite datetime('now') 产生的时间字符串。"""
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        s = str(value).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y/%m/%d %H:%M"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def _derive_traits(p: dict) -> list[str]:
        """基于本地统计归纳出的可读风格标签（不含主观臆测，阈值即规则）。"""
        traits: list[str] = []
        length = p["length"]
        if length["avg"] and length["avg"] < 9:
            traits.append("简洁惜字")
        elif length["avg"] and length["avg"] > 30:
            traits.append("表达详尽")
        if length["short_pct"] >= 0.6:
            traits.append("短句为主")

        tone = p["tone_particles"]
        tone_total = sum(tone.values())
        if tone_total / max(p["sample_count"], 1) >= 0.8:
            traits.append("语气词多、口语化")

        punct = p["punctuation"]
        if punct["exclaim_per_msg"] >= 0.5:
            traits.append("情绪外放")
        if punct["question_per_msg"] >= 0.4:
            traits.append("常带追问")
        if punct["ellipsis"] / max(p["sample_count"], 1) >= 0.2:
            traits.append("含蓄留白")
        if punct["tilde"] / max(p["sample_count"], 1) >= 0.1:
            traits.append("语气拖音")

        em = p["emoji"]
        if em["emoji_msg_pct"] + em["sticker_msg_pct"] >= 0.4:
            traits.append("表情丰富")

        sent = p["sentence"]
        if sent["question_pct"] >= 0.35:
            traits.append("疑问互动")
        if sent["short_stack_pct"] >= 0.2:
            traits.append("连发短句、节奏快")

        if not traits:
            traits.append("平实中性")
        return traits

    # ------------------------------------------------------------------ #
    # 云端可选增强
    # ------------------------------------------------------------------ #
    def enrich_with_cloud(self, force: bool = False) -> str | None:
        """把采样的本人消息送 L3 做抽象风格归纳，结果存回本地。

        Returns:
            云端归纳文本；不可用 / 失败时返回 None（本地档案不受影响）。
        """
        if self.llm is None:
            return None
        try:
            if not force and not getattr(self.llm, "l3_available", lambda: False)():
                return None
        except Exception:
            # 无法判断 L3 状态时允许尝试一次
            pass

        rows = self._fetch_self_messages(limit=self.CLOUD_SAMPLE)
        if len(rows) < 10:
            return None
        sample = "\n".join(f"- {r['content']}" for r in rows[-self.CLOUD_SAMPLE:])

        system_prompt = (
            "你是语言风格分析师。请根据用户本人发过的消息，客观归纳其文字说话风格，"
            "只输出：①一句话总体风格（30字内）；②3-5个最显著的口语特征（如口头禅、语气词、句式、表情习惯）；"
            "③模仿该风格时应注意的要点。不要评价好坏，不要给恋爱建议。"
        )
        try:
            summary = self.llm.chat(system_prompt, f"用户本人消息样本：\n{sample}", complexity="complex")
        except Exception:
            logger.debug("云端风格增强失败", exc_info=True)
            return None
        if not summary:
            return None
        summary = summary.strip()
        # 云端只是"增强"，本地档案才是主体：确保 id=1 行存在
        existing = self.db.fetch_one("SELECT id FROM user_speech_style WHERE id=1")
        if existing:
            self.db.execute(
                "UPDATE user_speech_style SET cloud_summary=:s, source='local+cloud', "
                "updated_at=datetime('now') WHERE id=1",
                {"s": summary},
            )
        else:
            # 尚无本地档案，先做本地特征提取并把云端归纳一并存入
            rows = self._fetch_self_messages()
            if len(rows) >= 5:
                local_profile = self._extract_features(rows)
                self._save(local_profile, cloud_summary=summary)
        return summary

    # ------------------------------------------------------------------ #
    # 存储
    # ------------------------------------------------------------------ #
    def _save(self, profile: dict, cloud_summary: str | None = None) -> None:
        """UPSERT 风格档案到 user_speech_style。"""
        if cloud_summary is not None:
            profile["cloud_summary"] = cloud_summary
        else:
            existing = self.get_profile()
            if existing and existing.get("cloud_summary"):
                profile["cloud_summary"] = existing["cloud_summary"]

        source = "local+cloud" if profile.get("cloud_summary") else "local"
        self.db.execute(
            "INSERT INTO user_speech_style (id, profile_json, sample_count, avg_length, cloud_summary, source, updated_at) "
            "VALUES (1, :pj, :sc, :al, :cs, :src, datetime('now')) "
            "ON CONFLICT(id) DO UPDATE SET profile_json=:pj, sample_count=:sc, avg_length=:al, "
            "cloud_summary=:cs, source=:src, updated_at=datetime('now')",
            {
                "pj": json.dumps(profile, ensure_ascii=False),
                "sc": profile["sample_count"],
                "al": profile["length"]["avg"],
                "cs": profile.get("cloud_summary"),
                "src": source,
            },
        )
        self._write_markdown(profile)

    def _write_markdown(self, profile: dict) -> None:
        """写一份可读的 data/user_style.md。"""
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            path = os.path.join(self.data_dir, "user_style.md")
            lines = [
                "# 用户说话风格档案（本地学习）",
                f"- 样本数：{profile['sample_count']}",
                f"- 平均句长：{profile['length']['avg']} 字（中位数 {profile['length']['median']}）",
                f"- 风格标签：{ '、'.join(profile['traits']) }",
                f"- 高频语气词：{ '、'.join(profile['tone_particles'].keys()) or '无' }",
                f"- 高频句尾：{ '、'.join(profile['endings'].keys()) or '无' }",
                f"- 标志性短语：{ '、'.join(profile['catchphrases']) or '无' }",
                f"- 常用 emoji：{ '、'.join(profile['emoji']['emoji_top']) or '无' }",
                f"- 常用表情包：{ '、'.join(profile['emoji']['sticker_top']) or '无' }",
                f"- 活跃时段：{ '、'.join(str(h)+'点' for h in profile['rhythm']['active_hours']) or '无' }",
            ]
            if profile.get("cloud_summary"):
                lines += ["", "## 云端增强归纳", profile["cloud_summary"]]
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception:
            logger.debug("写 user_style.md 失败", exc_info=True)

    def get_profile(self) -> dict | None:
        """读取本地风格档案（解析后的 dict），无档案返回 None。"""
        row = self.db.fetch_one(
            "SELECT profile_json, sample_count, cloud_summary, source, updated_at "
            "FROM user_speech_style WHERE id=1"
        )
        if not row or not row.get("profile_json"):
            return None
        try:
            profile = json.loads(row["profile_json"])
        except Exception:
            return None
        if row.get("cloud_summary"):
            profile["cloud_summary"] = row["cloud_summary"]
        profile["_source"] = row.get("source")
        profile["_updated_at"] = row.get("updated_at")
        return profile

    # ------------------------------------------------------------------ #
    # 对外主流程
    # ------------------------------------------------------------------ #
    def learn_now(self, use_cloud: bool = True) -> dict:
        """立即重新学习一次（本地统计必做，云端增强按条件可选）。"""
        rows = self._fetch_self_messages()
        if len(rows) < 5:
            return {"ok": True, "learned": False, "reason": "样本不足（<5 条本人消息）",
                    "sample_count": len(rows)}
        profile = self._extract_features(rows)

        cloud_summary = None
        if use_cloud:
            # 仅在首次、或每 CLOUD_EVERY 条时做云端增强，省 token
            existing = self.get_profile()
            already_cloud = bool(existing and existing.get("cloud_summary"))
            due = (not already_cloud) or (profile["sample_count"] % self.CLOUD_EVERY < self.UPDATE_EVERY)
            if due:
                cloud_summary = self.enrich_with_cloud()

        self._save(profile, cloud_summary)
        logger.info("说话风格档案已更新：样本 %d，标签 %s",
                    profile["sample_count"], "/".join(profile["traits"]))
        return {"ok": True, "learned": True, "sample_count": profile["sample_count"],
                "traits": profile["traits"], "cloud_used": bool(profile.get("cloud_summary"))}

    def maybe_update(self) -> dict:
        """自动触发：本人消息比档案新增 ≥ UPDATE_EVERY 条时重算。

        本地统计同步快速完成；云端增强放到独立 daemon 线程，绝不阻塞分析主链路。
        """
        total = len(self._fetch_self_messages())
        profile = self.get_profile()
        last = profile["sample_count"] if profile else 0
        if total - last >= self.UPDATE_EVERY or (total >= 5 and profile is None):
            r = self.learn_now(use_cloud=False)
            if r.get("learned"):
                self._schedule_cloud_if_due()
            return r
        return {"ok": True, "learned": False, "sample_count": total}

    def _schedule_cloud_if_due(self) -> None:
        """云端增强到期时，在独立 daemon 线程执行，不阻塞调用方（分析流水线）。"""
        if self.llm is None:
            return
        p = self.get_profile()
        if not p:
            return
        already_cloud = bool(p.get("cloud_summary"))
        due = (not already_cloud) or (p["sample_count"] % self.CLOUD_EVERY < self.UPDATE_EVERY)
        if not due:
            return

        def _job():
            try:
                self.enrich_with_cloud()
            except Exception:
                logger.debug("后台云端风格增强失败", exc_info=True)

        threading.Thread(target=_job, daemon=True, name="style_cloud_enhance").start()

    def build_style_context(self) -> str:
        """生成注入回复生成 system prompt 的"口吻模仿"文本。

        无档案（样本不足）时返回保守提示，不影响正常生成。
        """
        p = self.get_profile()
        if not p or p.get("sample_count", 0) < 5:
            return "暂无足够的本人说话样本，按自然、得体的方式生成回复。"

        parts = [f"平均句长约 {p['length']['avg']} 字，风格偏{'、'.join(p['traits'])}"]
        if p["tone_particles"]:
            parts.append("高频语气词：" + "、".join(list(p["tone_particles"].keys())[:5]))
        if p["catchphrases"]:
            parts.append("标志性说法：" + "、".join(p["catchphrases"][:5]))
        if p["emoji"]["emoji_top"] or p["emoji"]["sticker_top"]:
            ex = "、".join((p["emoji"]["emoji_top"] + p["emoji"]["sticker_top"])[:5])
            parts.append("常用表情：" + ex)
        if p.get("cloud_summary"):
            first_line = p["cloud_summary"].splitlines()[0][:60]
            parts.append("风格归纳：" + first_line)

        body = "；".join(parts)
        return (
            "【用户本人说话习惯】" + body + "。"
            "请让 3 条回复候选都模仿该用户本人的口吻（用词长短、语气词、口头禅、表情习惯），"
            "做到像他自己会说的话，而不是统一的书面腔。"
        )
