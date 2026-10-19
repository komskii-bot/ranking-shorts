"""Bojji Explains: long-form 16:9 explainer renderer.

python -m pipeline.explainer explainers/<id> [--offline] [--preview SECONDS] [--frames]

Reads explainers/<id>/script.json + audio/<part>.m4a (the narrator's recordings),
cleans the voice, times every beat to the recording, pulls landscape stock footage,
and renders 1920x1080 30 fps with Bojji (full-body frames + reaction busts),
animated graphics, captions, CTAs and SFX. Output in out/explainer/.
"""
import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import explainer_audio as A
from . import stock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = os.path.join(ROOT, "assets", "fonts", "Poppins-Bold.ttf")
W, H, FPS = 1920, 1080, 30
CX = 1110                      # centre of the graphics area (Bojji lives on the left)
SFX = os.path.join(ROOT, "assets", "sfx")
FULL = os.path.join(ROOT, "assets", "bojji_full")
EMO = os.path.join(ROOT, "assets", "emotions")
TITLE_SECS, CHAPTER_SECS = 3.6, 2.2
SFX_GAIN = {"whoosh": 0.30, "pop": 0.40, "ding": 0.35, "boom": 0.45, "tick": 0.3}

_fonts = {}


def font(size):
    if size not in _fonts:
        _fonts[size] = ImageFont.truetype(FONT, size)
    return _fonts[size]


def rgba(h, a=255):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


def ease(x):
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def back(x):  # overshoot pop
    x = min(max(x, 0.0), 1.0)
    c = 1.7
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


def text_w(d, s, f):
    b = d.textbbox((0, 0), s, font=f)
    return b[2] - b[0]


def draw_text(d, xy, s, f, fill, anchor="mm", stroke=6, sfill=(0, 0, 0, 255)):
    d.text(xy, s, font=f, fill=fill, anchor=anchor, stroke_width=stroke, stroke_fill=sfill)


