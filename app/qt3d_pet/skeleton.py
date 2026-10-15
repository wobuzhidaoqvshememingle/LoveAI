# -*- coding: utf-8 -*-
"""
skeleton.py - 16骨骼层级定义与可视化人形构建

骨骼列表（名称，父骨骼，默认位置，默认旋转）：
  0: hips（根，骨盆，位置(0,0,0)）
  1: spine（父hips，向上）
  2: neck（父spine，向上）
  3: head（父neck，向上）
  4: upper_arm_l（父spine，向左上）
  5: lower_arm_l（父upper_arm_l，向左下）
  6: hand_l（父lower_arm_l，末端）
  7: upper_arm_r（父spine，向右上）
  8: lower_arm_r（父upper_arm_r，向右下）
  9: hand_r（父lower_arm_r，末端）
  10: upper_leg_l（父hips，向左下）
  11: lower_leg_l（父upper_leg_l，向下）
  12: foot_l（父lower_leg_l，向前）
  13: upper_leg_r（父hips，向右下）
  14: lower_leg_r（父upper_leg_r，向下）
  15: foot_r（父lower_leg_r，向前）
"""

from PySide6.QtGui import QVector3D, QQuaternion, QColor
from PySide6.Qt3DCore import Qt3DCore
from PySide6.Qt3DExtras import Qt3DExtras
QEntity = Qt3DCore.QEntity
QTransform = Qt3DCore.QTransform
QSphereMesh = Qt3DExtras.QSphereMesh
QCylinderMesh = Qt3DExtras.QCylinderMesh
QCuboidMesh = Qt3DExtras.QCuboidMesh
QPhongMaterial = Qt3DExtras.QPhongMaterial


# ---------------------------------------------------------------------------
# 骨骼数据常量
# 每条骨骼：
#   name        : 骨骼唯一标识（str）
#   parent      : 父骨骼名（str），根骨骼为 None
#   position    : 相对于父骨骼的局部位置 (x, y, z)
#   rotation    : 相对于父骨骼的局部旋转 (axis_x, axis_y, axis_z, angle_deg)
#   mesh_type   : 可视化几何体类型（用于 build_skeleton_entity）
#   mesh_params : 几何体参数（半径/高度/尺寸）
# ---------------------------------------------------------------------------
SKELETON_DATA = {
    "hips": {
        "index": 0,
        "parent": None,
        "position": (0.0, 0.9, 0.0),
        "rotation": (0.0, 0.0, 1.0, 0.0),
        "mesh_type": "cuboid",
        "mesh_params": (0.22, 0.14, 0.16),   # 宽 高 深
    },
    "spine": {
        "index": 1,
        "parent": "hips",
        "position": (0.0, 0.12, 0.0),
        "rotation": (0.0, 0.0, 1.0, 0.0),
        "mesh_type": "cylinder",
        "mesh_params": (0.08, 0.28),          # 半径 高度
    },
    "neck": {
        "index": 2,
        "parent": "spine",
        "position": (0.0, 0.16, 0.0),
        "rotation": (0.0, 0.0, 1.0, 0.0),
        "mesh_type": "cylinder",
        "mesh_params": (0.04, 0.08),
    },
    "head": {
        "index": 3,
        "parent": "neck",
        "position": (0.0, 0.10, 0.0),
        "rotation": (0.0, 0.0, 1.0, 0.0),
        "mesh_type": "sphere",
        "mesh_params": (0.10,),               # 半径
    },
    "upper_arm_l": {
        "index": 4,
        "parent": "spine",
        "position": (-0.14, 0.10, 0.0),
        "rotation": (0.0, 0.0, 1.0, 15.0),    # 略微外展
        "mesh_type": "cylinder",
        "mesh_params": (0.035, 0.22),
    },
    "lower_arm_l": {
        "index": 5,
        "parent": "upper_arm_l",
        "position": (-0.02, -0.20, 0.0),
        "rotation": (0.0, 0.0, 1.0, 0.0),
        "mesh_type": "cylinder",
        "mesh_params": (0.03, 0.20),
    },
    "hand_l": {
        "index": 6,
        "parent": "lower_arm_l",
        "position": (0.0, -0.18, 0.0),
        "rotation": (0.0, 0.0, 1.0, 0.0),
        "mesh_type": "sphere",
        "mesh_params": (0.045,),
    },
    "upper_arm_r": {
        "index": 7,
        "parent": "spine",
        "position": (0.14, 0.10, 0.0),
        "rotation": (0.0, 0.0, 1.0, -15.0),
        "mesh_type": "cylinder",
        "mesh_params": (0.035, 0.22),
    },
    "lower_arm_r": {
        "index": 8,
        "parent": "upper_arm_r",
        "position": (0.02, -0.20, 0.0),
        "rotation": (0.0, 0.0, 1.0, 0.0),
        "mesh_type": "cylinder",
        "mesh_params": (0.03, 0.20),
    },
    "hand_r": {
        "index": 9,
        "parent": "lower_arm_r",
        "position": (0.0, -0.18, 0.0),
        "rotation": (0.0, 0.0, 1.0, 0.0),
        "mesh_type": "sphere",
        "mesh_params": (0.045,),
    },
    "upper_leg_l": {
        "index": 10,
        "parent": "hips",
        "position": (-0.08, -0.10, 0.0),
        "rotation": (0.0, 0.0, 1.0, 0.0),
        "mesh_type": "cylinder",
        "mesh_params": (0.05, 0.28),
    },
    "lower_leg_l": {
        "index": 11,
        "parent": "upper_leg_l",
        "position": (0.0, -0.26, 0.0),
        "rotation": (0.0, 0.0, 1.0, 0.0),
        "mesh_type": "cylinder",
        "mesh_params": (0.04, 0.26),
    },
    "foot_l": {
        "index": 12,
        "parent": "lower_leg_l",
        "position": (0.0, -0.24, 0.04),
        "rotation": (0.0, 0.0, 1.0, 0.0),
        "mesh_type": "cuboid",
        "mesh_params": (0.08, 0.04, 0.14),
    },
    "upper_leg_r": {
        "index": 13,
        "parent": "hips",
        "position": (0.08, -0.10, 0.0),
        "rotation": (0.0, 0.0, 1.0, 0.0),
        "mesh_type": "cylinder",
        "mesh_params": (0.05, 0.28),
    },
    "lower_leg_r": {
        "index": 14,
        "parent": "upper_leg_r",
        "position": (0.0, -0.26, 0.0),
        "rotation": (0.0, 0.0, 1.0, 0.0),
        "mesh_type": "cylinder",
        "mesh_params": (0.04, 0.26),
    },
    "foot_r": {
        "index": 15,
        "parent": "lower_leg_r",
        "position": (0.0, -0.24, 0.04),
        "rotation": (0.0, 0.0, 1.0, 0.0),
        "mesh_type": "cuboid",
        "mesh_params": (0.08, 0.04, 0.14),
    },
}


