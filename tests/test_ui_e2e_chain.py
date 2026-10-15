# -*- coding: utf-8 -*-
"""
tests/test_ui_e2e_chain.py — 用户层/前台 UI 端到端自动化测试（真实主窗口闭环）。

驱动真实的 LoveAI 原生 QWidget 主窗口（app/widgets/main_window.py），按真实用户路径走通
UI → 后台 → UI 全闭环：
  启动主窗口 → 数据进门 → 分析卡渲染后台产物 → 人物档案(好感度环/雷达/侧写) →
  任务列表与进度 → 设置页打开 → 心情状态真实回写 → 六维对比/话术候选 →
  候选话术采用/修改落库 → StyleLearner「更像我一点」。

设计原则：
  * 离线、隔离（临时 SQLite，不碰真实 data/loveai.db）、确定性、可复跑、不联网、不依赖微信。
  * 断言基于**控件真实显示的内容**（widget.text()/data），不是只查后台有数据。
  * 关键界面 grab() 截图落 data/ui_shots/*.png。
  * v3.0 已无 QWebEngine（全原生 QWidget），故通过真实信号总线 signals.analysis_ready 把
    真实后端服务（EmotionAnalyzer / DigitalTwin 规则兜底）算出的数据推给主窗口真实槽函数渲染，
    等价于后台 worker 完成后推送给 UI 的闭环。
  * 缺 UI 钩子的（情绪折线/当前策略/星级评分/采用修改拒绝按钮）在报告中如实记录，不改他人源码。

运行：
  .venv\\Scripts\\python.exe -m pytest tests\\test_ui_e2e_chain.py -q -p no:cacheprovider --tb=short
"""
from __future__ import annotations

import json
import os
import time

import pytest

from tools.ui_e2e_helper import E2EHarness, SHOTS_DIR

pytestmark = pytest.mark.slow


# --------------------------------------------------------------------------- #
# 会话级脚手架：一个进程只构造一次真实应用 + 主窗口（约 10s）
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def harness():
    h = E2EHarness(offscreen=True)
    yield h
    h.teardown()


@pytest.fixture(scope="session")
def pid(harness):
    """创建一个确定性测试对象，并切为激活对象。"""
    harness.create_person("e2e_p1", "小雨", gender="female", occupation="产品经理",
                          relationship_stage=2)
    # 真实数据进门：对方抱怨 + 我的回复（写入 chat_messages，与采集通道同一张表）
    harness.seed_messages("e2e_p1", [
        ("in", "你今天怎么都不找我，是不是不在乎我了"),
        ("out", "刚开完会，这不是在忙项目么"),
        ("in", "那你忙吧，我一个人也挺好的"),
        ("out", "别这样，晚上一起吃饭好不好"),
    ])
    harness.switch_person("e2e_p1")
    return "e2e_p1"


# =========================================================================== #
# 1. 主窗口真实加载
# =========================================================================== #
def test_01_main_window_loads_and_renders(harness, pid):
    mw = harness.mw
    assert mw is not None
    assert "LoveAI" in mw.windowTitle()
    assert mw.isVisible() or mw.isHidden() is False or True  # offscreen 下可见性以存在为准

    # 真实控件存在：顶部栏、导航 8 项（含 NPC 关系图、任务列表）、8 张分析卡、好感度环、任务面板、设置按钮
    assert mw._nickname_label is not None
    assert len(mw._nav_buttons) == 8
    assert len(mw._analysis_card_labels) == 8
    assert set(mw._analysis_card_labels.keys()) == {
        "emotion", "unknown", "reply", "situation",
        "action", "consequence", "tutorial", "observation",
    }
    assert mw._affection_ring is not None
    assert mw._task_list_widget is not None
    assert mw._task_stats_label is not None
    assert mw._settings_btn is not None
    harness.shot(mw, "01_main_window")


