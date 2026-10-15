# -*- coding: utf-8 -*-
"""一次性脚本：用 PIL 在 assets/pet_sprites/ 下生成占位立绘帧。

生成内容：
  - neutral / happy / sad / thinking / surprised  （整脸，含睁眼+对应嘴型）
  - eyes_open / eyes_closed                         （仅眼睛，透明底，用于眨眼叠加）
  - mouth_a / mouth_i / mouth_u / mouth_e / mouth_o （仅嘴型，透明底，用于口型同步叠加）

所有图都是 256x256 RGBA PNG。后续可直接替换同名文件为真实立绘。
"""
import os
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "assets", "pet_sprites")
os.makedirs(OUT, exist_ok=True)

W = H = 256
SKIN = (255, 215, 181, 255)      # 肤色
HAIR = (90, 60, 40, 255)         # 发色
DARK = (40, 30, 30, 255)         # 眼睛/嘴
BLUSH = (255, 150, 150, 160)     # 腮红

CX, CY = 128, 120                # 脸中心
R = 80                            # 脸半径


def _base_canvas() -> tuple:
    """返回 (img, draw)，画好头+头发轮廓，五官由调用方叠加。"""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 头发（后脑勺大圆，偏上）
    d.ellipse([CX - R - 8, CY - R - 22, CX + R + 8, CY + R - 10], fill=HAIR)
    # 脸
    d.ellipse([CX - R, CY - R, CX + R, CY + R], fill=SKIN)
    # 腮红
    d.ellipse([CX - R + 14, CY + 18, CX - R + 34, CY + 34], fill=BLUSH)
    d.ellipse([CX + R - 34, CY + 18, CX + R - 14, CY + 34], fill=BLUSH)
    return img, d


def _draw_open_eyes(d: ImageDraw.ImageDraw):
    """画睁眼（两个圆眼珠）。"""
    for ex in (CX - 28, CX + 28):
        d.ellipse([ex - 9, CY - 22, ex + 9, CY - 4], fill=(255, 255, 255, 255))
        d.ellipse([ex - 5, CY - 18, ex + 5, CY - 8], fill=DARK)
        d.ellipse([ex - 2, CY - 16, ex + 1, CY - 13], fill=(255, 255, 255, 255))


def _draw_closed_eyes(d: ImageDraw.ImageDraw):
    """画闭眼（两条弧线）。"""
    for ex in (CX - 28, CX + 28):
        d.arc([ex - 9, CY - 18, ex + 9, CY - 6], start=0, end=180, fill=DARK, width=3)


def _mouth_neutral(d: ImageDraw.ImageDraw):
    d.line([CX - 12, CY + 38, CX + 12, CY + 38], fill=DARK, width=3)


def _mouth_smile(d: ImageDraw.ImageDraw):
    d.arc([CX - 16, CY + 28, CX + 16, CY + 50], start=15, end=165, fill=DARK, width=3)


def _mouth_frown(d: ImageDraw.ImageDraw):
    d.arc([CX - 16, CY + 40, CX + 16, CY + 62], start=195, end=345, fill=DARK, width=3)


def _mouth_small_o(d: ImageDraw.ImageDraw):
    d.ellipse([CX - 7, CY + 32, CX + 7, CY + 46], outline=DARK, width=3)


def _mouth_big_o(d: ImageDraw.ImageDraw):
    d.ellipse([CX - 11, CY + 28, CX + 11, CY + 52], outline=DARK, width=3)


# ---- 5 种整脸表情 ----
EXPRESSIONS = {
    "neutral":   (_draw_open_eyes, _mouth_neutral),
    "happy":     (_draw_open_eyes, _mouth_smile),
    "sad":       (_draw_open_eyes, _mouth_frown),
    "thinking":  (_draw_open_eyes, _mouth_small_o),
    "surprised": (_draw_open_eyes, _mouth_big_o),
}

for name, (eye_fn, mouth_fn) in EXPRESSIONS.items():
    img, d = _base_canvas()
    eye_fn(d)
    mouth_fn(d)
    img.save(os.path.join(OUT, f"{name}.png"))
    print("wrote", f"{name}.png")


# ---- 仅眼睛（透明底，叠加用）----
# 睁眼
img_eo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
_draw_open_eyes(ImageDraw.Draw(img_eo))
img_eo.save(os.path.join(OUT, "eyes_open.png"))
print("wrote eyes_open.png")

# 闭眼
img_ec = Image.new("RGBA", (W, H), (0, 0, 0, 0))
_draw_closed_eyes(ImageDraw.Draw(img_ec))
img_ec.save(os.path.join(OUT, "eyes_closed.png"))
print("wrote eyes_closed.png")


# ---- 仅嘴型（透明底，叠加用，用于口型同步）----
def _mouth_canvas() -> tuple:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


# a：大开口（嘴型最开）
img, d = _mouth_canvas()
d.ellipse([CX - 14, CY + 28, CX + 14, CY + 52], fill=DARK)
img.save(os.path.join(OUT, "mouth_a.png"))

# i：扁宽（咧开）
img, d = _mouth_canvas()
d.rounded_rectangle([CX - 16, CY + 36, CX + 16, CY + 42], radius=3, fill=DARK)
img.save(os.path.join(OUT, "mouth_i.png"))

# u：小圆形（噘嘴）
img, d = _mouth_canvas()
d.ellipse([CX - 7, CY + 32, CX + 7, CY + 46], fill=DARK)
img.save(os.path.join(OUT, "mouth_u.png"))

# e：半开（椭圆）
img, d = _mouth_canvas()
d.ellipse([CX - 11, CY + 32, CX + 11, CY + 46], fill=DARK)
img.save(os.path.join(OUT, "mouth_e.png"))

# o：大圆（惊讶口型）
img, d = _mouth_canvas()
d.ellipse([CX - 12, CY + 28, CX + 12, CY + 52], fill=DARK)
img.save(os.path.join(OUT, "mouth_o.png"))

for n in ("a", "i", "u", "e", "o"):
    print("wrote", f"mouth_{n}.png")

print("ALL DONE ->", OUT)