def shadow(layer, blur=16, alpha=150):
    a = layer.getchannel("A").filter(ImageFilter.GaussianBlur(blur))
    a = a.point(lambda v: v * alpha // 255)
    sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    sh.putalpha(a)
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    out.alpha_composite(sh, (0, 8))
    out.alpha_composite(layer)
    return out


def pill(d, box, fill, r=None, outline=None, width=0):
    r = r if r is not None else (box[3] - box[1]) // 2
    d.rounded_rectangle(box, r, fill=fill, outline=outline, width=width)


# ---------------------------------------------------------------- Bojji sprites

class Bojji:
    def __init__(self, height=640):
        self.h = height
        self.cache = {}

    def full(self, name, h=None):
        h = h or self.h
        key = ("f", name, h)
        if key not in self.cache:
            im = Image.open(os.path.join(FULL, name + ".png")).convert("RGBA")
            # walk/stand frames share one scale so the walk doesn't "breathe"
            self.cache[key] = im.resize((int(im.width * h / im.height), h), Image.LANCZOS)
        return self.cache[key]

    def bust(self, emo, h=560):
        key = ("e", emo, h)
        if key not in self.cache:
            im = Image.open(os.path.join(EMO, emo + ".png")).convert("RGBA")
            self.cache[key] = im.resize((int(im.width * h / im.height), h), Image.LANCZOS)
        return self.cache[key]

    def place(self, frame, mode, t, dur):
        """Composite Bojji for this beat; t = seconds into the beat."""
        if not mode:
            return
        floor = H - 18
        home_x = 70
        if mode.startswith("react:"):
            im = self.bust(mode.split(":", 1)[1])
            k = back(t / 0.35)
            bob = int(5 * math.sin(t * 2.4))
            y = int(H - im.height * k * 0.92) + bob + 20
            frame.alpha_composite(im, (40, max(y, H - im.height + 20)) if k >= 1 else (40, y))
            return
        if mode == "walk":
            walk_t = 1.7
            if t < walk_t:
                n = int(t * 8) % 4
                im = self.full(f"walk_{n + 1:02d}")
                x = int(-im.width + (home_x + im.width) * ease(t / walk_t * 1.05))
                y = floor - im.height - (6 if n in (1, 3) else 0)
                frame.alpha_composite(im, (x, y))
                return
            mode, t = "stand", t - walk_t
        if mode == "stand":
            im = self.full("stand")
            frame.alpha_composite(im, (home_x, floor - im.height + int(3 * math.sin(t * 2.2))))
        elif mode == "point":
            im = self.full("point", int(self.h * 1.0))
            k = ease(t / 0.3)
            frame.alpha_composite(im, (int(home_x - 30 - 60 * (1 - k)), floor - im.height + int(3 * math.sin(t * 2))))
        elif mode == "wave":
            im = self.full("wave_01" if int(t * 4) % 2 == 0 else "wave_02")
            frame.alpha_composite(im, (home_x - 20, floor - im.height))
        elif mode == "jump":
            cyc = t % 1.4
            if cyc < 0.7:
                im = self.full("jump", int(self.h * 0.95))
                hop = int(120 * math.sin(math.pi * cyc / 0.7))
                frame.alpha_composite(im, (home_x - 60, floor - im.height - hop))
            else:
                im = self.full("stand")
                frame.alpha_composite(im, (home_x, floor - im.height))


# ---------------------------------------------------------------- graphics

def base_layer():
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def g_headline(v, t, dur, accent):
    lay = base_layer(); d = ImageDraw.Draw(lay)
    lines = v["lines"]; hl = set(v.get("hl", [])); col = rgba(v.get("color", accent))
    size = 150 if max(len(l) for l in lines) <= 12 else 118
    f = font(size)
    total_h = len(lines) * size * 1.05
    y = H / 2 - total_h / 2 + size / 2 - 40
    for i, line in enumerate(lines):
        k = back((t - 0.12 * i) / 0.4)
        if k <= 0:
            continue
        words = line.split(" ")
        widths = [text_w(d, w + " ", f) for w in words]
        x = CX - sum(widths) / 2
        for w, ww in zip(words, widths):
            fill = col if (w in hl or w.strip("?!:,") in hl) else (255, 255, 255, 255)
            d.text((x, y + i * size * 1.05 + (1 - k) * 60), w, font=f, fill=fill, anchor="lm",
                   stroke_width=9, stroke_fill=(0, 0, 0, 255))
            x += ww
    if v.get("sub") and t > 0.4:
        f2 = font(44)
        sw = text_w(d, v["sub"], f2) + 70
        yy = y + len(lines) * size * 1.05 + 10
        pill(d, (CX - sw / 2, yy - 38, CX + sw / 2, yy + 38), (0, 0, 0, 190))
        d.text((CX, yy), v["sub"], font=f2, fill=(255, 255, 255, 255), anchor="mm")
    return shadow(lay)


def g_label(text, t, accent):
    lay = base_layer(); d = ImageDraw.Draw(lay)
    k = back(t / 0.35)
    if k <= 0:
        return lay
    f = font(50)
    w = text_w(d, text, f) + 90
    y = 150 - (1 - k) * 40
    pill(d, (CX - w / 2, y - 48, CX + w / 2, y + 48), (0, 0, 0, 200), outline=rgba(accent), width=5)
    d.text((CX, y), text, font=f, fill=(255, 255, 255, 255), anchor="mm")
    return lay


def staggered(n, t, dur, first=0.15, gap=None):
    gap = gap or min(1.1, max(0.5, (dur * 0.6) / max(n, 1)))
    return [ease((t - first - i * gap) / 0.3) for i in range(n)], [back((t - first - i * gap) / 0.35) for i in range(n)]


def g_tags(v, t, dur, accent):
    lay = base_layer(); d = ImageDraw.Draw(lay)
    items = v["items"]; f = font(78)
    _, ks = staggered(len(items), t, dur)
    widths = [text_w(d, s, f) + 110 for s in items]
    gap = 40
    total = sum(widths) + gap * (len(items) - 1)
    if total > 1450:  # stack vertically
        for i, (s, k) in enumerate(zip(items, ks)):
            if k <= 0:
                continue
            y = H / 2 - 160 + i * 150
            w = widths[i] * k
            pill(d, (CX - w / 2, y - 62, CX + w / 2, y + 62), rgba(accent))
            if k > 0.6:
                d.text((CX, y), s, font=f, fill=(20, 20, 20, 255), anchor="mm")
        return shadow(lay)
    x = CX - total / 2
    for s, w, k in zip(items, widths, ks):
        if k > 0:
            cx = x + w / 2; y = H / 2 - 60 + (1 - k) * 50
            pill(d, (cx - w / 2 * k, y - 66, cx + w / 2 * k, y + 66), rgba(accent))
            if k > 0.6:
                d.text((cx, y), s, font=f, fill=(20, 20, 20, 255), anchor="mm")
        x += w + gap
    return shadow(lay)


def g_counter(v, t, dur, accent):
    lay = base_layer(); d = ImageDraw.Draw(lay)
    k = ease((t - 0.2) / 1.4)
    val = int(round(v["value"] * k))
    s = f"{val:,}{v.get('suffix', '')}"
    if t > 0.05:
        draw_text(d, (CX, 330), v["top"], font(54), (255, 255, 255, 255), stroke=5)
    draw_text(d, (CX, 520), s, font(230), rgba(accent), stroke=10)
    if t > 1.7:
        kb = back((t - 1.7) / 0.35)
        f = font(48)
        w = (text_w(d, v["bottom"], f) + 80) * kb
        pill(d, (CX - w / 2, 690, CX + w / 2, 780), (0, 0, 0, 200))
        if kb > 0.7:
            d.text((CX, 735), v["bottom"], font=f, fill=(255, 255, 255, 255), anchor="mm")
    return shadow(lay)


def g_range(v, t, dur, accent):
    lay = base_layer(); d = ImageDraw.Draw(lay)
    x0, x1, y = CX - 600, CX + 600, 560
    draw_text(d, (CX, 330), v["label"], font(56), (255, 255, 255, 255), stroke=5)
    pill(d, (x0, y - 34, x1, y + 34), (255, 255, 255, 60))
    sc = lambda val: x0 + (x1 - x0) * (val - v["min"]) / (v["max"] - v["min"])
    k = ease((t - 0.3) / 0.9)
    lo, hi = sc(v["low"]), sc(v["high"])
    mid = (lo + hi) / 2
    a, b = mid - (mid - lo) * k, mid + (hi - mid) * k
    if k > 0:
        pill(d, (a, y - 34, b, y + 34), rgba("#3DDC84"))
    # danger zone
    if t > 1.2:
        kk = ease((t - 1.2) / 0.6)
        pill(d, (sc(v["high"]) + 30, y - 34, sc(v["high"]) + 30 + (x1 - sc(v["high"]) - 30) * kk, y + 34), rgba("#FF3B3B"))
        if kk > 0.8:
            draw_text(d, ((sc(v["high"]) + x1) / 2 + 15, y + 100), "TOO HOT", font(46), rgba("#FF3B3B"), stroke=5)
    if k > 0.8:
        draw_text(d, ((lo + hi) / 2, y - 115), f"{v['low']}–{v['high']}{v['unit']}", font(96), rgba("#3DDC84"), stroke=8)
        draw_text(d, ((lo + hi) / 2, y + 100), "JUST RIGHT", font(46), rgba("#3DDC84"), stroke=5)
    return shadow(lay)


def g_bars(v, t, dur, accent):
    lay = base_layer(); d = ImageDraw.Draw(lay)
    draw_text(d, (CX, 250), v["title"], font(54), (255, 255, 255, 255), stroke=5)
    items = v["items"]; mx = max(i[1] for i in items)
    x0, maxw = CX - 560, 860
    colors = [rgba("#E8EDF2"), rgba(accent)]
    for i, (name, val, lab) in enumerate(items):
        y = 430 + i * 200
        k = ease((t - 0.3 - i * 0.6) / 1.0)
        draw_text(d, (x0, y - 85), name, font(44), (255, 255, 255, 255), anchor="lm", stroke=5)
        w = max(14, maxw * (val / mx) * k)
        pill(d, (x0, y - 50, x0 + w, y + 50), colors[i % 2], r=20)
        if k > 0.9:
            draw_text(d, (x0 + w + 24, y), lab, font(56), (255, 255, 255, 255), anchor="lm", stroke=6)
    if v.get("note") and t > 1.8:
        kb = back((t - 1.8) / 0.35)
        f = font(50); w = (text_w(d, v["note"], f) + 80) * kb
        pill(d, (CX - w / 2, 820, CX + w / 2, 910), rgba(accent))
        if kb > 0.7:
            d.text((CX, 865), v["note"], font=f, fill=(20, 20, 20, 255), anchor="mm")
    return shadow(lay)


def pin(d, x, y, s, col):
    d.ellipse((x - s, y - s, x + s, y + s), fill=col)
    d.polygon([(x - s * 0.8, y + s * 0.5), (x + s * 0.8, y + s * 0.5), (x, y + s * 2.1)], fill=col)
    d.ellipse((x - s * 0.4, y - s * 0.4, x + s * 0.4, y + s * 0.4), fill=(255, 255, 255, 255))


def place_header(d, place, t, accent):
    k = back(t / 0.35)
    if k <= 0:
        return
    f = font(62)
    w = text_w(d, place, f) + 150
    y = 230 - (1 - k) * 40
    pill(d, (CX - w / 2, y - 55, CX + w / 2, y + 55), (0, 0, 0, 200), outline=rgba(accent), width=5)
    pin(d, CX - w / 2 + 62, y - 14, 20, rgba("#FF3B3B"))
    d.text((CX + 35, y), place, font=f, fill=(255, 255, 255, 255), anchor="mm")


def g_cards(v, t, dur, accent):
    lay = base_layer(); d = ImageDraw.Draw(lay)
    place_header(d, v["place"], t, accent)
    items = v["items"]
    _, ks = staggered(len(items), t, dur, first=0.6)
    f = font(58 if max(len(s) for s in items) < 22 else 48)
    for i, (s, k) in enumerate(zip(items, ks)):
        if k <= 0:
            continue
        y = 420 + i * 150
        w = min(1300, text_w(d, s, f) + 120) * k
        pill(d, (CX - w / 2, y - 58, CX + w / 2, y + 58), (255, 255, 255, 235), r=28)
        d.rectangle((CX - w / 2 + 24, y - 34, CX - w / 2 + 36, y + 34), fill=rgba(accent))
        if k > 0.7:
            d.text((CX + 10, y), s, font=f, fill=(20, 20, 20, 255), anchor="mm")
    return shadow(lay)


def g_nested(v, t, dur, accent):
    lay = base_layer(); d = ImageDraw.Draw(lay)
    place_header(d, v["place"], t, accent)
    items = v["items"]
    _, ks = staggered(len(items), t, dur, first=0.6, gap=1.6)
    boxes = [(1150, 560), (780, 400), (430, 250)]
    cols = [(255, 255, 255, 40), rgba(accent, 120), rgba(accent, 255)]
    cy = 650
    for i, ((big, small), k, (bw, bh)) in enumerate(zip(items, ks, boxes)):
        if k <= 0:
            continue
        bw2, bh2 = bw * k, bh * k
        top = cy + 280 - bh2  # boxes share the bottom edge
        d.rounded_rectangle((CX - bw2 / 2, top, CX + bw2 / 2, cy + 280), 26, fill=cols[i],
                            outline=(255, 255, 255, 230), width=5)
        if k > 0.8:
            ty = top + 70
            draw_text(d, (CX, ty), big, font(72), (255, 255, 255, 255), stroke=6)
            draw_text(d, (CX, ty + 62), small, font(36), (255, 255, 255, 255), stroke=4)
    return shadow(lay)


def g_versus(v, t, dur, accent):
    lay = base_layer(); d = ImageDraw.Draw(lay)
    for side, (key, col) in enumerate((("left", v["lcolor"]), ("right", v["rcolor"]))):
        k = back((t - 0.2 - side * 0.7) / 0.4)
        if k <= 0:
            continue
        cx = CX - 340 + side * 680
        bw, bh = 560 * k, 470 * k
        d.rounded_rectangle((cx - bw / 2, 540 - bh / 2, cx + bw / 2, 540 + bh / 2), 34,
                            fill=(0, 0, 0, 200), outline=rgba(col), width=8)
        if k > 0.8:
            head, body = v[key]
            draw_text(d, (cx, 420), head, font(76), rgba(col), stroke=6)
            f = font(44)
            words = body.split(" "); lines, cur = [], ""
            for w_ in words:
                if text_w(d, (cur + " " + w_).strip(), f) > 500:
                    lines.append(cur); cur = w_
                else:
                    cur = (cur + " " + w_).strip()
            lines.append(cur)
            for j, ln in enumerate(lines):
                d.text((cx, 530 + j * 62), ln, font=f, fill=(255, 255, 255, 255), anchor="mm")
            # tick / cross drawn as shapes
            mx, my = cx, 700
            if side == 0:
                d.line([(mx - 40, my), (mx - 10, my + 32), (mx + 46, my - 30)], fill=rgba(col), width=16, joint="curve")
            else:
                d.line([(mx - 34, my - 34), (mx + 34, my + 34)], fill=rgba(col), width=16)
                d.line([(mx - 34, my + 34), (mx + 34, my - 34)], fill=rgba(col), width=16)
    if t > 0.6:
        d.ellipse((CX - 62, 478, CX + 62, 602), fill=(255, 255, 255, 255))
        d.text((CX, 540), "VS", font=font(60), fill=(20, 20, 20, 255), anchor="mm")
    return shadow(lay)


def g_share(v, t, dur, accent):
    lay = base_layer(); d = ImageDraw.Draw(lay)
    k = ease((t - 0.2) / 1.2)
    cx, cy, r = CX - 300, 520, 230
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, 70))
    if k > 0:
        d.pieslice((cx - r, cy - r, cx + r, cy + r), -90, -90 + 360 * v["pct"] / 100 * k, fill=rgba("#3DDC84"))
    d.ellipse((cx - r * 0.55, cy - r * 0.55, cx + r * 0.55, cy + r * 0.55), fill=(0, 0, 0, 220))
    draw_text(d, (cx, cy), v["big"] if k > 0.95 else f"{int(v['pct'] * k)}%", font(84), (255, 255, 255, 255), stroke=0)
    f = font(54)
    words = v["label"].split(" "); lines, cur = [], ""
    for w_ in words:
        if text_w(d, (cur + " " + w_).strip(), f) > 640:
            lines.append(cur); cur = w_
        else:
            cur = (cur + " " + w_).strip()
    lines.append(cur)
    for j, ln in enumerate(lines):
        draw_text(d, (CX + 40, 420 + j * 72), ln, f, (255, 255, 255, 255), anchor="lm", stroke=6)
    if v.get("note") and t > 2.0:
        kb = back((t - 2.0) / 0.35)
        f2 = font(42); w = (text_w(d, v["note"], f2) + 80) * kb
        pill(d, (CX - w / 2, 820, CX + w / 2, 900), rgba(accent))
        if kb > 0.7:
            d.text((CX, 860), v["note"], font=f2, fill=(20, 20, 20, 255), anchor="mm")
    return shadow(lay)