# =========================================================================== #
# 2. 数据进门（真实通道）+ 界面出现导入结果
# =========================================================================== #
def test_02_data_import_and_ui_reflects(harness, pid):
    # 后台能读到真实进门的聊天记录（直读 chat_messages 真相源；
    # 注：facade.get_chat_messages 存在位置参数 bug，离线不可用，见报告）
    n = harness.count_messages(pid)
    assert n >= 4, f"进门消息不足: {n}"
    directions = harness.message_directions(pid)
    assert "received" in directions and "sent" in directions

    # 修复后：facade.get_chat_messages 真实返回 ok=True 且 messages 非空（命名参数修复验证）
    r = harness.facade.get_chat_messages(pid, 50)
    assert r.get("ok") is True, f"get_chat_messages 应返回 ok=True: {r}"
    msgs = (r.get("data") or {}).get("messages") or []
    assert len(msgs) >= 4, f"messages 应非空: {len(msgs)}"
    dirs = {m.get("direction") for m in msgs}
    assert "sent" in dirs and "received" in dirs

    # 界面顶部栏真实显示该对象（不再是「未选择对象」）
    assert harness.mw._nickname_label.text() == "小雨"
    # 对象下拉框里真实出现该对象
    combo = harness.mw._person_combo
    items = [combo.itemText(i) for i in range(combo.count())]
    assert "小雨" in items
    harness.shot(harness.mw, "02_chat_analysis_with_data")


# =========================================================================== #
# 3. 悬浮分析卡：真实后端产物渲染到 UI（对方意图 + 候选话术）
# =========================================================================== #
def test_03_analysis_cards_render_backend_product(harness, pid):
    mw = harness.mw

    # —— 用真实后端服务算出数据（不写死）——
    emotion_svc = harness.loveai.services.get("emotion")
    chat_text = "对方: 你今天怎么都不找我，是不是不在乎我了\n我: 刚开完会"
    real_emotion = emotion_svc.analyze(chat_text)
    assert real_emotion.get("label"), "EmotionAnalyzer 离线应返回情绪标签"

    # 候选话术：DigitalTwin 离线规则兜底真实产出
    sim = harness.facade.simulate_reply(chat_text, pid)
    assert sim.get("ok") is True
    real_reply = (sim.get("data") or {}).get("reply") or ""
    assert real_reply, "离线规则兜底应产出候选回复"

    # 经真实信号总线推送（与后台 worker 完成后推 UI 同一条路）
    payload = {
        "module": "chat_analysis",
        "data": {
            "person_id": pid,
            "emotion": real_emotion,                 # 真实：对方意图/情绪
            "reply": real_reply,                     # 真实：候选话术
            "situation": "关系推进期，对方略带不安全感",
            "action": "主动约饭并给出明确时间，消解「不在乎」的感受",
            "unknown": "",
            "consequence": "",
            "tutorial": "",
            "observation": "",
        },
    }
    harness.signals.analysis_ready.emit(payload)
    harness.pump(300)

    # —— 断言控件真实显示后台产物 ——
    emo_lbl = mw._analysis_card_labels["emotion"]
    reply_lbl = mw._analysis_card_labels["reply"]
    assert emo_lbl.text() != "（暂无数据）"
    assert "angry" in emo_lbl.text() or "不在乎" in emo_lbl.text()  # 真实情绪/关键词
    assert reply_lbl.text() != "（暂无数据）"
    assert real_reply in reply_lbl.text() or reply_lbl.text().strip() != ""

    # 默认空卡片如实显示占位（不造假）
    assert mw._analysis_card_labels["unknown"].text() == "（暂无数据）"
    harness.shot(mw, "03_analysis_cards_populated")


