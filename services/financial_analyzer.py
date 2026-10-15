"""
LoveAI v2.0 - 财务分析引擎
需求任务的前置检查：从聊天数据中提取对方财务信号，生成分析报告，判断需求可行性。
底线：绝不让对方超负荷或借贷。
"""
import json
import re
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)


class FinancialAnalyzer:
    """财务分析器：聊天信号提取 + LLM综合分析 + 需求可行性前置检查。

    核心底线：任何需求任务在创建前必须经过本引擎检查，
    绝不允许让对方因为满足需求而借贷或经济超负荷。
    """

    # ------------------------------------------------------------------ #
    # 正则信号词典
    # ------------------------------------------------------------------ #
    # 收入相关
    _INCOME_PATTERNS: list[tuple[str, str]] = [
        (r'工资.{0,8}(\d+)[千百]?', 'income'),
        (r'月薪|月入|到手|税后', 'income'),
        (r'奖金|年终奖|分红|绩效', 'income'),
        (r'副业|兼职|外快', 'income'),
    ]

    # 消费相关
    _CONSUMPTION_PATTERNS: list[tuple[str, str]] = [
        (r'买了.{0,10}(手机|电脑|包|鞋|衣服|表|首饰)', 'consumption'),
        (r'(人均|花了|消费).{0,8}(\d+)', 'consumption'),
        (r'(耐克|阿迪|苹果|华为|星巴克|海底捞|LV|Gucci|普拉达|香奈儿)', 'brand'),
    ]

    # 经济压力 / 储蓄
    _STRESS_PATTERNS: list[tuple[str, str]] = [
        (r'房贷|车贷|房租', 'stress'),
        (r'欠款|借钱|贷款|还债|网贷', 'stress'),
        (r'没钱|穷|吃土|手头紧|月光', 'stress'),
        (r'攒钱|存钱|省钱|存款', 'saving'),
    ]

    # 大额往来：转账 / 红包 / 礼物（早期大额支出识别）
    _TRANSFER_PATTERNS: list[tuple[str, str]] = [
        (r'转账|发红包|收红包', 'transfer'),
        (r'礼物|送礼|项链|首饰', 'gift'),
    ]

    def __init__(self, db: Any, llm: Any) -> None:
        """
        初始化财务分析引擎。

        Args:
            db: Database 实例（提供 fetch_one / fetch_all / execute 方法）。
            llm: LLMGateway 实例（提供 chat 方法）；传 None 则走规则兜底。
        """
        self.db = db
        self.llm = llm

        # 确保财务相关表存在（幂等）
        self._ensure_tables()

        logger.info("FinancialAnalyzer 初始化完成")

    # ------------------------------------------------------------------ #
    # 表结构保障
    # ------------------------------------------------------------------ #
    def _ensure_tables(self) -> None:
        """确保 financial_reports 与 expense_records 表存在（CREATE IF NOT EXISTS）。"""
        try:
            self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS financial_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person_id TEXT,
                    income_estimate TEXT,
                    disposable_income TEXT,
                    consumption_level TEXT,
                    large_purchase_capacity TEXT,
                    stress_index INTEGER,
                    suggested_amount_limit REAL,
                    raw_signals TEXT,
                    report_json TEXT,
                    created_at TEXT
                )
                """
            )
            self.db.execute(
                """
                CREATE TABLE IF NOT EXISTS expense_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person_id TEXT,
                    amount REAL,
                    description TEXT,
                    direction TEXT DEFAULT 'sent',
                    created_at TEXT
                )
                """
            )
        except Exception as e:
            logger.warning("财务相关表创建失败（可能已存在）: %s", e)

    # ------------------------------------------------------------------ #
    # 主入口：生成财务报告
    # ------------------------------------------------------------------ #
    def generate_report(self, person_id: str) -> dict:
        """
        生成对方财务分析报告。

        流程：
            1. _extract_signals 从聊天记录提取财务信号
            2. 从 persons 表提取职业信息
            3. LLM 综合分析（失败则走 _estimate_from_signals 兜底）
            4. 保存到 financial_reports 表
            5. 返回报告 dict，附加 raw_signals

        Args:
            person_id: 对方人物 ID。

        Returns:
            dict: 财务报告，包含 income_estimate / disposable_income /
                  consumption_level / large_purchase_capacity / stress_index /
                  suggested_amount_limit / summary / raw_signals。
        """
        # 1. 提取聊天信号
        signals = self._extract_signals(person_id)

        # 2. 提取人物档案中的职业信息
        profile = self._fetch_profile(person_id)
        occupation = profile.get("occupation", "未知") if profile else "未知"

        # 3. LLM 综合分析
        report: Optional[dict] = None
        if self.llm is not None:
            try:
                report = self._llm_analyze(occupation, signals)
            except Exception as e:
                logger.warning("LLM 财务分析失败，走规则兜底: %s", e)
                report = None

        if report is None:
            report = self._estimate_from_signals(signals)

        # 附加原始信号
        report["raw_signals"] = signals

        # 4. 保存到 financial_reports 表
        self._save_report(person_id, report, signals)

        logger.info(
            "财务报告生成: person=%s, 消费水平=%s, 压力指数=%s, 建议上限=%.0f",
            person_id, report.get("consumption_level"),
            report.get("stress_index"), report.get("suggested_amount_limit", 0),
        )
        return report

    # ------------------------------------------------------------------ #
    # 信号提取
    # ------------------------------------------------------------------ #
    def _extract_signals(self, person_id: str) -> list:
        """
        全量扫描聊天记录，提取财务相关信号（不再只看最近200条）。

        性能优化：先用 SQL LIKE 在数据库侧预过滤含金额关键词的候选消息，
        仅对命中的候选逐条走正则解析，避免对全部消息跑正则。
        兼容表名：先试 chat_messages（direction 列），再试 messages（sender 列）。

        Args:
            person_id: 对方人物 ID。

        Returns:
            list[dict]: 信号列表，每条含 type / text / sender；无记录时返回空列表。
        """
        messages = self._fetch_financial_candidates(person_id)
        if not messages:
            return []

        signals: list[dict] = []
        all_patterns = (
            self._INCOME_PATTERNS
            + self._CONSUMPTION_PATTERNS
            + self._STRESS_PATTERNS
            + self._TRANSFER_PATTERNS
        )

        for msg in messages:
            text = (msg.get("content") or "").strip()
            if not text:
                continue
            # sender 兼容：chat_messages 用 direction，messages 用 sender
            sender = msg.get("direction") or msg.get("sender") or "unknown"

            for pattern, sig_type in all_patterns:
                if re.search(pattern, text):
                    signals.append({
                        "type": sig_type,
                        "text": text[:100],
                        "sender": sender,
                    })
                    break  # 每条消息只取第一个命中的信号类型

        logger.debug("提取财务信号 %d 条 (person=%s)", len(signals), person_id)
        return signals

    # ------------------------------------------------------------------ #
    # 需求可行性前置检查
    # ------------------------------------------------------------------ #
    def check_demand_feasibility(
        self, person_id: str, item_value: float, item_name: str
    ) -> dict:
        """
        需求任务前置检查：判断对方能不能承担这个需求。

        底线逻辑（ratio = item_value / suggested_amount_limit）：
            - ratio < 0.3   -> low 风险，feasible=True
            - 0.3 ~ 0.6     -> medium，feasible=True 但建议分期
            - 0.6 ~ 1.0     -> high，feasible=False 建议降低标准
            - ratio > 1.0   -> dangerous，feasible=False，明确警告不可借贷

        Args:
            person_id: 对方人物 ID。
            item_value: 需求物品预估金额（元）。
            item_name: 需求物品名称。

        Returns:
            dict: feasible / risk_level / ratio / suggestion / warning。
        """
        # 取最新报告，没有则生成
        report = self.get_latest_report(person_id)
        if report is None:
            report = self.generate_report(person_id)

        limit = float(report.get("suggested_amount_limit") or 0)

        # 防止除零：建议上限为0视为完全无承受能力
        if limit <= 0:
            ratio = float("inf")
        else:
            ratio = item_value / limit

        # 按 ratio 分档
        if ratio < 0.3:
            risk_level = "low"
            feasible = True
            suggestion = f"{item_name}（约{item_value:.0f}元）金额在对方可承受范围内（上限{limit:.0f}元，占比{ratio*100:.0f}%）"
            warning = ""
        elif ratio < 0.6:
            risk_level = "medium"
            feasible = True
            suggestion = f"{item_name}（约{item_value:.0f}元）偏高，占建议上限{ratio*100:.0f}%，建议分阶段或分期进行"
            warning = ""
        elif ratio <= 1.0:
            risk_level = "high"
            feasible = False
            suggestion = f"{item_name}（约{item_value:.0f}元）占建议上限{ratio*100:.0f}%，超出舒适承受范围，建议降低标准或换方式"
            warning = ""
        else:
            risk_level = "dangerous"
            feasible = False
            suggestion = f"{item_name}（约{item_value:.0f}元）严重超出对方承受能力（上限仅{limit:.0f}元，占比{ratio*100:.0f}%），不建议创建此需求"
            warning = "【安全警告】绝不让对方借贷或超负荷满足此需求。系统不允许创建此需求任务。"

        result = {
            "feasible": feasible,
            "risk_level": risk_level,
            "ratio": round(ratio, 2) if ratio != float("inf") else 999.0,
            "suggestion": suggestion,
            "warning": warning,
            "item_name": item_name,
            "item_value": item_value,
            "amount_limit": limit,
        }
        logger.info(
            "需求可行性检查: person=%s, item=%s, value=%.0f, risk=%s, feasible=%s",
            person_id, item_name, item_value, risk_level, feasible,
        )
        return result

    # ------------------------------------------------------------------ #
    # 支出记录
    # ------------------------------------------------------------------ #
    def record_expense(
        self,
        person_id: str,
        amount: float,
        description: str,
        direction: str = "sent",
    ) -> None:
        """
        记录一笔付出/消费（用于追踪实际经济往来）。

        Args:
            person_id: 对方人物 ID。
            amount: 金额（元）。
            description: 消费描述。
            direction: 方向，"sent"（我方付出）或 "received"（对方付出）。
        """
        self._ensure_tables()
        try:
            self.db.execute(
                """
                INSERT INTO expense_records
                    (person_id, amount, description, direction, created_at)
                VALUES
                    (:pid, :amount, :desc, :direction, datetime('now'))
                """,
                {
                    "pid": person_id,
                    "amount": amount,
                    "desc": description,
                    "direction": direction,
                },
            )
            logger.debug("记录支出: person=%s, amount=%.2f, desc=%s", person_id, amount, description)
        except Exception as e:
            logger.error("记录支出失败: %s", e)

    def get_expense_summary(self, person_id: str, days: int = 30) -> dict:
        """
        获取最近 days 天的支出汇总。

        Args:
            person_id: 对方人物 ID。
            days: 统计天数，默认30。

        Returns:
            dict: total_amount / count / avg_amount / by_direction / latest。
        """
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        try:
            rows = self.db.fetch_all(
                """
                SELECT amount, description, direction, created_at
                FROM expense_records
                WHERE person_id = :pid AND created_at >= :since
                ORDER BY created_at DESC
                """,
                {"pid": person_id, "since": since},
            )
        except Exception as e:
            logger.warning("查询支出记录失败（表可能不存在）: %s", e)
            rows = []

        if not rows:
            return {
                "total_amount": 0.0,
                "count": 0,
                "avg_amount": 0.0,
                "by_direction": {},
                "latest": None,
            }

        total = sum(float(r.get("amount") or 0) for r in rows)
        count = len(rows)
        avg = total / count if count > 0 else 0.0

        # 按方向分组
        by_dir: dict[str, float] = {}
        for r in rows:
            d = r.get("direction", "sent")
            by_dir[d] = by_dir.get(d, 0.0) + float(r.get("amount") or 0)

        return {
            "total_amount": round(total, 2),
            "count": count,
            "avg_amount": round(avg, 2),
            "by_direction": by_dir,
            "latest": rows[0] if rows else None,
        }

    # ------------------------------------------------------------------ #
    # 读取最新报告
    # ------------------------------------------------------------------ #
    def get_latest_report(self, person_id: str) -> Optional[dict]:
        """
        获取指定人物的最新财务报告。

        Args:
            person_id: 对方人物 ID。

        Returns:
            dict: 最新报告；无记录时返回 None。
        """
        try:
            row = self.db.fetch_one(
                """
                SELECT * FROM financial_reports
                WHERE person_id = :pid
                ORDER BY created_at DESC LIMIT 1
                """,
                {"pid": person_id},
            )
        except Exception as e:
            logger.warning("查询最新财务报告失败: %s", e)
            return None

        if row is None:
            return None

        # 尝试解析 report_json 还原完整结构
        report: dict = {
            "income_estimate": row.get("income_estimate", ""),
            "disposable_income": row.get("disposable_income", ""),
            "consumption_level": row.get("consumption_level", "mid"),
            "large_purchase_capacity": row.get("large_purchase_capacity", ""),
            "stress_index": row.get("stress_index", 50),
            "suggested_amount_limit": row.get("suggested_amount_limit", 0),
            "summary": "",
            "created_at": row.get("created_at", ""),
        }
        # 从 report_json 还原 summary 等字段
        raw_json = row.get("report_json")
        if raw_json:
            try:
                parsed = json.loads(raw_json)
                if isinstance(parsed, dict):
                    report.update(parsed)
            except (json.JSONDecodeError, TypeError):
                pass
        # 还原 raw_signals
        raw_sigs = row.get("raw_signals")
        if raw_sigs:
            try:
                report["raw_signals"] = json.loads(raw_sigs)
            except (json.JSONDecodeError, TypeError):
                report["raw_signals"] = []
        else:
            report["raw_signals"] = []

        return report

    # ------------------------------------------------------------------ #
    # LLM 综合分析
    # ------------------------------------------------------------------ #
    def _llm_analyze(self, occupation: str, signals: list) -> Optional[dict]:
        """
        调用 LLM 进行综合财务分析，输出结构化 JSON。

        Args:
            occupation: 对方职业描述。
            signals: 提取到的财务信号列表。

        Returns:
            dict: 解析后的财务报告；失败返回 None。
        """
        prompt = (
            "根据以下信息，分析对方的财务状况，输出严格JSON：\n\n"
            f"【职业信息】{occupation}\n"
            "【聊天中提取的财务信号】\n"
            f"{json.dumps(signals, ensure_ascii=False, indent=2)}\n\n"
            "输出格式：\n"
            '{"income_estimate":"月收入区间描述",\n'
            ' "disposable_income":"可支配收入描述",\n'
            ' "consumption_level":"low/mid/mid_high/high",\n'
            ' "large_purchase_capacity":"大额支出能力描述",\n'
            ' "stress_index":0到100的整数,\n'
            ' "suggested_amount_limit":建议金额上限(数字,单位元),\n'
            ' "summary":"一句话总结"}\n\n'
            "要求：基于证据推断，不瞎编。信息不足时标注区间较宽。只输出JSON，不要其他文字。"
        )

        raw = self.llm.chat(
            system_prompt="你是严谨的私人财务分析师，擅长从聊天碎片推断对方真实经济状况。",
            user_prompt=prompt,
            complexity="complex",
            temperature=0.3,
            max_tokens=800,
        )
        if not raw:
            return None

        # 清洗：去除可能的 markdown 代码块包裹
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        report = json.loads(cleaned)
        if not isinstance(report, dict):
            return None
        return report

    # ------------------------------------------------------------------ #
    # 规则兜底估算（LLM不可用时）
    # ------------------------------------------------------------------ #
    def _estimate_from_signals(self, signals: list) -> dict:
        """
        基于信号类型与数量做粗略财务估算（LLM 不可用时的兜底）。

        估算规则：
            - 有收入信号且含数字 -> 提取数字估算月收入
            - 有房贷/车贷 -> stress_index += 20
            - 有"没钱/穷/吃土" -> stress_index += 30, consumption_level = low
            - 有苹果/星巴克等品牌 -> consumption_level = mid_high
            - 有攒钱/存钱 -> suggested_amount_limit 较低

        Args:
            signals: 财务信号列表。

        Returns:
            dict: 基础估算报告。
        """
        stress = 30  # 基础压力分
        consumption = "mid"
        income_low = 3000
        income_high = 8000
        has_income_number = False

        income_signals = [s for s in signals if s.get("type") == "income"]
        stress_signals = [s for s in signals if s.get("type") == "stress"]
        brand_signals = [s for s in signals if s.get("type") == "brand"]
        saving_signals = [s for s in signals if s.get("type") == "saving"]
        consumption_signals = [s for s in signals if s.get("type") == "consumption"]

        # 收入信号：尝试提取数字
        for s in income_signals:
            text = s.get("text", "")
            m = re.search(r"(\d+)\s*[千百]?", text)
            if m:
                num = int(m.group(1))
                # 处理 "5千" / "1万" 等
                if "万" in text:
                    num *= 10000
                elif "千" in text:
                    num *= 1000
                if num >= 1000:
                    income_low = num - 1000
                    income_high = num + 2000
                    has_income_number = True

        # 压力信号
        for s in stress_signals:
            text = s.get("text", "")
            if re.search(r"房贷|车贷|房租", text):
                stress += 20
            if re.search(r"欠款|借钱|贷款|还债|网贷", text):
                stress += 15
            if re.search(r"没钱|穷|吃土|手头紧|月光", text):
                stress += 30
                consumption = "low"

        # 品牌信号 -> 消费水平偏中高
        if brand_signals:
            consumption = "mid_high"

        # 消费信号数量多 -> 消费水平上调
        if len(consumption_signals) >= 3 and consumption == "mid":
            consumption = "mid_high"

        # 储蓄信号 -> 支出能力保守
        if saving_signals:
            stress += 5

        # 压力分 clamp 到 0-100
        stress = max(0, min(100, stress))

        # 建议金额上限：粗略按可支配月收入的 1~2 倍估算
        disposable_mid = (income_low + income_high) / 2
        # 压力越高，上限越低
        stress_factor = 1.0 - (stress / 100.0) * 0.6  # 0.4 ~ 1.0
        suggested_limit = disposable_mid * 1.5 * stress_factor
        suggested_limit = max(500, suggested_limit)  # 保底500元

        # 大额支出能力描述
        if consumption == "low":
            large_cap = "大额支出能力弱，建议避免500元以上消费"
        elif consumption == "mid_high":
            large_cap = "具备一定大额支出能力，千元级消费可承受"
        else:
            large_cap = "可承受数百元级消费，千元以上需斟酌"

        summary = f"信号{len(signals)}条，消费水平{consumption}，压力指数{stress}/100"
        if not signals:
            summary = "聊天数据不足，财务状况为保守估算"

        return {
            "income_estimate": f"约{income_low}-{income_high}元/月" if has_income_number else "数据不足，保守估计5k-10k",
            "disposable_income": f"约{int(disposable_mid * 0.6)}-{int(disposable_mid * 0.8)}元/月",
            "consumption_level": consumption,
            "large_purchase_capacity": large_cap,
            "stress_index": stress,
            "suggested_amount_limit": round(suggested_limit, 0),
            "summary": summary,
        }

    # ------------------------------------------------------------------ #
    # 内部工具：查询
    # ------------------------------------------------------------------ #
    def _fetch_profile(self, person_id: str) -> Optional[dict]:
        """从 persons 表获取人物档案（职业等）。"""
        try:
            return self.db.fetch_one(
                "SELECT id, nickname, occupation, gender FROM persons WHERE id = :pid",
                {"pid": person_id},
            )
        except Exception as e:
            logger.warning("查询人物档案失败: %s", e)
            return None

    # 金额关键词（SQL LIKE 预过滤用，先在库侧筛出候选消息，再回 Python 正则解析）
    _AMOUNT_KEYWORDS = (
        "元", "块", "钱", "工资", "转账", "红包", "礼物", "吃饭",
        "AA", "花了", "花费", "开销", "贷款", "房贷", "房租",
        "收入", "月薪", "年薪",
    )

    def _fetch_financial_candidates(self, person_id: str, limit: int = 50000) -> list:
        """
        全量扫描财务候选消息（替代旧的"最近200条"窗口）。

        做法：先用 SQL LIKE 在数据库侧预过滤含金额关键词的消息，
        再回到 Python 逐条正则解析，减少计算量。
        LIKE 的通配值通过 :p0..:pN 命名参数传入（如 '%元%'），
        关键词列表为代码内常量、不拼接用户输入，避免注入。

        兼容两种表名：
            - chat_messages（direction / created_at）
            - messages（sender / timestamp）
        优先使用 chat_messages；若表不存在则回退 messages。
        """
        like_params = {f"p{i}": f"%{kw}%" for i, kw in enumerate(self._AMOUNT_KEYWORDS)}
        or_clause = " OR ".join(
            f"content LIKE :p{i}" for i in range(len(self._AMOUNT_KEYWORDS))
        )
        params = {"pid": person_id, "lim": limit, **like_params}

        # 方案1：chat_messages（项目实际表）
        try:
            rows = self.db.fetch_all(
                f"""
                SELECT content, direction, created_at
                FROM chat_messages
                WHERE person_id = :pid AND ({or_clause})
                ORDER BY created_at DESC
                LIMIT :lim
                """,
                params,
            )
            if rows:
                return rows
            # 表存在但无命中也直接返回（避免再去查 messages）
            return rows
        except Exception:
            pass

        # 方案2：messages（技术指导书示例表名）
        try:
            rows = self.db.fetch_all(
                f"""
                SELECT content, sender, timestamp
                FROM messages
                WHERE person_id = :pid AND ({or_clause})
                ORDER BY timestamp DESC
                LIMIT :lim
                """,
                params,
            )
            return rows or []
        except Exception as e:
            logger.warning("聊天记录表查询失败（chat_messages 与 messages 均不可用）: %s", e)
            return []

    # ------------------------------------------------------------------ #
    # 内部工具：保存报告
    # ------------------------------------------------------------------ #
    def _save_report(self, person_id: str, report: dict, signals: list) -> None:
        """
        将财务报告写入 financial_reports 表（INSERT OR REPLACE 语义）。

        注：表上 person_id 无 UNIQUE 约束，每次生成即为新快照；
        通过 ORDER BY created_at DESC 取最新，符合需求。
        """
        try:
            self.db.execute(
                """
                INSERT OR REPLACE INTO financial_reports
                    (person_id, income_estimate, disposable_income, consumption_level,
                     large_purchase_capacity, stress_index, suggested_amount_limit,
                     raw_signals, report_json, created_at)
                VALUES
                    (:pid, :income, :disposable, :clevel, :large_cap,
                     :stress, :limit, :raw_sigs, :report_json, datetime('now'))
                """,
                {
                    "pid": person_id,
                    "income": report.get("income_estimate", ""),
                    "disposable": report.get("disposable_income", ""),
                    "clevel": report.get("consumption_level", "mid"),
                    "large_cap": report.get("large_purchase_capacity", ""),
                    "stress": int(report.get("stress_index", 50)),
                    "limit": float(report.get("suggested_amount_limit", 0)),
                    "raw_sigs": json.dumps(signals, ensure_ascii=False),
                    "report_json": json.dumps(report, ensure_ascii=False),
                },
            )
        except Exception as e:
            logger.error("保存财务报告失败: %s", e)