def arrow(d, x0, x1, y, col, w=26):
    d.line([(x0, y), (x1 - 40, y)], fill=col, width=w)
    d.polygon([(x1 - 50, y - 38), (x1, y), (x1 - 50, y + 38)], fill=col)


def g_evap(v, t, dur, accent):
    lay = base_layer(); d = ImageDraw.Draw(lay)
    red, blue = rgba("#FF5C3B"), rgba("#4DA3FF")
    pad_x0, pad_x1, y0, y1 = CX - 90, CX + 90, 330, 760
    # wet pad
    d.rounded_rectangle((pad_x0, y0, pad_x1, y1), 24, fill=rgba("#2D6CDF", 230), outline=(255, 255, 255, 255), width=6)
    for i in range(9):
        yy = y0 + 30 + i * 48
        d.line([(pad_x0 + 20, yy), (pad_x1 - 20, yy + 26)], fill=(255, 255, 255, 120), width=6)
    draw_text(d, (CX, y1 + 60), "WET PAD", font(46), (255, 255, 255, 255), stroke=5)
    # moving arrows
    for row in range(3):
        y = 420 + row * 130
        off = ((t * 220) + row * 90) % 260
        for j in range(2):
            xs = CX - 700 + off + j * 260
            if xs + 200 < pad_x0 - 20:
                arrow(d, xs, xs + 200, y, red)
        for j in range(2):
            xs = pad_x1 + 40 + off + j * 260
            if xs + 200 < CX + 760:
                arrow(d, xs, xs + 200, y, blue)
    draw_text(d, (CX - 470, 290), "HOT AIR IN", font(52), red, stroke=6)
    draw_text(d, (CX + 470, 290), "COOL AIR OUT", font(52), blue, stroke=6)
    # rising vapour
    for i in range(4):
        ph = (t * 0.8 + i * 0.25) % 1
        x = CX - 45 + i * 30
        y = y0 - 20 - ph * 140
        a = int(200 * (1 - ph))
        d.ellipse((x - 14, y - 14, x + 14, y + 14), fill=(255, 255, 255, a))
    return shadow(lay)