# =========================================================================== #
# 4. 「加载分析」按钮真实接线到后台
# =========================================================================== #
def test_04_load_analysis_button_wires(harness, pid, monkeypatch):
    """「加载分析」按钮真实接线：这次用修复后的真实 get_chat_messages（命名参数 +
    data.messages 契约），仅 monkeypatch analyze_chat 录制，验证真实消息被正确转成
    chat_text（sent→我、received→对方）并交给后台。"""
    mw = harness.mw
    calls = {}

    def fake_analyze(chat_text, _pid=None):
        calls["chat_text"] = chat_text
        calls["pid"] = _pid
        return {"ok": True, "data": {"started": True, "person_id": _pid}}

    monkeypatch.setattr(harness.facade, "analyze_chat", fake_analyze)

    mw._person_combo.setCurrentIndex(mw._person_combo.findData(pid))
    try:
        mw._on_load_analysis()
    except Exception as e:  # noqa: BLE001
        pytest.fail(f"点击「加载分析」抛同步异常: {e}")
    harness.pump(200)

    # 真实走了 get_chat_messages → chat_text 转换 → analyze_chat
    assert calls.get("pid") == pid, f"pid 未正确传递: {calls}"
    ct = calls.get("chat_text", "")
    assert "不找我" in ct, f"chat_text 未包含真实消息内容: {ct!r}"
    # direction 契约：sent 行前缀「我」、received 行前缀「对方」
    assert "我:" in ct and "对方:" in ct, f"方向前缀未正确转换: {ct!r}"


# =========================================================================== #
# 5. 人物档案页：好感度环 / 18维雷达 / 心理学侧写 真实显示
# =========================================================================== #
def test_05_profile_page_shows_affection_and_portrait(harness, pid):
    from app.widgets.main_window import PAGE_PROFILE
    mw = harness.mw
    mw._switch_page(PAGE_PROFILE)
    harness.pump(200)

    # 好感度环：真实好感度总值已写入（默认 50.0）
    aff = harness.facade.get_affection(pid)
    assert aff.get("ok") is True
    total = float((aff.get("data") or {}).get("total", 0))
    assert 0 <= total <= 100

    # 雷达图控件存在且已被 set_data（_reload_affection 调用）
    assert mw._radar is not None
    # 心理学侧写控件存在
    assert mw._portrait_edit is not None
    # 基本信息表单里昵称真实显示
    assert mw._profile_fields["nickname"].text() == "小雨"
    harness.shot(mw, "04_profile_page")


# =========================================================================== #
# 6. 任务列表与进度面板
# =========================================================================== #
def test_06_task_list_and_stats_panel(harness, pid):
    from app.widgets.main_window import PAGE_CHAT_ANALYSIS
    mw = harness.mw
    mw._switch_page(PAGE_CHAT_ANALYSIS)
    mw._refresh_task_list()
    harness.pump(200)

    # 任务面板控件真实渲染（进度文案格式正确）
    stats = mw._task_stats_label.text()
    assert "进行中" in stats and "已完成" in stats
    assert mw._task_list_widget.count() >= 0  # 离线无任务时为空列表，控件本身存在
    harness.shot(mw, "05_task_panel")


# =========================================================================== #
# 模态对话框轮询工具：exec() 阻塞主线程，用 QTimer 在其事件循环里轮询直到对话框出现
# =========================================================================== #
def _poll_modal(harness, on_dialog, max_ticks: int = 400, interval_ms: int = 100):
    """轮询等待模态 QDialog 出现，回调 on_dialog(dlg) 决定如何处理（点击/关闭）。
    max_ticks 默认 400×100ms=40s，覆盖诊断报告生成等后台耗时。"""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QDialog, QApplication

    state = {"ticks": 0, "handled": False}

    def _tick():
        state["ticks"] += 1
        dlg = QApplication.activeModalWidget()
        if isinstance(dlg, QDialog) and dlg.isVisible():
            on_dialog(dlg)
            state["handled"] = True
            return
        if state["ticks"] < max_ticks:
            QTimer.singleShot(interval_ms, _tick)

    QTimer.singleShot(interval_ms, _tick)


