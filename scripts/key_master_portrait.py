# -*- coding: utf-8 -*-
"""绿幕母版色键抠像 → 透明底真人感立绘（沉浸桌面右侧立绘素材）。

输入：desktop-pet-electron/digital-human/local/assets/master_greenscreen.png（用户锁定母版，勿重新设计）
输出：assets/pet_portrait/master_keyed.png（RGBA 透明底）

算法（不依赖 rembg，opencv + numpy 即可，项目 venv 已装）：
  1. 色键距离 key = G - max(R, B)，双阈值取硬 mask；
  2. 硬 mask 收缩 1px 去绿边，再高斯模糊得羽化 alpha；
  3. despill：残留绿边像素把 G 压到 max(R,B)，防"绿光晕"；
  4. 裁掉全透明外边距，输出紧凑立绘。
可复跑：python scripts/key_master_portrait.py [--src 路径] [--out 路径]
"""
from __future__ import annotations

import argparse
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
DEFAULT_SRC = os.path.join(ROOT, "desktop-pet-electron", "digital-human",
                           "local", "assets", "master_greenscreen.png")
DEFAULT_OUT = os.path.join(ROOT, "assets", "pet_portrait", "master_keyed.png")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--hard", type=float, default=40.0, help="硬阈值（色键距离）")
    ap.add_argument("--soft", type=float, default=12.0, help="软阈值（羽化下界）")
    a = ap.parse_args()

    img = cv2.imread(a.src, cv2.IMREAD_COLOR)
    if img is None:
        print(f"[FAIL] 读不到母版: {a.src}")
        return 2
    h, w = img.shape[:2]
    bgr = img.astype(np.float32)
    B, G, R = bgr[:, :, 0], bgr[:, :, 1], bgr[:, :, 2]
    key = G - np.maximum(R, B)

    hard = (key > a.hard).astype(np.uint8) * 255          # 绿幕=255
    # 收缩去绿边（腐蚀），再羽化
    k = np.ones((3, 3), np.uint8)
    hard = cv2.erode(hard, k, iterations=1)
    soft = cv2.GaussianBlur(hard, (5, 5), 0)
    alpha = 255 - soft                                     # 人物=不透明
    # 软过渡带：key 在 soft~hard 之间的像素按距离插值，保发丝
    band = (key > a.soft) & (key <= a.hard)
    t = (key[band] - a.soft) / max(1e-6, (a.hard - a.soft))
    alpha_f = alpha.astype(np.float32)
    alpha_f[band] = 255.0 * (1.0 - t)
    alpha = np.clip(alpha_f, 0, 255).astype(np.uint8)

    # despill：alpha>0 且仍偏绿的像素，G 压到 max(R,B)
    spill = (alpha > 0) & (key > 6)
    g_new = np.minimum(G, np.maximum(R, B))
    G2 = np.where(spill, g_new, G)

    out = np.dstack([B, G2, R, alpha.astype(np.float32)]).astype(np.uint8)

    # 裁外边距
    ys, xs = np.where(alpha > 8)
    if len(xs) == 0:
        print("[FAIL] 抠像后无有效像素，检查阈值")
        return 1
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    out = out[y0:y1, x0:x1]

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    cv2.imwrite(a.out, out)
    ratio = (alpha > 8).sum() / alpha.size
    print(f"[OK] saved {a.out} size={out.shape[1]}x{out.shape[0]} 不透明占比={ratio:.2%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
