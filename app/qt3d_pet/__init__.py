# -*- coding: utf-8 -*-
"""
qt3d_pet - LoveAI-v2 的 Qt3D 3D 骨骼桌宠模块

导出：
    PetView         : Qt3DWindow 封装的桌宠窗口
    SKELETON_DATA   : 16 骨骼数据常量
    ANIMATIONS      : 7 种动画数据常量
    GLTFLoader      : GLTF/GLB 模型加载器
"""

from .skeleton import SKELETON_DATA, build_skeleton_entity
from .animations import ANIMATIONS, BoneAnimator, play_animation
from .gltf_loader import GLTFLoader, try_load_custom_model
from .pet_view import PetView

__all__ = [
    "PetView",
    "SKELETON_DATA",
    "ANIMATIONS",
    "GLTFLoader",
    "build_skeleton_entity",
    "BoneAnimator",
    "play_animation",
    "try_load_custom_model",
]
