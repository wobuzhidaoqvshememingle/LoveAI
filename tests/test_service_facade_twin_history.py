# -*- coding: utf-8 -*-
"""D5 回归：facade 三个数字分身入口必须把真实历史交给 twin（2026-10-17）。

背景（对抗验证实测）：``app/service_facade.py`` 的 ``predict_reaction`` /
``simulate_reply`` / ``compare_options`` 原先一律传 ``recent_chats=[]``，而同一个
``twin.predict_reaction`` 在 ``_run_analysis`` / ``_run_background_services`` /
``background_scheduler`` 都传真实历史（实测 10 条）——同一能力两条入口行为不一致，
数字分身在这三个入口拿不到任何历史证据（``digital_twin._build_system_prompt`` 的
【最近聊天记录】退化成「（暂无）」，confidence 上限被压到 0.5/0.3）。

本测试用打桩 twin 捕获入参（不联网、不起 Qt UI、DB 落临时目录）：
  1) 有历史时，三个入口传给 twin 的 ``recent_chats`` 非空，且等于末 10 条真实消息；
  2) 无历史 / 无 DB 时，行为与旧版一致：返回 ok、不抛异常、``recent_chats == []``。
"""
import os
import shutil
import sys
import tempfile

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from models.database import Database  # noqa: E402
from app.service_facade import ServiceFacade  # noqa: E402

PID = "p_twin_hist"
WINDOW = 10


class RecordingTwin:
    """打桩数字分身：只记录 recent_chats 入参，不联网、不读配置。"""

    def __init__(self):
        self.calls = []

    def predict_reaction(self, user_action, recent_chats=None, affection=None, **kw):
        self.calls.append(("predict_reaction", recent_chats))
        return {"prediction_id": "p-1", "likely_reaction": "stub", "confidence": 0.9}

    def simulate_reply(self, context, recent_chats=None, affection=None, **kw):
        self.calls.append(("simulate_reply", recent_chats))
        return "打桩回复（不调用真模型）"

    def compare_options(self, option_a, option_b, recent_chats=None, affection=None, **kw):
        self.calls.append(("compare_options", recent_chats))
        return {"option_a": {"affection_delta": 1}, "option_b": {"affection_delta": -1}}


def _make_facade(db, twin):
    """最小 facade：绕过需要 Qt/全套服务的 __init__，只挂 services 与打桩 twin。"""
    facade = ServiceFacade.__new__(ServiceFacade)
    facade.services = {"db": db} if db is not None else {}
    facade._get_twin = lambda person_id=None: twin
    return facade


@pytest.fixture
def tmp_db():
    tmpdir = tempfile.mkdtemp(prefix="loveai_test_twin_hist_")
    db = Database(os.path.join(tmpdir, "twin_hist.db"))
    db.init_tables()
    db.execute(
        "INSERT INTO persons (id, nickname, is_active) VALUES (:id, :nick, 1)",
        {"id": PID, "nick": PID},
    )
    yield db
    try:
        db.engine.dispose()
    except Exception:
        pass
    shutil.rmtree(tmpdir, ignore_errors=True)


def _seed(db, n):
    """写入 n 条真实消息（created_at 递增，保证取窗顺序确定）。"""
    for i in range(n):
        db.execute(
            "INSERT INTO chat_messages (person_id, direction, content, created_at) "
            "VALUES (:pid, :dir, :ct, :ts)",
            {
                "pid": PID,
                "dir": "sent" if i % 2 == 0 else "received",
                "ct": f"历史消息{i}",
                "ts": f"2026-10-17 05:{i:02d}:00",
            },
        )


def _expected_window(n):
    """末 10 条（按时间升序）的文本，与 get_chat_messages 的 DESC + reverse 口径一致。"""
    start = max(0, n - WINDOW)
    return [f"历史消息{i}" for i in range(start, n)]


def test_predict_reaction_passes_real_history(tmp_db):
    _seed(tmp_db, 15)
    twin = RecordingTwin()
    facade = _make_facade(tmp_db, twin)

    r = facade.predict_reaction("约她周五一起吃饭", PID)

    assert r.get("ok") is True, r
    name, recent_chats = twin.calls[-1]
    print(f"\n[D5] predict_reaction -> recent_chats 条数 = {len(recent_chats)}")
    print(f"[D5] 首条 = {recent_chats[0]!r}")
    print(f"[D5] 末条 = {recent_chats[-1]!r}")
    assert name == "predict_reaction"
    assert len(recent_chats) == WINDOW, f"应传末 {WINDOW} 条真实历史，实际 {len(recent_chats)} 条"
    assert [c["content"] for c in recent_chats] == _expected_window(15)
    assert {c["direction"] for c in recent_chats} == {"sent", "received"}
    assert all((c["content"] or "").strip() for c in recent_chats)


def test_simulate_reply_passes_real_history(tmp_db):
    _seed(tmp_db, 3)
    twin = RecordingTwin()
    facade = _make_facade(tmp_db, twin)

    r = facade.simulate_reply("对方: 你今天怎么都不找我", PID)

    assert r.get("ok") is True, r
    name, recent_chats = twin.calls[-1]
    print(f"\n[D5] simulate_reply -> recent_chats 条数 = {len(recent_chats)}")
    print(f"[D5] 内容 = {[c['content'] for c in recent_chats]!r}")
    assert name == "simulate_reply"
    assert len(recent_chats) == 3
    assert [c["content"] for c in recent_chats] == _expected_window(3)


def test_compare_options_passes_real_history(tmp_db):
    _seed(tmp_db, 12)
    twin = RecordingTwin()
    facade = _make_facade(tmp_db, twin)

    r = facade.compare_options("今晚陪她吃饭", "先加班改天再约", PID)

    assert r.get("ok") is True, r
    name, recent_chats = twin.calls[-1]
    print(f"\n[D5] compare_options -> recent_chats 条数 = {len(recent_chats)}")
    assert name == "compare_options"
    assert len(recent_chats) == WINDOW
    assert [c["content"] for c in recent_chats] == _expected_window(12)


def test_no_history_behaves_as_before(tmp_db):
    """该人物无任何聊天记录：三个入口都不抛异常、返回 ok，且 recent_chats 为空。"""
    twin = RecordingTwin()
    facade = _make_facade(tmp_db, twin)

    assert facade.predict_reaction("x", PID).get("ok") is True
    assert facade.simulate_reply("x", PID).get("ok") is True
    assert facade.compare_options("a", "b", PID).get("ok") is True

    lens = {name: (len(chats) if chats is not None else None) for name, chats in twin.calls}
    print(f"\n[D5] 无历史 -> recent_chats 长度 = {lens}")
    assert [c[1] for c in twin.calls] == [[], [], []]


def test_no_db_behaves_as_before():
    """services 里没有 db（旧版同路径）：不抛异常、返回 ok、recent_chats 为空。"""
    twin = RecordingTwin()
    facade = _make_facade(None, twin)

    r = facade.predict_reaction("x", PID)

    assert r.get("ok") is True, r
    print(f"\n[D5] 无 DB -> recent_chats = {twin.calls[-1][1]!r}")
    assert twin.calls[-1][1] == []