def _make_mesh(mesh_type, params):
    """根据类型创建 Qt3D 基本几何体。"""
    if mesh_type == "sphere":
        m = QSphereMesh()
        m.setRadius(params[0])
        return m
    if mesh_type == "cylinder":
        m = QCylinderMesh()
        m.setRadius(params[0])
        m.setLength(params[1])
        return m
    if mesh_type == "cuboid":
        m = QCuboidMesh()
        m.setXExtent(params[0])
        m.setYExtent(params[1])
        m.setZExtent(params[2])
        return m
    # 默认返回一个小球，避免崩溃
    m = QSphereMesh()
    m.setRadius(0.05)
    return m


def _make_material(color):
    """创建 Phong 材质。"""
    mat = QPhongMaterial()
    mat.setDiffuse(color)
    mat.setSpecular(QColor(80, 80, 80))
    mat.setShininess(20.0)
    return mat


# 每根骨骼的默认颜色（柔和拟人配色）
_BONE_COLORS = {
    "hips": QColor(120, 130, 180),
    "spine": QColor(140, 150, 200),
    "neck": QColor(200, 170, 150),
    "head": QColor(255, 220, 190),
    "upper_arm_l": QColor(160, 170, 210),
    "lower_arm_l": QColor(170, 180, 220),
    "hand_l": QColor(255, 220, 190),
    "upper_arm_r": QColor(160, 170, 210),
    "lower_arm_r": QColor(170, 180, 220),
    "hand_r": QColor(255, 220, 190),
    "upper_leg_l": QColor(110, 120, 170),
    "lower_leg_l": QColor(120, 130, 180),
    "foot_l": QColor(80, 80, 100),
    "upper_leg_r": QColor(110, 120, 170),
    "lower_leg_r": QColor(120, 130, 180),
    "foot_r": QColor(80, 80, 100),
}


def build_skeleton_entity(root_entity=None):
    """
    构建可视化人形骨骼实体树。

    Parameters
    ----------
    root_entity : QEntity, optional
        场景根节点。若为 None，会创建一个新的顶层 QEntity。

    Returns
    -------
    (QEntity, dict[str, QEntity])
        返回 (根实体, {骨骼名: 对应QEntity})，方便后续动画直接操作。
    """
    if root_entity is None:
        root_entity = QEntity()

    bone_entities = {}

    # 按 index 顺序创建，保证父骨骼先存在
    ordered = sorted(SKELETON_DATA.items(), key=lambda kv: kv[1]["index"])

    for bone_name, info in ordered:
        ent = QEntity()

        # --- Transform ---
        transform = QTransform()
        pos = info["position"]
        rot = info["rotation"]
        transform.setTranslation(QVector3D(pos[0], pos[1], pos[2]))
        axis = QVector3D(rot[0], rot[1], rot[2])
        if axis.lengthSquared() > 1e-6:
            transform.setRotation(QQuaternion.fromAxisAndAngle(axis, rot[3]))
        ent.addComponent(transform)

        # --- Mesh ---
        mesh = _make_mesh(info["mesh_type"], info["mesh_params"])
        ent.addComponent(mesh)

        # --- Material ---
        mat = _make_material(_BONE_COLORS.get(bone_name, QColor(200, 200, 200)))
        ent.addComponent(mat)

        # --- 挂载到父节点 ---
        parent_name = info["parent"]
        if parent_name is None:
            ent.setParent(root_entity)
        else:
            parent_ent = bone_entities.get(parent_name)
            if parent_ent is not None:
                ent.setParent(parent_ent)
            else:
                # 父骨骼缺失时挂到根，避免崩溃
                ent.setParent(root_entity)

        bone_entities[bone_name] = ent

    return root_entity, bone_entities


def get_default_transform(bone_name):
    """
    读取某根骨骼的默认 Transform 数据，方便动画回放时复位。

    Returns
    -------
    dict with keys: translation (QVector3D), rotation (QQuaternion)
    """
    info = SKELETON_DATA[bone_name]
    pos = info["position"]
    rot = info["rotation"]
    t = QVector3D(pos[0], pos[1], pos[2])
    axis = QVector3D(rot[0], rot[1], rot[2])
    if axis.lengthSquared() > 1e-6:
        r = QQuaternion.fromAxisAndAngle(axis, rot[3])
    else:
        r = QQuaternion()
    return {"translation": t, "rotation": r}