def thumb(d, x, y, s, col):
    d.rounded_rectangle((x - s * 0.9, y - s * 0.1, x - s * 0.45, y + s), int(s * 0.12), fill=col)
    d.rounded_rectangle((x - s * 0.35, y - s * 0.15, x + s * 0.85, y + s), int(s * 0.25), fill=col)
    d.rounded_rectangle((x - s * 0.15, y - s * 0.95, x + s * 0.25, y), int(s * 0.2), fill=col)


def g_cta(v, t, dur, accent):
    lay = base_layer(); d = ImageDraw.Draw(lay)
    kind = v["kind"]
    k = back((t - 0.3) / 0.4)
    if k <= 0:
        return lay
    y = 470
    if kind == "subscribe":
        clicked = t > 2.4
        w, h = 760 * k, 180 * k
        col = (90, 90, 90, 255) if clicked else rgba("#FF0000")
        pill(d, (CX - w / 2, y - h / 2, CX + w / 2, y + h / 2), col)
        if k > 0.8:
            d.text((CX + 30, y), "SUBSCRIBED" if clicked else "SUBSCRIBE", font=font(88),
                   fill=(255, 255, 255, 255), anchor="mm")
            px = CX - w / 2 + 75
            d.polygon([(px - 22, y - 32), (px + 32, y), (px - 22, y + 32)], fill=(255, 255, 255, 255))
        if 1.6 < t < 2.6:  # cursor click
            ph = ease((t - 1.6) / 0.6)
            cx, cy = CX + 520 - 300 * ph, y + 220 - 160 * ph
            d.polygon([(cx, cy), (cx + 26, cy + 70), (cx + 38, cy + 44), (cx + 66, cy + 56)], fill=(255, 255, 255, 255),
                      outline=(0, 0, 0, 255))
        draw_text(d, (CX, y + 180), "IT'S FREE & HELPS A SMALL CREATOR", font(44), (255, 255, 255, 255), stroke=5)
    elif kind == "like":
        r = 130 * k
        d.ellipse((CX - r, y - r, CX + r, y + r), fill=rgba("#4DA3FF"))
        if k > 0.8:
            thumb(d, CX + 5, y, 70, (255, 255, 255, 255))
        draw_text(d, (CX, y + 210), "HIT LIKE!", font(80), (255, 255, 255, 255), stroke=7)
    elif kind == "comment":
        w, h = 900 * k, 230 * k
        d.rounded_rectangle((CX - w / 2, y - h / 2, CX + w / 2, y + h / 2), 50, fill=(255, 255, 255, 245))
        d.polygon([(CX - 200, y + h / 2 - 2), (CX - 120, y + h / 2 - 2), (CX - 230, y + h / 2 + 80)], fill=(255, 255, 255, 245))
        if k > 0.8:
            d.text((CX, y - 32), "YES OR NO?", font=font(80), fill=rgba(accent), anchor="mm")
            d.text((CX, y + 52), "TELL ME IN THE COMMENTS", font=font(50), fill=(30, 30, 30, 255), anchor="mm")
    elif kind == "follow":
        w, h = 860 * k, 170 * k
        pill(d, (CX - w / 2, y - h / 2, CX + w / 2, y + h / 2), (255, 255, 255, 255))
        if k > 0.8:
            d.text((CX, y), "+ FOLLOW FOR MORE", font=font(80), fill=rgba("#FF0000"), anchor="mm")
        draw_text(d, (CX, y + 170), "NEW BOJJI EXPLAINS EVERY WEEK", font(46), (255, 255, 255, 255), stroke=5)
    return shadow(lay)


