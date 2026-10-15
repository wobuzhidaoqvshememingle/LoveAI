# -*- coding: utf-8 -*-
"""
gltf_loader.py - GLTF/GLB 模型加载接口

使用 Qt3DRender.QSceneLoader 加载 GLTF2 模型。
若模型包含骨骼动画，QSceneLoader 会自动加载；若需要提取动画 clip，
可在后续版本通过 QSceneLoader.animationNames() / setActiveAnimation()
进一步接入。当前版本只做"加载实体 + 失败回退"。
"""

import os
import logging

from PySide6.Qt3DCore import Qt3DCore
from PySide6.Qt3DRender import Qt3DRender
QEntity = Qt3DCore.QEntity
QSceneLoader = Qt3DRender.QSceneLoader

logger = logging.getLogger(__name__)


class GLTFLoader:
    """
    GLTF/GLB 模型加载器。

    用法：
        loader = GLTFLoader()
        entity = loader.load("assets/3d/pet.glb")
        if entity is None:
            # 回退到程序生成模型
            ...
    """

    def __init__(self, parent=None):
        self._parent = parent

    def load(self, path):
        """
        加载 GLTF/GLB 模型。

        Parameters
        ----------
        path : str
            模型文件绝对路径或相对路径。

        Returns
        -------
        QEntity or None
            成功返回 Qt3D 实体；失败返回 None。
        """
        if not path or not os.path.exists(path):
            logger.warning("GLTF 文件不存在: %s", path)
            return None

        try:
            loader = QSceneLoader(self._parent)
            # QSceneLoader 作为组件挂载到一个新 QEntity 上
            entity = QEntity()
            loader.setSource("file:///" + os.path.abspath(path).replace("\\", "/"))
            entity.addComponent(loader)

            # 简单校验：若加载后 entity 没有子节点，视为失败
            if len(entity.children()) == 0:
                logger.warning("GLTF 加载后无内容: %s", path)
                # 仍然返回 entity，让调用方决定是否回退
            return entity
        except Exception as e:
            logger.exception("GLTF 加载异常: %s", e)
            return None

    def list_animations(self, entity):
        """
        若模型包含骨骼动画，返回动画名称列表。
        （依赖 QSceneLoader.animationNames，不同版本 API 可能有差异。）
        """
        if entity is None:
            return []
        for comp in entity.components():
            if isinstance(comp, QSceneLoader):
                try:
                    return list(comp.animationNames())
                except Exception:
                    return []
        return []


def try_load_custom_model():
    """
    检查 assets/3d/pet.glb 是否存在，存在则加载，不存在返回 None。

    路径解析规则：
        相对于本文件所在目录，向上推 3 级到项目根，再拼 assets/3d/pet.glb。
        即：qt3d_pet/ -> app/ -> LoveAI-v2/ -> assets/3d/pet.glb

    Returns
    -------
    QEntity or None
    """
    here = os.path.dirname(os.path.abspath(__file__))
    # here = .../LoveAI-v2/app/qt3d_pet
    project_root = os.path.abspath(os.path.join(here, "..", ".."))
    candidate = os.path.join(project_root, "assets", "3d", "pet.glb")

    if not os.path.exists(candidate):
        return None

    loader = GLTFLoader()
    return loader.load(candidate)
