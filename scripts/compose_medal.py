# -*- coding: utf-8 -*-
"""成就勋章合成管线（R3.3-84 定稿实现）。

规则（用户历史决策）：
  - 边框/顶栏/底部金句区/地球插画 = 母版原像素，永远不动、不重绘；
  - 内容区（主题图 + 成就名 + 描述 + 解锁率）= 按成就替换；
  - 月桂枝与奖杯为母版装饰块，用 lighten 混叠贴回（亮像素保留、暗底透出主题图）；
  - 金句文字可选替换：替换时仅填充 quote_text 矩形并重写两行，面板边框与地球不动。

用法：
  python scripts/compose_medal.py \
    --theme 主题图.png --name "成就名" \
    --desc "第一行" --desc "第二行" --rate 43.2 \
    [--quote "金句第一行" --quote "金句第二行"] --out 输出.png
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.normpath(os.path.join(HERE, "..", "assets"))
MASTER = os.path.join(ASSETS, "medal_master_source.png")
SLOTS = os.path.join(ASSETS, "medal_slots.json")

GOLD = (232, 196, 106)
GOLD_DEEP = (198, 156, 74)
WHITE = (246, 246, 246)
FONT_KAI = r"C:\Windows\Fonts\simkai.ttf"
FONT_SONG = r"C:\Windows\Fonts\simsun.ttc"
FONT_HEI = r"C:\Windows\Fonts\simhei.ttf"


def fit_font(path: str, text: str, max_w: int, start: int, min_size: int = 18) -> ImageFont.FreeTypeFont:
    size = start
    while size > min_size:
        f = ImageFont.truetype(path, size)
        b = f.getbbox(text)
        if b[2] - b[0] <= max_w:
            return f
        size -= 2
    return ImageFont.truetype(path, min_size)


def center(draw: ImageDraw.ImageDraw, box, text, font, fill, shadow=True, dy=0):
    x0, y0, x1, y1 = box
    b = font.getbbox(text)
    w, h = b[2] - b[0], b[3] - b[1]
    tx = x0 + (x1 - x0 - w) / 2 - b[0]
    ty = y0 + (y1 - y0 - h) / 2 - b[1] + dy
    if shadow:
        draw.text((tx + 3, ty + 3), text, font=font, fill=(0, 0, 0))
    draw.text((tx, ty), text, font=font, fill=fill)
    return (tx, ty, w, h)


def cover_paste(canvas: Image.Image, theme: Image.Image, box) -> None:
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    tw, th = theme.size
    s = max(bw / tw, bh / th)
    nw, nh = int(tw * s), int(th * s)
    t = theme.resize((nw, nh), Image.LANCZOS)
    lx, ly = (nw - bw) // 2, (nh - bh) // 2
    canvas.paste(t.crop((lx, ly, lx + bw, ly + bh)), (x0, y0))


def lighten_paste(canvas: Image.Image, master: Image.Image, box) -> None:
    x0, y0, x1, y1 = box
    piece = master.crop(box)
    region = canvas.crop(box)
    canvas.paste(ImageChops.lighter(region, piece), (x0, y0))


def main() -> int:
    ap = argparse.ArgumentParser(description="按地球online成就馆版式合成单枚勋章")
    ap.add_argument("--theme", required=True, help="内容区背景主题图路径")
    ap.add_argument("--name", required=True)
    ap.add_argument("--desc", action="append", default=[], help="描述行，可给两次")
    ap.add_argument("--rate", required=True, help="解锁率百分比数字，如 43.2")
    ap.add_argument("--quote", action="append", default=[], help="金句行，可给两次；不给则保留母版原金句")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    master = Image.open(MASTER).convert("RGB")
    slots = json.load(open(SLOTS, encoding="utf-8"))
    S = slots["slots"]
    canvas = master.copy()

    # 1) 内容区整块换成新主题图（原图文字/月桂/奖杯随之被覆盖，后面贴回装饰块）
    cover_paste(canvas, Image.open(a.theme).convert("RGB"), tuple(S["content"]))

    # 2) 月桂枝、奖杯：从母版 lighten 贴回
    for key in ("laurel_l", "laurel_r"):
        lighten_paste(canvas, master, tuple(S[key]))

    d = ImageDraw.Draw(canvas)

    # 3) 成就名（楷体金色，自适应宽度）
    tx0, ty0, tx1, ty1 = S["title"]
    f_name = fit_font(FONT_KAI, a.name, int((tx1 - tx0) * 0.98), 96)
    center(d, (tx0, ty0, tx1, ty1), a.name, f_name, GOLD)

    # 4) 描述两行（宋体白）
    dx0, dy0, dx1, dy1 = S["desc"]
    lines = a.desc[:2] or ["", ""]
    line_h = (dy1 - dy0) / 2
    for i, ln in enumerate(lines):
        if not ln:
            continue
        f = fit_font(FONT_SONG, ln, int((dx1 - dx0) * 0.98), 46)
        center(d, (dx0, dy0 + i * line_h, dx1, dy0 + (i + 1) * line_h), ln, f, WHITE, shadow=False)

    # 5) 解锁率：奖杯块 + "XX% 的玩家拥有此成就"
    rx0, ry0, rx1, ry1 = S["rate"]
    rate_txt = f"{a.rate}% 的玩家拥有此成就"
    f_pct = ImageFont.truetype(FONT_SONG, 58)
    f_suf = ImageFont.truetype(FONT_SONG, 30)
    pct, suf = rate_txt.split("% ", 1)
    bp = f_pct.getbbox(pct + "%")
    bs = f_suf.getbbox(suf)
    trophy_w = int((rx1 - rx0) * 0.16)
    gap = 14
    total = trophy_w + gap + (bp[2] - bp[0]) + gap + (bs[2] - bs[0])
    cx = rx0 + (rx1 - rx0 - total) / 2
    cy = ry0 + (ry1 - ry0) / 2
    # 奖杯：母版 rate 行左侧原像素块，lighten 贴到计算位置
    mw = master.size[0]
    tb = master.crop((int(0.306 * mw), ry0, int(0.306 * mw) + trophy_w, ry1))
    region = canvas.crop((int(cx), ry0, int(cx) + trophy_w, ry1))
    canvas.paste(ImageChops.lighter(region, tb.resize((trophy_w, ry1 - ry0))), (int(cx), ry0))
    d = ImageDraw.Draw(canvas)
    px_ = cx + trophy_w + gap
    d.text((px_, cy - (bp[3] - bp[1]) / 2 - bp[1]), pct + "%", font=f_pct, fill=GOLD)
    px_ += (bp[2] - bp[0]) + gap
    d.text((px_, cy - (bs[3] - bs[1]) / 2 - bs[1] + 6), suf, font=f_suf, fill=WHITE)

    # 6) 金句（可选替换；面板边框与地球在 quote_text 矩形之外，不受影响）
    if a.quote:
        qx0, qy0, qx1, qy1 = S["quote_text"]
        # 逐行取面板左缘同高度像素填充，保留竖向渐变、避免纯色矩形接缝
        for y in range(qy0, qy1 + 1):
            c = master.getpixel((int(master.size[0] * 0.05), y))
            d.line([(qx0, y), (qx1, y)], fill=c)
        qh = (qy1 - qy0) / 2
        for i, ln in enumerate(a.quote[:2]):
            f = fit_font(FONT_KAI, ln, int((qx1 - qx0) * 0.96), 46)
            center(d, (qx0, qy0 + i * qh, qx1, qy0 + (i + 1) * qh), ln, f, GOLD_DEEP, shadow=False)

    canvas.save(a.out)
    print("saved", a.out, canvas.size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
