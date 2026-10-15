# -*- coding: utf-8 -*-
"""
animations.py - 7种骨骼动画定义与播放控制

实现方式说明：
    PySide6 的 Qt3DAnimation 骨骼动画 API（QClipAnimator + QAnimationClip）
    在 Python 侧文档稀少、信号槽绑定容易出问题，且对 QEntity/QTransform
    的直接驱动不够直观。本模块采用更可靠的方案：

        QTimer + 直接修改骨骼 Entity 的 QTransform

    即：动画数据用纯 Python dict 描述关键帧，由 BoneAnimator 类在
    QTimer.timerEvent / timeout 回调中按时间插值，逐帧 setTranslation /
    setRotation / setScale。这种方式可控、可调试、跨 PySide6 版本稳定。

动画关键帧数据结构（每条 animation）：
    {
        "duration": 总时长（毫秒）,
        "loop":     是否循环,
        "keyframes": [
            {
                "time":        时间点（毫秒）,
                "bone":        骨骼名,
                "type":        "rotate" | "translate" | "scale",
                "axis":        (x,y,z)   # rotate 时使用
                "angle":       角度      # rotate 时使用
                "position":    (x,y,z)   # translate 时使用
                "scale":       (x,y,z)   # scale 时使用
            },
            ...
        ]
    }
"""

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QVector3D, QQuaternion
from PySide6.Qt3DCore import Qt3DCore
QTransform = Qt3DCore.QTransform