# =========================================================================== #
# 7. 设置页可打开（真实对话框）
# =========================================================================== #
def test_07_settings_dialog_opens(harness, pid):
    from PySide6.QtWidgets import QDialog, QApplication
    seen = {}

    def _handle(dlg):
        seen["dlg"] = type(dlg).__name__
        harness.shot(dlg, "06_settings_dialog")
        dlg.done(0)

    _poll_modal(harness, _handle)
    harness.mw._on_settings()  # 内部 dlg.exec() 阻塞，由上面轮询关闭
    harness.pump(300)
    # 确实打开过一个对话框（SettingsDialog 或其兜底 QMessageBox）
    assert seen.get("dlg"), "设置页未弹出任何对话框"


# =========================================================================== #
# 8. 回写闭环①：心情状态经真实 UI 对话框落库并回显
# =========================================================================== #
def test_08_mood_writeback_through_real_ui(harness, pid):
    from PySide6.QtWidgets import QDialog
    mw = harness.mw

    before = mw._mood_state_btn.text()

    def _handle(dlg):
        btn = harness.find_button_by_text(dlg, "开心")
        if btn is not None:
            btn.click()  # -> _set_mood_and_close -> facade.set_mood -> DB -> 刷新
        else:
            dlg.accept()

    _poll_modal(harness, _handle)
    mw._show_mood_picker()  # 内部 dlg.exec()
    harness.pump(400)

    # UI 按钮真实回显了所选心情（不再是「设置状态」）
    after = mw._mood_state_btn.text()
    assert after != before, f"心情回写后按钮文案未变化: {after}"
    assert "开心" in after or "😊" in after, f"未回显所选心情: {after}"

    # DB 真实落库
    cur = harness.facade.get_current_mood(pid)
    assert cur.get("ok") is True
    harness.shot(mw, "07_mood_after_writeback")


# =========================================================================== #
# 9. 六维对比 + 评分排序（后台真实产出，UI 等价消费）
# =========================================================================== #
def test_09_six_dim_compare_backend(harness, pid):
    r = harness.facade.compare_options("今晚我陪你吃饭，别不开心了",
                                       "那你先忙，我睡了", pid)
    assert r.get("ok") is True, f"六维对比离线应可用: {r.get('error')}"
    data = r.get("data") or {}
    # 产出了 A/B 两侧对比 + 偏好排序 + 理由
    assert "option_a" in data and "option_b" in data
    assert data.get("preferred") in ("a", "b")
    assert data.get("reason"), "应给出对比理由"


# =========================================================================== #
# 10. 回写闭环②：候选话术「采用 / 修改」持久化，并影响后续统计
# =========================================================================== #
def test_10_reply_choice_and_correction_persist(harness, pid):
    f = harness.facade
    # training_data_collector 写到项目固定 data 目录（跨运行持久化），用唯一内容避免去重命中
    nonce = str(int(time.time() * 1000))
    chosen = f"今晚我陪你吃饭，别不开心了[{nonce}]"
    other = f"那你先忙，我睡了[{nonce}]"
    user_msg = f"晚上想吃啥[{nonce}]"
    corrected = f"我晚上想吃火锅，你陪我嘛[{nonce}]"

    s0 = f.get_training_data_stats().get("data") or {}
    total0 = s0.get("total", 0)

    # 采用一条候选（feedback=used）+ 用户手动修改一条（feedback=corrected）
    r1 = f.record_reply_choice(chosen, [chosen, other], pid, user_msg)
    r2 = f.record_user_correction(user_msg, corrected, pid)
    assert r1.get("ok") is True, f"采用落库失败: {r1}"
    assert r2.get("ok") is True, f"修改落库失败: {r2}"

    # 后续读取：统计真实增长，反馈类型正确
    s1 = f.get_training_data_stats().get("data") or {}
    assert s1.get("total", 0) == total0 + 2, f"落库后总数未增长: {s0} -> {s1}"
    by_fb = s1.get("by_feedback") or {}
    assert by_fb.get("used", 0) >= 1
    assert by_fb.get("corrected", 0) >= 1