GRAPHICS = {"headline": g_headline, "tags": g_tags, "counter": g_counter, "range": g_range,
            "bars": g_bars, "cards": g_cards, "nested": g_nested, "versus": g_versus,
            "share": g_share, "evap": g_evap, "cta": g_cta}
ANIM_SECS = {"headline": 0.8, "tags": None, "counter": 2.2, "range": 2.0, "bars": 2.4, "cards": None,
             "nested": None, "versus": 1.6, "share": 2.5, "evap": 1e9, "cta": 2.8}
DIM = {"footage": 0.12, "cta": 0.45}


def anim_secs(v, dur):
    a = ANIM_SECS.get(v["type"])
    if a is None:
        n = len(v.get("items", []))
        gap = min(1.1, max(0.5, (dur * 0.6) / max(n, 1)))
        first = 0.6 if v["type"] in ("cards", "nested") else 0.15
        if v["type"] == "nested":
            gap = 1.6
        a = first + gap * n + 0.5
    return a


# ---------------------------------------------------------------- captions

def caption_chunks(words, max_words=6):
    chunks, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or w[0][-1:] in ".?!" or (w[0][-1:] == "," and len(cur) >= 3):
            chunks.append(cur); cur = []
    if cur:
        chunks.append(cur)
    return [(" ".join(x[0] for x in c), c[0][1], c[-1][2]) for c in chunks]


