"""
LoveAI v2.0 - 全局信号定义
后台线程通过这些信号通知UI更新，所有窗口共享同一个信号实例。
"""
from PySide6.QtCore import QObject, Signal


class AppSignals(QObject):
    """全局信号总线，所有后台线程→UI的通信走这里"""

    # 微信相关
    new_message = Signal(dict)           # 新微信消息: {person_id, content, direction, timestamp}
    wechat_status = Signal(dict)         # 微信连接状态: {status: connected/disconnected/error, info}
    person_switched = Signal(dict)       # 自动切换对象: {wechat_nickname, person_id}

    # 分析相关
    analysis_ready = Signal(dict)        # 分析结果就绪: {module, data}
    analysis_progress = Signal(dict)     # 分析进度: {percent, stage}

    # 好感度
    affection_changed = Signal(dict)     # 好感度变化: {person_id, scores, total, deltas}
    # R3.3-78 忠诚度（反制线主指标）独立变化信号，与好感分开发出
    loyalty_changed = Signal(dict)       # 忠诚度变化: {person_id, loyalty, loyalty_delta}

    # 任务
    task_updated = Signal(dict)          # 任务变化: {task, event: new/completed/failed/alternative}
    task_reminder = Signal(dict)         # 任务提醒

    # 勋章
    medal_unlocked = Signal(dict)        # 勋章解锁: {medal_id, name, icon}

    # 窗口控制
    open_window = Signal(str)            # 请求打开窗口: main/chat_overlay/consult_mini/float_ball
    hide_all_windows = Signal()          # 老板键：隐藏所有窗口
    show_all_windows = Signal()          # 恢复所有窗口

    # 系统
    config_changed = Signal(str)         # 配置变更: key
    error_occurred = Signal(dict)        # 错误: {source, message, severity}
    app_quitting = Signal()              # 应用退出中


_signals_instance = None


def get_signals() -> AppSignals:
    """获取全局信号单例"""
    global _signals_instance
    if _signals_instance is None:
        _signals_instance = AppSignals()
    return _signals_instance