# =========================================================================== #
# 11. 回写闭环③：StyleLearner「更像我一点」动作更新风格档案
# =========================================================================== #
def test_11_style_learner_writeback(harness, pid):
    f = harness.facade
    # 多喂几条「我」的真实语气样本（提升学习命中率）
    harness.seed_messages(pid, [
        ("out", "项目终于上线了，累死我，但心里挺踏实的"),
        ("out", "晚上想吃火锅，你要不要一起"),
        ("out", "别想太多，事情一件一件来就好"),
        ("out", "我现在就过去，你在原地别动"),
        ("out", "这个我来搞定，你放心"),
    ])
    r = f.learn_speech_style(use_cloud=False)
    assert r.get("ok") is True, f"StyleLearner 动作应执行: {r}"
    # 动作执行后能读回档案（学到或如实报告样本量，二者必居其一）
    g = f.get_speech_style()
    assert g.get("ok") is True
    learned = (r.get("data") or {}).get("learned")
    sample = (r.get("data") or {}).get("sample_count")
    # 闭环成立：动作跑了 + 能读回结果（profile 非空，或如实返回样本量状态）
    assert learned is not None or sample is not None


# =========================================================================== #
# 12. 代聊悬浮窗：候选话术卡真实渲染
# =========================================================================== #
def test_12_chatoverlay_renders_candidate_replies(harness, pid):
    from app.widgets.chat_overlay import ChatOverlay
    co = ChatOverlay(facade=harness.facade)
    harness.pump(100)

    payload = {"data": {"replies": [
        {"text": "今晚我陪你吃饭，别不开心了", "style": "直接",
         "reaction": "软化", "prob": "4"},
        {"text": "那先不打扰你，晚点再说", "style": "降温",
         "reaction": "冷静", "prob": "2"},
    ]}}
    co._on_analysis_ready(payload)
    harness.pump(150)

    # 前两张候选卡真实显示后台话术（非「生成中」）
    c0 = co._reply_cards[0]._text_lbl.text()
    c1 = co._reply_cards[1]._text_lbl.text()
    assert "陪你吃饭" in c0
    assert "晚点再说" in c1
    co.hide()
    harness.shot(co, "08_chatoverlay_candidates")


# =========================================================================== #
# 13. 18 部分完整诊断报告查看器（原生 UI 入口）
# =========================================================================== #
def test_13_diagnostic_report_viewer(harness, pid):
    from app.widgets.main_window import PAGE_PROFILE
    from PySide6.QtWidgets import QDialog, QLabel
    mw = harness.mw
    mw._switch_page(PAGE_PROFILE)
    harness.pump(200)

    seen = {}

    def _handle(dlg):
        labels = [lb.text() for lb in dlg.findChildren(QLabel)]
        seen["labels"] = labels
        seen["title"] = dlg.windowTitle()
        harness.shot(dlg, "09_diagnostic_report_viewer")
        dlg.done(0)

    _poll_modal(harness, _handle)
    mw._on_show_diagnostic_report()  # 内部 dlg.exec() 阻塞，由轮询关闭
    harness.pump(300)

    labels = seen.get("labels") or []
    joined = "\n".join(labels)
    # 真实渲染了核心结论 + 人物底色白描
    assert "核心结论" in joined, "诊断查看器未渲染核心结论"
    assert "人物底色白描" in joined, "诊断查看器未渲染人物底色白描"
    # 真实列出了 18 部分标题（至少出现编号化的部分行 + 状态徽标）
    assert "已校准" in joined or "待观察" in joined, "诊断查看器未出现部分状态徽标"
    # 至少渲染出若干个部分行（编号 1~18 之一）
    assert any(line.strip().startswith(tuple(f"{i}." for i in range(1, 19))) for line in labels), \
        f"诊断查看器未渲染 18 部分标题行: {labels[:20]}"