def caption_layer(text, accent):
    lay = Image.new("RGBA", (1400, 140), (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
    f = font(50)
    words = text.split(" ")
    widths = [text_w(d, w + " ", f) for w in words]
    total = sum(widths)
    while total > 1360 and f.size > 34:
        f = font(f.size - 4); widths = [text_w(d, w + " ", f) for w in words]; total = sum(widths)
    x = 700 - total / 2
    for w, ww in zip(words, widths):
        num = any(c.isdigit() for c in w)
        d.text((x, 70), w, font=f, fill=rgba("#FFD23F") if num else (255, 255, 255, 255), anchor="lm",
               stroke_width=6, stroke_fill=(0, 0, 0, 255))
        x += ww
    return lay


# ---------------------------------------------------------------- footage

def fetch_backgrounds(script, workdir, offline):
    """One or more clips per beat (beats longer than ~8 s get several shots)."""
    used, credits = set(), []
    random.seed(script["id"])
    for pi, part in enumerate(script["parts"]):
        for bi, b in enumerate(part["beats"]):
            dur = b["t1"] - b["t0"]
            n = max(1, min(4, round(dur / 8)))
            b["shots"] = []
            for si in range(n):
                path = os.path.join(workdir, f"bg_{pi}_{bi}_{si}.mp4")
                info = None
                if not offline:
                    try:
                        info = stock.find_clip(b["search"], path, dur / n, used, landscape=True)
                    except Exception as e:
                        print("  footage error:", e)
                if info:
                    credits.append(info)
                    b["shots"].append(info.get("path") if info["kind"] == "photo" else path)
                else:
                    b["shots"].append(None)
    return credits


def gradient(seed):
    rnd = random.Random(seed)
    c1 = np.array([rnd.randint(10, 60), rnd.randint(20, 60), rnd.randint(50, 110)], np.float32)
    c2 = np.array([rnd.randint(120, 200), rnd.randint(60, 120), rnd.randint(20, 60)], np.float32)
    yy = np.linspace(0, 1, H * 2 // 2)[:, None, None]
    img = (c1 * (1 - yy) + c2 * yy).repeat(int(W * 1.15), axis=1).astype(np.uint8)
    return Image.fromarray(img, "RGB")


class BG:
    """Streams raw 1920x1080 RGB frames for one shot."""

    def __init__(self, src, n_frames, seed):
        self.n = n_frames
        self.proc = None
        self.static = None
        self.i = 0
        if src and src.lower().endswith((".jpg", ".jpeg", ".png")):
            self.static = Image.open(src).convert("RGB")
        elif src and os.path.exists(src):
            vf = (f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                  f"eq=saturation=1.08")
            self.proc = subprocess.Popen(
                ["ffmpeg", "-loglevel", "error", "-stream_loop", "-1", "-i", src, "-an", "-vf", vf,
                 "-frames:v", str(n_frames), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                stdout=subprocess.PIPE)
        else:
            self.static = gradient(seed)
        if self.static is not None:  # slow pan over a slightly oversized still
            s = max(W * 1.12 / self.static.width, H * 1.12 / self.static.height)
            self.static = self.static.resize((int(self.static.width * s) + 1, int(self.static.height * s) + 1))
        self.last = None

    def next(self):
        self.i += 1
        if self.proc:
            raw = self.proc.stdout.read(W * H * 3)
            if len(raw) == W * H * 3:
                self.last = np.frombuffer(raw, np.uint8).reshape(H, W, 3)
            return self.last if self.last is not None else np.zeros((H, W, 3), np.uint8)
        p = self.i / max(self.n, 1)
        ex, ey = self.static.width - W, self.static.height - H
        x, y = int(ex * (0.15 + 0.7 * p)), int(ey * 0.5)
        return np.asarray(self.static.crop((x, y, x + W, y + H)))

    def close(self):
        if self.proc:
            self.proc.stdout.close(); self.proc.wait()


# ---------------------------------------------------------------- timeline

def build_timeline(script, workdir):
    """Global list of segments, voice track and SFX cues."""
    segs, voice, cues = [], [], []
    t = 0.0
    for pi, part in enumerate(script["parts"]):
        if pi == 1:
            segs.append({"kind": "title", "t0": t, "t1": t + TITLE_SECS})
            cues += [("whoosh", t), ("boom", t + 0.15)]
            voice.append(("silence", TITLE_SECS)); t += TITLE_SECS
        if part.get("chapter"):
            first = part["beats"][0]
            segs.append({"kind": "chapter", "t0": t, "t1": t + CHAPTER_SECS, "text": part["chapter"],
                         "bg_beat": first})
            cues.append(("whoosh", t))
            voice.append(("silence", CHAPTER_SECS)); t += CHAPTER_SECS
        for b in part["beats"]:
            segs.append({"kind": "beat", "t0": t + b["t0"], "t1": t + b["t1"], "beat": b})
            if b.get("sfx"):
                cues.append((b["sfx"], t + b["t0"] + 0.1))
                if b["sfx"] == "boom":
                    cues.append(("ding", t + b["t0"] + 0.45))
        voice.append(("wav", part["wav"], t))
        t += part["dur"]
    end = script.get("end_screen_secs", 20)
    segs.append({"kind": "end", "t0": t, "t1": t + end})
    cues.append(("pop", t + 0.2))
    return segs, voice, cues, t + end


def mix_audio(voice, cues, total, out_wav):
    mix = np.zeros(int(total * A.SR) + A.SR, np.float32)
    for v in voice:
        if v[0] == "wav":
            x = A.read_wav(v[1]); i = int(v[2] * A.SR)
            n = min(len(x), len(mix) - i)
            if n > 0:
                mix[i:i + n] += x[:n]
    for name, at in cues:
        p = os.path.join(SFX, name + ".wav")
        if not os.path.exists(p):
            continue
        tmp = out_wav + f".{name}.wav"
        if not os.path.exists(tmp):
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", p, "-ac", "1", "-ar", str(A.SR), tmp], check=True)
        x = A.read_wav(tmp) * SFX_GAIN.get(name, 0.35)
        i = int(at * A.SR)
        n = min(len(x), len(mix) - i)
        if n > 0:
            mix[i:i + n] += x[:n]
    raw = out_wav + ".raw.wav"
    A.write_wav(raw, mix[: int(total * A.SR)])
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw, "-af", "loudnorm=I=-14:TP=-1.5:LRA=9",
                    "-ar", "48000", out_wav], check=True)


# ---------------------------------------------------------------- frames

def darken(arr, amount):
    if amount <= 0:
        return arr
    return (arr.astype(np.uint16) * int(256 * (1 - amount)) >> 8).astype(np.uint8)


def title_card(script, t, accent, bojji):
    img = gradient(script["id"] + "title").crop((0, 0, W, H)).convert("RGBA")
    d = ImageDraw.Draw(img)
    k = back(t / 0.45)
    draw_text(d, (CX, 300 - (1 - k) * 50), script.get("series", "BOJJI EXPLAINS"), font(64), rgba(accent), stroke=6)
    lines = script["title_lines"]
    for i, ln in enumerate(lines):
        kk = back((t - 0.2 - 0.15 * i) / 0.4)
        if kk <= 0:
            continue
        f = font(104)
        words = ln.split(" "); widths = [text_w(d, w + " ", f) for w in words]
        x = CX - sum(widths) / 2
        for w, ww in zip(words, widths):
            col = rgba(accent) if w.strip("?!") in ("DESERT",) else (255, 255, 255, 255)
            d.text((x, 470 + i * 125 + (1 - kk) * 60), w, font=f, fill=col, anchor="lm", stroke_width=9, stroke_fill=(0, 0, 0, 255))
            x += ww
    bojji.place(img, "point", t, TITLE_SECS)
    return img


def end_card(script, t, accent, bojji):
    img = gradient(script["id"] + "end").crop((0, 0, W, H)).convert("RGBA")
    d = ImageDraw.Draw(img)
    draw_text(d, (W / 2, 150), "THANKS FOR WATCHING!", font(84), (255, 255, 255, 255), stroke=8)
    draw_text(d, (W / 2, 250), "WATCH NEXT · SUBSCRIBE FOR A NEW EXPLAINER EVERY WEEK", font(40), rgba(accent), stroke=5)
    # Bojji walks to the centre, then waves (YouTube end-screen elements go left/right)
    floor = H - 18
    walk_t = 2.6
    if t < walk_t:
        n = int(t * 8) % 4
        im = bojji.full(f"walk_{n + 1:02d}")
        x = int(-im.width + (W / 2 - im.width / 2 + im.width) * ease(t / walk_t))
        img.alpha_composite(im, (x, floor - im.height - (6 if n in (1, 3) else 0)))
    else:
        im = bojji.full("wave_01" if int(t * 4) % 2 == 0 else "wave_02")
        img.alpha_composite(im, (int(W / 2 - im.width / 2), floor - im.height))
    return img


def chapter_card(seg, t, accent, bg_arr):
    img = Image.fromarray(darken(bg_arr, 0.7)).convert("RGBA")
    d = ImageDraw.Draw(img)
    head, _, rest = seg["text"].partition(":")
    k = back(t / 0.4)
    draw_text(d, (W / 2, 430 - (1 - k) * 60), head, font(76), rgba(accent), stroke=7)
    kk = back((t - 0.15) / 0.4)
    if kk > 0:
        draw_text(d, (W / 2, 560 + (1 - kk) * 60), rest.strip(), font(104), (255, 255, 255, 255), stroke=9)
    w = 900 * ease((t - 0.3) / 0.8)
    d.rectangle((W / 2 - w / 2, 650, W / 2 + w / 2, 662), fill=rgba(accent))
    return img


def source_tag(img, text):
    d = ImageDraw.Draw(img)
    f = font(30)
    s = "Source: " + text
    w = text_w(d, s, f) + 44
    pill(d, (W - 40 - w, 36, W - 40, 88), (0, 0, 0, 170))
    d.text((W - 40 - w / 2, 62), s, font=f, fill=(255, 255, 255, 235), anchor="mm")


def series_bug(img, accent):
    d = ImageDraw.Draw(img)
    d.text((44, 62), "BOJJI", font=font(34), fill=rgba(accent), anchor="lm", stroke_width=4, stroke_fill=(0, 0, 0, 255))
    d.text((44 + text_w(d, "BOJJI ", font(34)), 62), "EXPLAINS", font=font(34), fill=(255, 255, 255, 255),
           anchor="lm", stroke_width=4, stroke_fill=(0, 0, 0, 255))


def render(script, workdir, out_path, preview=None, frames_dir=None):
    accent = script.get("accent", "#FF9F1C")
    bojji = Bojji()
    segs, voice, cues, total = build_timeline(script, workdir)
    if preview:
        total = min(total, preview)
    audio = os.path.join(workdir, "mix.wav")
    mix_audio(voice, cues, total, audio)
    n_total = int(total * FPS)
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
         "-r", str(FPS), "-i", "-", "-i", audio, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", out_path],
        stdin=subprocess.PIPE)
    snap_at = {int(s["t0"] * FPS) + int(1.5 * FPS) for s in segs}  # preview stills
    seg_i = 0
    shot = None
    shot_key = None
    cache = {}
    cap_cache = {}
    for fi in range(n_total):
        t = fi / FPS
        while seg_i + 1 < len(segs) and t >= segs[seg_i]["t1"]:
            seg_i += 1
        seg = segs[seg_i]
        lt = t - seg["t0"]
        dur = seg["t1"] - seg["t0"]
        if seg["kind"] == "title":
            img = title_card(script, lt, accent, bojji)
        elif seg["kind"] == "end":
            img = end_card(script, lt, accent, bojji)
        else:
            b = seg["beat"] if seg["kind"] == "beat" else seg["bg_beat"]
            shots = b.get("shots") or [None]
            if seg["kind"] == "chapter":
                si, sdur, key = 0, dur, ("ch", id(seg))
            else:
                si = min(len(shots) - 1, int(lt / (dur / len(shots))))
                sdur, key = dur / len(shots), (id(b), si)
            if key != shot_key:
                if shot:
                    shot.close()
                shot = BG(shots[si], int(sdur * FPS) + 2, f"{script['id']}{key}")
                shot_key = key
            arr = shot.next()
            if seg["kind"] == "chapter":
                img = chapter_card(seg, lt, accent, arr)
            else:
                v = b["visual"]
                img = Image.fromarray(darken(arr, DIM.get(v["type"], 0.55))).convert("RGBA")
                series_bug(img, accent)
                if v["type"] == "footage":
                    if v.get("label"):
                        img.alpha_composite(g_label(v["label"], lt, accent))
                else:
                    an = anim_secs(v, dur)
                    if lt < an:
                        lay = GRAPHICS[v["type"]](v, lt, dur, accent)
                    else:
                        ck = (id(b), "done")
                        if ck not in cache:
                            cache.clear()
                            cache[ck] = GRAPHICS[v["type"]](v, an + 0.01, dur, accent)
                        lay = cache[ck]
                    img.alpha_composite(lay)
                if b.get("source"):
                    source_tag(img, b["source"])
                bojji.place(img, b.get("bojji"), lt, dur)
                # caption
                part_t = lt + b["t0"]
                for text, c0, c1 in b.setdefault("_chunks", caption_chunks(b["words"])):
                    if c0 - 0.05 <= part_t < c1 + 0.25:
                        if text not in cap_cache:
                            if len(cap_cache) > 50:
                                cap_cache.clear()
                            cap_cache[text] = caption_layer(text, accent)
                        img.alpha_composite(cap_cache[text], (CX - 700, H - 175))
                        break
        frame = np.asarray(img.convert("RGB"))
        enc.stdin.write(frame.tobytes())
        if frames_dir and fi in snap_at:
            img.convert("RGB").save(os.path.join(frames_dir, f"f{fi:06d}.jpg"), quality=80)
        if fi % (FPS * 30) == 0:
            print(f"  {t:6.1f}s / {total:.0f}s", flush=True)
    if shot:
        shot.close()
    enc.stdin.close(); enc.wait()
    return total


def thumbnail(script, workdir, out_png):
    th = script["thumbnail"]
    src = None
    for part in script["parts"]:
        for b in part["beats"]:
            for s in b.get("shots") or []:
                if s and b["search"] == th.get("search"):
                    src = s; break
    img = None
    if src and src.endswith(".mp4"):
        still = os.path.join(workdir, "thumb_bg.jpg")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", src, "-frames:v", "1", still])
        if os.path.exists(still):
            img = Image.open(still).convert("RGB")
    elif src:
        img = Image.open(src).convert("RGB")
    if img is None:
        img = gradient(script["id"] + "thumb")
    s = max(1280 / img.width, 720 / img.height)
    img = img.resize((int(img.width * s) + 1, int(img.height * s) + 1))
    img = img.crop((0, 0, 1280, 720)).convert("RGBA")
    img = Image.fromarray(darken(np.asarray(img.convert("RGB")), 0.25)).convert("RGBA")
    d = ImageDraw.Draw(img)
    for i, (ln, col) in enumerate(zip(th["text"], th["colors"])):
        draw_text(d, (40, 200 + i * 190), ln, font(170), rgba(col), anchor="lm", stroke=12)
    bust = Image.open(os.path.join(EMO, th.get("pose", "shocked") + ".png")).convert("RGBA")
    bust = bust.resize((int(bust.width * 640 / bust.height), 640), Image.LANCZOS)
    img.alpha_composite(shadow(Image.new("RGBA", img.size)), (0, 0))
    img.alpha_composite(bust, (1280 - bust.width + 30, 720 - bust.height + 60))
    img.convert("RGB").save(out_png, quality=92)


def upload_sheet(script, total, segs_starts, credits, out_md):
    chapters = "\n".join(f"{int(t // 60)}:{int(t % 60):02d} {name}" for name, t in segs_starts)
    src = "\n".join(f"- {n}: {u}" for n, u in script["sources"])
    names = sorted({c.get("by", "") for c in credits if c})
    sites = sorted({c.get("site", "") for c in credits if c})
    footage = f"Footage and images: {', '.join(sites)} ({', '.join(names[:12])})" if names else ""
    desc = (f"{script['description']}\n\nChapters\n{chapters}\n\nSources\n{src}\n\n{footage}\n\n"
            f"New Bojji Explains every week. Subscribe so you don't miss the next one!\n\n#bojjiexplains #datacenters #ai")
    md = (f"# Bojji Explains — {script['title']}\n\nLength: {int(total // 60)}:{int(total % 60):02d}\n\n"
          f"**Title**\n```\n{script['title']}\n```\n**Description**\n```\n{desc}\n```\n"
          f"**Tags**\n```\n{', '.join(script['tags'])}\n```\n"
          "Settings: Not made for kids · Altered or synthetic content: **No** (your real voice; cartoon host is "
          "clearly animated) · Category: Education · Thumbnail: upload thumbnail.jpg · "
          "End screen: add a video + subscribe element for the last 20 s.\n")
    open(out_md, "w").write(md)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--preview", type=float, default=None, help="only render the first N seconds")
    ap.add_argument("--frames", action="store_true", help="save a still from every segment")
    ap.add_argument("--timing-only", action="store_true", help="only time the voice and save timing.json")
    a = ap.parse_args()
    folder = os.path.abspath(a.folder)
    script = json.load(open(os.path.join(folder, "script.json")))
    out_dir = os.path.join(ROOT, "out", "explainer")
    work = os.path.join(out_dir, "work")
    os.makedirs(work, exist_ok=True)
    frames = os.path.join(out_dir, "frames") if a.frames else None
    if frames:
        shutil.rmtree(frames, ignore_errors=True); os.makedirs(frames)
    print("1/4 cleaning and timing the voice")
    saved_path = os.path.join(folder, "timing.json")
    saved = json.load(open(saved_path)) if os.path.exists(saved_path) and not a.timing_only else None
    if saved and not saved.get("method", "").startswith("whisper"):
        saved = None
    for pi, p in enumerate(script["parts"]):
        wav = os.path.join(work, p["audio"] + ".wav")
        src = os.path.join(folder, "audio", p["audio"] + ".m4a")
        if not os.path.exists(src):
            src = next(os.path.join(folder, "audio", f) for f in os.listdir(os.path.join(folder, "audio"))
                       if f.startswith(p["audio"]))
        samples = A.clean(src, wav)
        if saved:
            cuts = saved["parts"][pi].get("cuts", [])
            if cuts:
                samples = A.apply_cuts(samples, cuts)
                A.write_wav(wav, samples)
                print(f"  {p['audio']}: removed {len(cuts)} retake(s), {sum(c['end'] - c['start'] for c in cuts):.1f}s")
            sh = lambda t: A.shift_time(t, cuts)
            for b, sb in zip(p["beats"], saved["parts"][pi]["beats"]):
                b["t0"], b["t1"] = sh(sb["t0"]), sh(sb["t1"])
                b["words"] = [(w, sh(s0), sh(s1)) for w, s0, s1 in sb["words"]]
            p["beats"][-1]["t1"] = len(samples) / A.SR
        else:
            A.align(wav, samples, p["beats"])
        p["wav"], p["dur"] = wav, len(samples) / A.SR
        print(f"  {p['audio']}: {p['dur']:.1f}s, beats at {[round(b['t0'], 1) for b in p['beats']]}")
    if a.timing_only:
        method = "whisper" if A.LOG and not any(l.startswith("pauses") for l in A.LOG) else "pauses"
        json.dump({"method": method, "log": A.LOG,
                   "parts": [{"audio": p["audio"], "cuts": A.CUTS.get(p["wav"], []),
                              "heard": A.HEARD.get(p["wav"], []),
                              "beats": [{"t0": b["t0"], "t1": b["t1"], "words": b["words"]}
                                        for b in p["beats"]]} for p in script["parts"]]},
                  open(saved_path, "w"), indent=0, default=float)
        print("timing saved:", method, A.LOG)
        return
    print("2/4 footage")
    credits = fetch_backgrounds(script, work, a.offline)
    print("3/4 rendering")
    out_mp4 = os.path.join(out_dir, f"{script['id']}.mp4")
    total = render(script, work, out_mp4, a.preview, frames)
    print("4/4 thumbnail + upload sheet")
    thumbnail(script, work, os.path.join(out_dir, "thumbnail.jpg"))
    segs, _, _, _ = build_timeline(script, work)
    starts = [("Intro", 0.0)] + [(s["text"].split(":", 1)[1].strip().title(), s["t0"]) for s in segs if s["kind"] == "chapter"]
    upload_sheet(script, total, starts, credits, os.path.join(out_dir, "upload_sheet.md"))
    json.dump({"parts": [{"audio": p["audio"], "beats": [{"t0": b["t0"], "t1": b["t1"]} for b in p["beats"]]}
                         for p in script["parts"]]}, open(os.path.join(out_dir, "timing.json"), "w"), indent=1)
    print("done:", out_mp4, f"{total:.1f}s")


if __name__ == "__main__":
    main()