# ---------------------------------------------------------------------------
# 7 种动画数据
# ---------------------------------------------------------------------------
ANIMATIONS = {
    # 1. 待机呼吸眨眼：spine 轻微上下浮动，head 轻微左右转动（用 scale 模拟眨眼）
    "idle": {
        "duration": 2000,
        "loop": True,
        "keyframes": [
            # 呼吸：spine 轻微上下浮动
            {"time": 0,    "bone": "spine", "type": "translate", "position": (0.0, 0.12, 0.0)},
            {"time": 500,  "bone": "spine", "type": "translate", "position": (0.0, 0.14, 0.0)},
            {"time": 1000, "bone": "spine", "type": "translate", "position": (0.0, 0.12, 0.0)},
            {"time": 1500, "bone": "spine", "type": "translate", "position": (0.0, 0.14, 0.0)},
            {"time": 2000, "bone": "spine", "type": "translate", "position": (0.0, 0.12, 0.0)},
            # 头部轻微左右转动
            {"time": 0,    "bone": "head", "type": "rotate", "axis": (0, 1, 0), "angle": 0},
            {"time": 500,  "bone": "head", "type": "rotate", "axis": (0, 1, 0), "angle": 5},
            {"time": 1000, "bone": "head", "type": "rotate", "axis": (0, 1, 0), "angle": -5},
            {"time": 1500, "bone": "head", "type": "rotate", "axis": (0, 1, 0), "angle": 3},
            {"time": 2000, "bone": "head", "type": "rotate", "axis": (0, 1, 0), "angle": 0},
        ],
    },

    # 2. 开心挥手跳：hand_r 上下挥动，hips 上下跳动，循环2次（共 ~2s）
    "happy": {
        "duration": 2000,
        "loop": True,
        "keyframes": [
            # hips 跳动
            {"time": 0,    "bone": "hips", "type": "translate", "position": (0.0, 0.9, 0.0)},
            {"time": 200,  "bone": "hips", "type": "translate", "position": (0.0, 1.05, 0.0)},
            {"time": 400,  "bone": "hips", "type": "translate", "position": (0.0, 0.9, 0.0)},
            {"time": 1000, "bone": "hips", "type": "translate", "position": (0.0, 1.05, 0.0)},
            {"time": 1200, "bone": "hips", "type": "translate", "position": (0.0, 0.9, 0.0)},
            # hand_r 挥动（绕 shoulder 前后摆）
            {"time": 0,    "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -15},
            {"time": 250,  "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -80},
            {"time": 500,  "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -15},
            {"time": 750,  "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -80},
            {"time": 1000, "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -15},
            {"time": 1250, "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -80},
            {"time": 1500, "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -15},
            {"time": 1750, "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -80},
            {"time": 2000, "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -15},
        ],
    },

    # 3. 疑惑摸头：hand_r 抬起摸 head，head 倾斜
    "confused": {
        "duration": 2400,
        "loop": True,
        "keyframes": [
            # 抬起右手到头上
            {"time": 0,    "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -15},
            {"time": 400,  "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -120},
            {"time": 400,  "bone": "lower_arm_r", "type": "rotate", "axis": (1, 0, 0), "angle": 40},
            # 摸头停顿
            {"time": 1600, "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -120},
            {"time": 1600, "bone": "lower_arm_r", "type": "rotate", "axis": (1, 0, 0), "angle": 40},
            # 放回
            {"time": 2000, "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -15},
            {"time": 2000, "bone": "lower_arm_r", "type": "rotate", "axis": (1, 0, 0), "angle": 0},
            # head 倾斜
            {"time": 400,  "bone": "head", "type": "rotate", "axis": (0, 0, 1), "angle": 15},
            {"time": 1600, "bone": "head", "type": "rotate", "axis": (0, 0, 1), "angle": 15},
            {"time": 2000, "bone": "head", "type": "rotate", "axis": (0, 0, 1), "angle": 0},
        ],
    },

    # 4. 生气握拳跺脚：hand_l/r scale 缩小（握拳），foot_l 上下踏地，身体前倾
    "angry": {
        "duration": 1600,
        "loop": True,
        "keyframes": [
            # 身体前倾
            {"time": 0,    "bone": "spine", "type": "rotate", "axis": (1, 0, 0), "angle": 0},
            {"time": 200,  "bone": "spine", "type": "rotate", "axis": (1, 0, 0), "angle": 15},
            {"time": 1400, "bone": "spine", "type": "rotate", "axis": (1, 0, 0), "angle": 15},
            {"time": 1600, "bone": "spine", "type": "rotate", "axis": (1, 0, 0), "angle": 0},
            # 握拳（scale 缩小手部）
            {"time": 200,  "bone": "hand_l", "type": "scale", "scale": (0.6, 0.6, 0.6)},
            {"time": 200,  "bone": "hand_r", "type": "scale", "scale": (0.6, 0.6, 0.6)},
            {"time": 1400, "bone": "hand_l", "type": "scale", "scale": (0.6, 0.6, 0.6)},
            {"time": 1400, "bone": "hand_r", "type": "scale", "scale": (0.6, 0.6, 0.6)},
            {"time": 1600, "bone": "hand_l", "type": "scale", "scale": (1.0, 1.0, 1.0)},
            {"time": 1600, "bone": "hand_r", "type": "scale", "scale": (1.0, 1.0, 1.0)},
            # 跺脚 2 次
            {"time": 200,  "bone": "foot_l", "type": "translate", "position": (0.0, -0.28, 0.04)},
            {"time": 400,  "bone": "foot_l", "type": "translate", "position": (0.0, -0.24, 0.04)},
            {"time": 600,  "bone": "foot_l", "type": "translate", "position": (0.0, -0.28, 0.04)},
            {"time": 800,  "bone": "foot_l", "type": "translate", "position": (0.0, -0.24, 0.04)},
            {"time": 1400, "bone": "foot_l", "type": "translate", "position": (0.0, -0.24, 0.04)},
        ],
    },

    # 5. 难过驼背：spine 弯曲前倾，head 低下，hand 下垂
    "sad": {
        "duration": 3000,
        "loop": True,
        "keyframes": [
            # spine 驼背前倾
            {"time": 0,    "bone": "spine", "type": "rotate", "axis": (1, 0, 0), "angle": 0},
            {"time": 500,  "bone": "spine", "type": "rotate", "axis": (1, 0, 0), "angle": 25},
            {"time": 2500, "bone": "spine", "type": "rotate", "axis": (1, 0, 0), "angle": 25},
            {"time": 3000, "bone": "spine", "type": "rotate", "axis": (1, 0, 0), "angle": 0},
            # head 低下
            {"time": 500,  "bone": "head", "type": "rotate", "axis": (1, 0, 0), "angle": 20},
            {"time": 2500, "bone": "head", "type": "rotate", "axis": (1, 0, 0), "angle": 20},
            {"time": 3000, "bone": "head", "type": "rotate", "axis": (1, 0, 0), "angle": 0},
            # 手臂下垂
            {"time": 500,  "bone": "upper_arm_l", "type": "rotate", "axis": (0, 0, 1), "angle": 5},
            {"time": 500,  "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -5},
            {"time": 2500, "bone": "upper_arm_l", "type": "rotate", "axis": (0, 0, 1), "angle": 5},
            {"time": 2500, "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -5},
            {"time": 3000, "bone": "upper_arm_l", "type": "rotate", "axis": (0, 0, 1), "angle": 15},
            {"time": 3000, "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -15},
        ],
    },

    # 6. 害羞捂脸：hand_l/r 抬起遮住 face，head 微低
    "shy": {
        "duration": 2500,
        "loop": True,
        "keyframes": [
            # 双手抬起捂脸
            {"time": 0,    "bone": "upper_arm_l", "type": "rotate", "axis": (0, 0, 1), "angle": 15},
            {"time": 0,    "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -15},
            {"time": 500,  "bone": "upper_arm_l", "type": "rotate", "axis": (0, 0, 1), "angle": 80},
            {"time": 500,  "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -80},
            {"time": 500,  "bone": "lower_arm_l", "type": "rotate", "axis": (1, 0, 0), "angle": -30},
            {"time": 500,  "bone": "lower_arm_r", "type": "rotate", "axis": (1, 0, 0), "angle": -30},
            # 捂脸停顿
            {"time": 2000, "bone": "upper_arm_l", "type": "rotate", "axis": (0, 0, 1), "angle": 80},
            {"time": 2000, "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -80},
            {"time": 2000, "bone": "lower_arm_l", "type": "rotate", "axis": (1, 0, 0), "angle": -30},
            {"time": 2000, "bone": "lower_arm_r", "type": "rotate", "axis": (1, 0, 0), "angle": -30},
            # 放回
            {"time": 2500, "bone": "upper_arm_l", "type": "rotate", "axis": (0, 0, 1), "angle": 15},
            {"time": 2500, "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -15},
            {"time": 2500, "bone": "lower_arm_l", "type": "rotate", "axis": (1, 0, 0), "angle": 0},
            {"time": 2500, "bone": "lower_arm_r", "type": "rotate", "axis": (1, 0, 0), "angle": 0},
            # head 微低
            {"time": 500,  "bone": "head", "type": "rotate", "axis": (1, 0, 0), "angle": 10},
            {"time": 2000, "bone": "head", "type": "rotate", "axis": (1, 0, 0), "angle": 10},
            {"time": 2500, "bone": "head", "type": "rotate", "axis": (1, 0, 0), "angle": 0},
        ],
    },

    # 7. 提醒跳跃：hips 上下跳跃 3 次，hand_r 挥动召唤
    "reminder": {
        "duration": 1800,
        "loop": True,
        "keyframes": [
            # 跳跃 3 次
            {"time": 0,    "bone": "hips", "type": "translate", "position": (0.0, 0.9, 0.0)},
            {"time": 150,  "bone": "hips", "type": "translate", "position": (0.0, 1.15, 0.0)},
            {"time": 300,  "bone": "hips", "type": "translate", "position": (0.0, 0.9, 0.0)},
            {"time": 450,  "bone": "hips", "type": "translate", "position": (0.0, 1.15, 0.0)},
            {"time": 600,  "bone": "hips", "type": "translate", "position": (0.0, 0.9, 0.0)},
            {"time": 750,  "bone": "hips", "type": "translate", "position": (0.0, 1.15, 0.0)},
            {"time": 900,  "bone": "hips", "type": "translate", "position": (0.0, 0.9, 0.0)},
            # hand_r 召唤挥动
            {"time": 0,    "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -15},
            {"time": 300,  "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -100},
            {"time": 600,  "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -100},
            {"time": 900,  "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -100},
            {"time": 1200, "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -100},
            {"time": 1500, "bone": "upper_arm_r", "type": "rotate", "axis": (0, 0, 1), "angle": -15},
        ],
    },
}


# ---------------------------------------------------------------------------
# BoneAnimator：基于 QTimer 的骨骼动画播放器
# ---------------------------------------------------------------------------
class BoneAnimator(QObject):
    """
    用 QTimer 驱动骨骼动画。

    用法：
        animator = BoneAnimator(bone_entities_dict, parent)
        animator.play("happy")
        animator.stop()   # 停止并复位到 idle
    """

    finished = Signal(str)   # 单次播放完成（非循环）时发出

    TICK_MS = 33             # ~30fps

    def __init__(self, bone_entities, parent=None):
        super().__init__(parent)
        self._bones = bone_entities                 # {name: QEntity}
        self._transforms = self._extract_transforms(bone_entities)
        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MS)
        self._timer.timeout.connect(self._on_tick)
        self._current_name = None
        self._current_data = None
        self._elapsed = 0
        self._loop = False

    # ---- 内部工具 ----
    def _extract_transforms(self, bone_entities):
        """缓存每根骨骼的 QTransform 引用。"""
        result = {}
        for name, ent in bone_entities.items():
            # 找到 entity 上的 QTransform 组件
            for comp in ent.components():
                if isinstance(comp, QTransform):
                    result[name] = comp
                    break
        return result

    def _get_transform(self, bone_name):
        return self._transforms.get(bone_name)

    # ---- 播放控制 ----
    def play(self, animation_name):
        """开始播放指定动画。"""
        if animation_name not in ANIMATIONS:
            return
        self._current_name = animation_name
        self._current_data = ANIMATIONS[animation_name]
        self._loop = self._current_data.get("loop", False)
        self._elapsed = 0
        self._timer.start()

    def stop(self):
        """停止当前动画并复位到 idle。"""
        self._timer.stop()
        self._current_name = None
        self._current_data = None
        self._elapsed = 0

    def is_playing(self):
        return self._timer.isActive()

    # ---- 每帧回调 ----
    def _on_tick(self):
        if self._current_data is None:
            return
        self._elapsed += self.TICK_MS
        duration = self._current_data["duration"]

        if self._elapsed >= duration:
            if self._loop:
                self._elapsed = self._elapsed % duration
            else:
                self._elapsed = duration
                self._apply_frame(self._elapsed)
                self._timer.stop()
                name = self._current_name
                self._current_name = None
                self._current_data = None
                self.finished.emit(name)
                return

        self._apply_frame(self._elapsed)

    def _apply_frame(self, t):
        """按当前时间 t，对每根受影响的骨骼插值并写入 Transform。"""
        keyframes = self._current_data["keyframes"]

        # 按骨骼分组，找每根骨骼在 t 时刻的插值
        bones_touched = {}
        for kf in keyframes:
            bones_touched.setdefault(kf["bone"], []).append(kf)

        for bone_name, frames in bones_touched.items():
            frames.sort(key=lambda k: k["time"])
            self._interpolate_bone(bone_name, frames, t)

    def _interpolate_bone(self, bone_name, frames, t):
        """对单根骨骼在时间 t 做线性插值。"""
        if not frames:
            return
        # 找到 t 所在的区间
        prev = frames[0]
        nxt = frames[-1]
        for i in range(len(frames) - 1):
            if frames[i]["time"] <= t <= frames[i + 1]["time"]:
                prev = frames[i]
                nxt = frames[i + 1]
                break
        else:
            # t 超出范围，用最后一帧
            prev = frames[-1]
            nxt = frames[-1]

        t0 = prev["time"]
        t1 = nxt["time"]
        if t1 == t0:
            alpha = 1.0
        else:
            alpha = (t - t0) / (t1 - t0)
            alpha = max(0.0, min(1.0, alpha))

        # 两条帧的 type 必须一致才能插值（我们的数据保证了）
        frame_type = prev["type"]
        tf = self._get_transform(bone_name)
        if tf is None:
            return

        if frame_type == "rotate":
            ax = prev["axis"]
            ax_n = QVector3D(ax[0], ax[1], ax[2])
            angle = prev["angle"] + (nxt["angle"] - prev["angle"]) * alpha
            tf.setRotation(QQuaternion.fromAxisAndAngle(ax_n, angle))
        elif frame_type == "translate":
            p0 = prev["position"]
            p1 = nxt["position"]
            x = p0[0] + (p1[0] - p0[0]) * alpha
            y = p0[1] + (p1[1] - p0[1]) * alpha
            z = p0[2] + (p1[2] - p0[2]) * alpha
            tf.setTranslation(QVector3D(x, y, z))
        elif frame_type == "scale":
            s0 = prev["scale"]
            s1 = nxt["scale"]
            x = s0[0] + (s1[0] - s0[0]) * alpha
            y = s0[1] + (s1[1] - s0[1]) * alpha
            z = s0[2] + (s1[2] - s0[2]) * alpha
            tf.setScale(QVector3D(x, y, z))


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
def play_animation(animator, animation_name):
    """
    辅助函数：在给定 BoneAnimator 上播放指定动画。

    Parameters
    ----------
    animator : BoneAnimator
    animation_name : str
        必须是 ANIMATIONS 的 key 之一。
    """
    if not isinstance(animator, BoneAnimator):
        return
    animator.play(animation_name)
