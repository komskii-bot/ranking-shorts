"""Assembles a 1080x1920 ranking Short with FFmpeg + Pillow (no editor needed)."""
import json
import os
import random
import subprocess

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1080, 1920, 30
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = os.path.join(ROOT, "assets", "fonts", "Poppins-Bold.ttf")
WHITE = (255, 255, 255, 255)


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def duration(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "default=nw=1:nk=1", path])
    return float(out.strip())


def has_audio(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "a",
                                   "-show_entries", "stream=index", "-of", "csv=p=0", path])
    return bool(out.strip())


def hex_rgba(h, a=255):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a)


def font(size):
    return ImageFont.truetype(FONT, size)


def wrap(draw, text, fnt, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=fnt) <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit_text(draw, text, max_w, start, min_size, max_lines):
    size = start
    while size > min_size:
        f = font(size)
        lines = wrap(draw, text, f, max_w)
        if len(lines) <= max_lines and all(draw.textlength(l, font=f) <= max_w for l in lines):
            return f, lines
        size -= 4
    f = font(min_size)
    return f, wrap(draw, text, f, max_w)[:max_lines]


def centered(draw, lines, fnt, y, fill, stroke=6, gap=1.08):
    for line in lines:
        x = (W - draw.textlength(line, font=fnt)) / 2
        draw.text((x, y), line, font=fnt, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
        y += int(fnt.size * gap)
    return y


def shadowed(layer, blur=14):
    """Soft drop shadow under everything drawn on `layer`."""
    alpha = layer.split()[3]
    shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(blur)).point(lambda a: int(a * 0.7)))
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    out.alpha_composite(shadow, (0, 8))
    out.alpha_composite(layer)
    return out


HIGHLIGHT = ["#FF3B3B", "#1DE9D6", "#FF9F1C", "#FFD23F", "#C86BFF"]
RANK_COLORS = {1: "#FFD23F", 2: "#E8EDF2", 3: "#FF9F1C", 4: "#1DE9D6", 5: "#FF5C8A"}
STOP = {"ON", "IN", "OF", "THE", "TO", "FOR", "AT", "BY", "WITH", "THAT", "WHERE", "WHO",
        "RIGHT", "NOW", "EVER", "AND", "A", "AN", "IS", "ARE", "#SHORTS"}


def title_colors(title, highlights=None, palette=None):
    """Return [(word, color_or_None)] — key phrases get flashy colours, numbers yellow."""
    words = title.upper().replace("#SHORTS", "").split()
    colors = [None] * len(words)
    phrases = [h.upper().split() for h in (highlights or []) if h.strip()]
    if not phrases:  # heuristic: the superlative + the subject that follows it
        for i, w in enumerate(words):
            if (w.endswith("EST") and len(w) > 4) or w in ("MOST", "BEST", "WORST", "LEAST"):
                j = i + 1
                while j < len(words) and words[j] not in STOP and not words[j].isdigit():
                    j += 1
                phrases = [[w]] + ([words[i + 1:j]] if j > i + 1 else [])
                break
        if not phrases and len(words) > 2:
            phrases = [words[-2:]]
    pal = [c for c in (palette or []) if c] or HIGHLIGHT
    for k, ph in enumerate(phrases):
        n = len(ph)
        for i in range(len(words) - n + 1):
            if [x.strip(",.!?:") for x in words[i:i + n]] == [x.strip(",.!?:") for x in ph]:
                for j in range(i, i + n):
                    colors[j] = pal[k % len(pal)]
                break
    for i, w in enumerate(words):
        if colors[i] is None and any(ch.isdigit() for ch in w):
            colors[i] = HIGHLIGHT[3]
    return list(zip(words, colors))


def rich_title(draw, tokens, y, start, min_size, max_lines, max_w=920, stroke=8):
    """Centered, word-wrapped title where each word can have its own colour."""
    text = " ".join(w for w, _ in tokens)
    f, lines = fit_text(draw, text, max_w, start, min_size, max_lines)
    k = 0
    for line in lines:
        n = len(line.split())
        x = (W - draw.textlength(line, font=f)) / 2
        for w, c in tokens[k:k + n]:
            draw.text((x, y), w, font=f, fill=hex_rgba(c) if c else WHITE,
                      stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
            x += draw.textlength(w + " ", font=f)
        k += n
        y += int(f.size * 1.08)
    return y


HOOK_STYLES = ("badge", "banners", "guess", "bignumber", "frame")


def hook_style(script):
    """Opening frame look (it doubles as the Short's thumbnail): varies per video, stable on re-render."""
    import random as _r
    return script.get("hook_style") or _r.Random(script["id"] + "hookstyle").choice(HOOK_STYLES)


def _title_tokens_no_top(tokens):
    """Drop a leading 'Top 10' from the title: the frame shows it as a graphic instead."""
    t = list(tokens)
    if len(t) >= 2 and t[0][0].lower() == "top" and t[1][0].isdigit():
        t = t[2:]
    return t


def hook_frame(img, d, script, tokens, acc):
    n = len(script.get("items", [])) or 10
    label = f"TOP {n}"
    style = hook_style(script)
    body = _title_tokens_no_top(tokens)
    red, yel, teal = hex_rgba("#FF3B3B"), hex_rgba("#FFD23F"), hex_rgba("#1DE9D6")
    if style == "badge":
        # tilted starburst badge with TOP 10, title underneath
        b = Image.new("RGBA", (440, 440), (0, 0, 0, 0)); bd = ImageDraw.Draw(b)
        pts = []
        import math
        for k in range(32):
            r = 212 if k % 2 == 0 else 174
            a = math.pi * 2 * k / 32
            pts.append((220 + r * math.cos(a), 220 + r * math.sin(a)))
        bd.polygon(pts, fill=red, outline=(255, 255, 255, 255))
        bd.text((220, 172), "TOP", font=font(82), fill=WHITE, anchor="mm", stroke_width=6, stroke_fill=(0, 0, 0, 255))
        bd.text((220, 268), str(n), font=font(146), fill=yel, anchor="mm", stroke_width=8, stroke_fill=(0, 0, 0, 255))
        b = b.rotate(-12, resample=Image.BICUBIC, expand=True)
        img.alpha_composite(b, ((W - b.width) // 2, 130))
        rich_title(d, body, 610, 100, 60, 3)
    elif style == "banners":
        # each title line on its own slanted colour strip
        d.rounded_rectangle([60, 210, 420, 330], 24, fill=yel)
        d.text((240, 270), label, font=font(84), fill=(15, 15, 15, 255), anchor="mm")
        text = " ".join(w for w, _ in body).upper()
        f, lines = fit_text(d, text, 860, 96, 60, 4)
        colours = [red, hex_rgba("#4DA3FF"), hex_rgba("#FF9F1C"), teal]
        y = 380
        for i, line in enumerate(lines):
            bb = d.textbbox((0, 0), line, font=f, stroke_width=6)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            strip = Image.new("RGBA", (int(tw) + 100, int(th + f.size * 0.45)), colours[i % 4])
            sd = ImageDraw.Draw(strip)
            sd.text((strip.width / 2, strip.height / 2), line, font=f, fill=WHITE, anchor="mm",
                    stroke_width=6, stroke_fill=(0, 0, 0, 255))
            strip = strip.rotate(3 if i % 2 else -3, resample=Image.BICUBIC, expand=True)
            img.alpha_composite(strip, (int((W - strip.width) / 2), int(y)))
            y += strip.height * 0.9
    elif style == "guess":
        d.rounded_rectangle([(W - 330) / 2, 220, (W + 330) / 2, 320], 26, fill=red)
        d.text((W / 2, 270), label, font=font(76), fill=WHITE, anchor="mm")
        yb = rich_title(d, body, 360, 100, 60, 3)
        cy = yb + 95
        d.ellipse([W / 2 - 115, cy - 95, W / 2 + 115, cy + 95], fill=yel, outline=(0, 0, 0, 255), width=9)
        d.text((W / 2, cy), "#1?", font=font(110), fill=(15, 15, 15, 255), anchor="mm")
        d.text((W / 2, cy + 140), "CAN YOU GUESS IT?", font=font(54), fill=WHITE, anchor="mm",
               stroke_width=6, stroke_fill=(0, 0, 0, 255))
    elif style == "bignumber":
        # huge translucent number behind the title
        big = Image.new("RGBA", (W, H), (0, 0, 0, 0)); bg = ImageDraw.Draw(big)
        bg.text((W / 2, 560), str(n), font=font(760), fill=acc[:3] + (110,), anchor="mm",
                stroke_width=14, stroke_fill=(255, 255, 255, 170))
        img.alpha_composite(big)
        d.text((W / 2, 250), "TOP", font=font(90), fill=WHITE, anchor="mm", stroke_width=8, stroke_fill=(0, 0, 0, 255))
        rich_title(d, body, 470, 112, 64, 3)
    else:  # frame
        fr = Image.new("RGBA", (W, H), (0, 0, 0, 0)); fd = ImageDraw.Draw(fr)
        fd.rectangle([0, 0, W - 1, H - 1], outline=acc, width=26)
        fd.rectangle([26, 26, W - 27, H - 27], outline=(255, 255, 255, 200), width=6)
        img.alpha_composite(fr)
        rib = Image.new("RGBA", (W, 150), acc); rd = ImageDraw.Draw(rib)
        rd.text((W / 2, 75), f"{label} · RANKED", font=font(84), fill=(15, 15, 15, 255), anchor="mm")
        img.alpha_composite(rib, (0, 190))
        yb = rich_title(d, body, 400, 110, 64, 3)
        src = (script.get("sources") or [""])[0]
        if src:
            src = src.split("(")[0].split(" - ")[0].strip()
            if src.startswith("http"):
                src = ""
        if src:
            tag = f"DATA: {src.upper()}"[:34]
            fs = font(42)
            tw = d.textlength(tag, font=fs)
            d.rounded_rectangle([(W - tw) / 2 - 30, yb + 20, (W + tw) / 2 + 30, yb + 90], 30, fill=(0, 0, 0, 200))
            d.text((W / 2, yb + 55), tag, font=fs, fill=WHITE, anchor="mm")


LIST_TOP, ROW_H = 300, 118


def list_geom(n):
    """(top, row height, number font, max name font) for a 5- or 10-item list."""
    return (300, 118, 76, 72) if n <= 5 else (290, 92, 60, 56)
OPT_TOP, OPT_H, OPT_GAP = 640, 118, 22
LETTERS = "ABCD"
GREEN = "#3DDC84"


def quiz_layer(img, d, seg, acc):
    """Question n/5, the question, 4 options; on reveal the right one turns green + fact and source note."""
    q, n, total = seg["question"], seg["n"], seg["total"]
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 95))
    img.alpha_composite(shade)
    pf = font(46)
    label = f"QUESTION {n}/{total}"
    tw = d.textlength(label, font=pf)
    d.rounded_rectangle([(W - tw) / 2 - 30, 140, (W + tw) / 2 + 30, 215], 36, fill=acc)
    d.text(((W - tw) / 2, 148), label, font=pf, fill=(15, 15, 15, 255))
    f, lines = fit_text(d, q["q"], 960, 76, 46, 4)
    y = 250 + (4 - len(lines)) * f.size * 0.28
    centered(d, lines, f, y, WHITE, stroke=7)
    reveal = seg["kind"] == "q_reveal"
    for i, opt in enumerate(q["options"]):
        y = OPT_TOP + i * (OPT_H + OPT_GAP)
        right = i == q["answer"]
        if reveal and right:
            fill, txt, out = hex_rgba(GREEN), (12, 12, 12, 255), (255, 255, 255, 255)
        elif reveal:
            fill, txt, out = (0, 0, 0, 150), (255, 255, 255, 120), (255, 255, 255, 70)
        else:
            fill, txt, out = (0, 0, 0, 175), WHITE, (255, 255, 255, 230)
        d.rounded_rectangle([50, y, W - 50, y + OPT_H], 34, fill=fill, outline=out, width=4)
        cx, cy = 115, y + OPT_H / 2
        circ = hex_rgba(HIGHLIGHT[i % len(HIGHLIGHT)]) if not reveal or right else (120, 120, 120, 160)
        if reveal and right:
            circ = (255, 255, 255, 255)
        d.ellipse([cx - 40, cy - 40, cx + 40, cy + 40], fill=circ)
        lf = font(48)
        lw = d.textlength(LETTERS[i], font=lf)
        d.text((cx - lw / 2, cy - 33), LETTERS[i], font=lf, fill=(15, 15, 15, 255))
        size = 56
        while size > 34 and d.textlength(opt, font=font(size)) > W - 50 - 185:
            size -= 2
        of = font(size)
        d.text((180, cy - size * 0.62), opt, font=of, fill=txt,
               stroke_width=0 if (reveal and right) else 4, stroke_fill=(0, 0, 0, 255))
        if reveal and right:  # check mark
            x0, y0 = W - 120, cy
            d.line([(x0, y0), (x0 + 18, y0 + 20), (x0 + 52, y0 - 24)], fill=(12, 12, 12, 255), width=11,
                   joint="curve")
    if reveal:
        yb = OPT_TOP + 4 * (OPT_H + OPT_GAP) + 10
        ef, el = fit_text(d, q.get("explain", ""), 610, 44, 32, 3)
        for line in el:
            d.text((60, yb), line, font=ef, fill=WHITE, stroke_width=5, stroke_fill=(0, 0, 0, 255))
            yb += int(ef.size * 1.15)
        if q.get("note"):
            nf, nl = fit_text(d, q["note"], 610, 30, 24, 3)
            yb += 8
            for line in nl:
                d.text((60, yb), line, font=nf, fill=(225, 225, 225, 255), stroke_width=4,
                       stroke_fill=(0, 0, 0, 255))
                yb += int(nf.size * 1.2)


def follow_badge(workdir, idx, kind, y=None, label="FOLLOW FOR MORE"):
    """Small 'FOLLOW FOR MORE' pill shown while the mid-video follow ask is spoken."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = font(50)
    t = label
    tw = d.textlength(t, font=f)
    y = y or (1470 if kind.startswith("q_") else 1262)
    x0 = 50  # left side, clear of the mascot on the right
    d.rounded_rectangle([x0, y, x0 + tw + 130, y + 86], 43, fill=(255, 255, 255, 255),
                        outline=(255, 0, 0, 255), width=5)
    cx, cy = x0 + 45, y + 43  # plus sign
    d.line([(cx - 14, cy), (cx + 14, cy)], fill=(255, 0, 0, 255), width=8)
    d.line([(cx, cy - 14), (cx, cy + 14)], fill=(255, 0, 0, 255), width=8)
    d.text((x0 + 82, y + 12), t, font=f, fill=(230, 0, 0, 255))
    p = os.path.join(workdir, f"s{idx}_follow.png")
    img.save(p)
    return p


def countdown_pngs(accent, workdir, idx, secs=3):
    """One PNG per second: big number in a ring, shown left of the mascot."""
    out = []
    cx, cy, r = 250, OPT_TOP + 4 * (OPT_H + OPT_GAP) + 130, 105
    for k in range(secs):
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([cx - r - 10, cy - r - 10, cx + r + 10, cy + r + 10], fill=(0, 0, 0, 170))
        frac = (secs - k) / secs
        d.arc([cx - r, cy - r, cx + r, cy + r], -90, -90 + 360 * frac, fill=hex_rgba(accent), width=18)
        t = str(secs - k)
        f = font(130)
        tw = d.textlength(t, font=f)
        d.text((cx - tw / 2, cy - 92), t, font=f, fill=WHITE, stroke_width=6, stroke_fill=(0, 0, 0, 255))
        p = os.path.join(workdir, f"s{idx}_cd{k}.png")
        img.save(p)
        out.append((p, float(k), float(k + 1)))
    return out


def static_layer(script, seg, accent, path):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(H):
        if y < 1150:
            a = int(150 * (1 - y / 1150) ** 1.1) + 25
        elif 1300 <= y < 1600:
            a = int(110 * (1 - abs(y - 1450) / 150))
        else:
            a = 0
        if a > 0:
            gd.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    img.alpha_composite(grad)

    txt = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt)
    acc = hex_rgba(accent)
    tokens = title_colors(script["title"], script.get("highlight"), script.get("highlight_colors"))

    if seg["kind"] in ("q_ask", "q_timer", "q_reveal"):
        quiz_layer(img, d, seg, acc)
    elif seg["kind"] == "q_hook":
        tag = font(70)
        label = "QUIZ TIME"
        tw = d.textlength(label, font=tag)
        d.rounded_rectangle([(W - tw) / 2 - 34, 250, (W + tw) / 2 + 34, 350], 26, fill=hex_rgba(HIGHLIGHT[0]))
        d.text(((W - tw) / 2, 256), label, font=tag, fill=WHITE)
        rich_title(d, tokens, 395, 110, 64, 4)
    elif seg["kind"] == "hook":
        hook_frame(img, d, script, tokens, acc)
    elif seg["kind"] in ("outro", "q_outro"):
        f = font(118)
        parts = ([("HOW MANY", WHITE), ("DID YOU GET?", hex_rgba(HIGHLIGHT[3]))] if seg["kind"] == "q_outro"
                 else [("WHAT'S YOUR", WHITE), ("#1?", hex_rgba(HIGHLIGHT[3]))])
        f = font(118) if seg["kind"] == "outro" else font(104)
        y = 250
        for t, c in parts:
            x = (W - d.textlength(t, font=f)) / 2
            d.text((x, y), t, font=f, fill=c, stroke_width=9, stroke_fill=(0, 0, 0, 255))
            y += 130
        f2 = font(64)
        t = "COMMENT YOUR SCORE" if seg["kind"] == "q_outro" else "COMMENT BELOW"
        d.text(((W - d.textlength(t, font=f2)) / 2, y + 10), t, font=f2, fill=hex_rgba(HIGHLIGHT[1]),
               stroke_width=7, stroke_fill=(0, 0, 0, 255))
        # subscribe button
        f3 = font(62)
        t = "SUBSCRIBE"
        tw = d.textlength(t, font=f3)
        bx, by = (W - tw - 150) / 2, y + 110
        d.rounded_rectangle([bx, by, bx + tw + 150, by + 104], 52, fill=(255, 0, 0, 255),
                            outline=(255, 255, 255, 255), width=5)
        cx, cy = bx + 52, by + 52  # bell-ish play icon
        d.polygon([(cx - 14, cy - 20), (cx - 14, cy + 20), (cx + 20, cy)], fill=WHITE)
        d.text((bx + 95, by + 12), t, font=f3, fill=WHITE)
    else:
        item = seg["item"]
        rich_title(d, tokens, 150, 50, 34, 2, max_w=960, stroke=5)
        # big ranking list: #1 at the top, names revealed as the countdown reaches them
        n_items = len(script["items"])
        top, rh, nfs, maxs = list_geom(n_items)
        panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(panel).rounded_rectangle([30, top - 16, W - 30, top + n_items * rh + 4], 36,
                                                fill=(0, 0, 0, 120))
        img.alpha_composite(panel)
        numw = 105 if n_items <= 5 else 130   # room for "10."
        for it in sorted(script["items"], key=lambda i: i["rank"]):
            r = it["rank"]
            y = top + (r - 1) * rh
            current = r == item["rank"]
            shown = r >= item["rank"]
            if current:
                d.rounded_rectangle([44, y + 4, W - 44, y + rh - 8], 26, fill=acc)
            col = hex_rgba(RANK_COLORS.get(r, "#FFFFFF"))
            nf = font(nfs)
            d.text((66, y + 8), f"{r}.", font=nf, fill=(20, 20, 20, 255) if current else col,
                   stroke_width=0 if current else 5, stroke_fill=(0, 0, 0, 255))
            name = it["name"].upper() if shown else "?????"
            x0 = 66 + numw
            size = maxs
            while size > 36 and d.textlength(name, font=font(size)) > W - x0 - 60:
                size -= 4
            f = font(size)
            while d.textlength(name, font=f) > W - x0 - 60 and len(name) > 4:
                name = name[:-2].rstrip() + "…"
            ny = y + 8 + (nfs - size) * 0.6
            if current:
                d.text((x0, ny), name, font=f, fill=(15, 15, 15, 255))
            else:
                d.text((x0, ny), name, font=f, fill=WHITE if shown else (255, 255, 255, 140),
                       stroke_width=5, stroke_fill=(0, 0, 0, 255))
        if item.get("stat"):
            fs, sl = fit_text(d, item["stat"], 600 if n_items <= 5 else 560, 66 if n_items <= 5 else 58, 40, 1)
            sw = d.textlength(sl[0], font=fs)
            sy = top + n_items * rh + (40 if n_items <= 5 else 26)
            d.rounded_rectangle([50, sy, 50 + sw + 56, sy + fs.size * 1.45], 24, fill=(0, 0, 0, 200),
                                outline=acc, width=5)
            d.text((78, sy + fs.size * 0.18), sl[0], font=fs, fill=acc)
    img.alpha_composite(shadowed(txt))
    img.save(path)


def caption_chunks(text, dur, n_words=3):
    words = text.replace("—", " ").split()
    chunks = [words[i : i + n_words] for i in range(0, len(words), n_words)]
    weights = [sum(len(w) + 2 for w in c) for c in chunks]
    total, t, out = sum(weights), 0.05, []
    span = max(0.5, dur - 0.1)
    for c, wgt in zip(chunks, weights):
        d = span * wgt / total
        out.append((" ".join(c), t, t + d))
        t += d
    return out


def caption_layer(text, accent, path, y_one=1235, y_two=1190):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f, lines = fit_text(d, text.upper(), 900, 92, 60, 2)
    y = y_one if len(lines) == 1 else y_two
    acc = hex_rgba(accent)
    for line in lines:
        # colour numbers in the accent colour
        x = (W - d.textlength(line, font=f)) / 2
        for word in line.split(" "):
            col = acc if any(ch.isdigit() for ch in word) else WHITE
            d.text((x, y), word, font=f, fill=col, stroke_width=9, stroke_fill=(0, 0, 0, 255))
            x += d.textlength(word + " ", font=f)
        y += int(f.size * 1.1)
    img.save(path)


MASCOT_SPOTS = {
    # kind: (height px, top y)
    "item": (360, 1000),
    "item10": (280, 1232),   # 10-item lists: smaller, below the list
    "hook": (560, 1220),
    "outro": (640, 1120),
    "q_hook": (640, 1120),
    "q_outro": (640, 1120),
    "q_ask": (330, 1225),
    "q_timer": (330, 1225),
    "q_reveal": (330, 1225),
}
RIGHT_SIDE = ("item", "item10", "q_ask", "q_timer", "q_reveal")


def mascot_png(src, kind, workdir):
    """Sized 'sticker' version of a mascot pose: white outline + soft shadow."""
    h, _ = MASCOT_SPOTS[kind]
    out = os.path.join(workdir, f"mascot_{kind}_{os.path.splitext(os.path.basename(src))[0]}.png")
    if os.path.exists(out):
        return out
    m = Image.open(src).convert("RGBA")
    m = m.resize((int(m.width * h / m.height), h), Image.LANCZOS)
    pad = 24
    canvas = Image.new("RGBA", (m.width + pad * 2, m.height + pad), (0, 0, 0, 0))
    alpha = Image.new("L", canvas.size, 0)
    alpha.paste(m.split()[3], (pad, pad))
    ring = alpha.filter(ImageFilter.MaxFilter(13))
    shadow = ring.filter(ImageFilter.GaussianBlur(10)).point(lambda a: int(a * 0.55))
    canvas.paste((0, 0, 0, 255), (0, 6), shadow)
    canvas.paste((255, 255, 255, 255), (0, 0), ring)
    canvas.alpha_composite(m, (pad, pad))
    canvas.save(out)
    return out


SFX_DIR = os.path.join(ROOT, "assets", "sfx")
# narrator: cut rumble, even out loud/quiet words, then lift it above the effects
VOICE_FX = "highpass=f=70,equalizer=f=3500:t=q:w=1.2:g=3,acompressor=threshold=-20dB:ratio=3:attack=5:release=80:makeup=4,volume=1.25"


def sfx_for(seg):
    """Sound effects for a segment: (file, start seconds, volume)."""
    f = lambda name: os.path.join(SFX_DIR, name + ".wav")
    if not os.path.isdir(SFX_DIR):
        return []
    kind = seg["kind"]
    if kind in ("hook", "q_hook"):
        out = [(f("whoosh"), 0.0, 0.45)]
    elif kind in ("outro", "q_outro"):
        out = [(f("pop"), 0.15, 0.6)]
    elif kind == "q_ask":
        out = [(f("whoosh"), 0.0, 0.4)]
    elif kind == "q_timer":
        out = [(f("tick"), float(k), 0.8) for k in range(3)]
    elif kind == "q_reveal":
        out = [(f("ding"), 0.0, 0.5)]
    elif seg["item"]["rank"] == 1:
        out = [(f("boom"), 0.0, 0.7), (f("ding"), 0.12, 0.45)]
    else:
        out = [(f("whoosh"), 0.0, 0.4)]
    return [x for x in out if os.path.exists(x[0])]


def gradient_bg(path, seed):
    rnd = random.Random(seed)
    top = tuple(rnd.randint(20, 90) for _ in range(3))
    bot = tuple(rnd.randint(0, 40) for _ in range(3))
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bot)))
    img.save(path)
    return path


def mascot_track(frames, kind, workdir, idx):
    """Animated mascot: frames = [(png path, seconds), ...] -> ffconcat list of sized stickers + first sticker."""
    stickers = [(mascot_png(p, kind, workdir), d) for p, d in frames if d > 0.001]
    # every frame on one canvas size: a size change mid-stream makes ffmpeg rebuild the filter graph and drop
    # the list/caption layers. Bottom-aligned; right-aligned on the right-side spots, centred elsewhere.
    sizes = {p: Image.open(p).size for p, _ in stickers}
    cw, ch = max(w for w, _ in sizes.values()), max(h for _, h in sizes.values())
    padded = {}
    for p, (w, h) in sizes.items():
        if (w, h) == (cw, ch):
            padded[p] = p
            continue
        q = p[:-4] + f"_{cw}x{ch}.png"
        if not os.path.exists(q):
            canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            x = cw - w if kind in RIGHT_SIDE else (cw - w) // 2
            canvas.alpha_composite(Image.open(p).convert("RGBA"), (x, ch - h))
            canvas.save(q)
        padded[p] = q
    stickers = [(padded[p], d) for p, d in stickers]
    lst = os.path.join(workdir, f"s{idx}_mascot.ffconcat")
    with open(lst, "w") as fh:
        fh.write("ffconcat version 1.0\n")
        for p, d in stickers:
            fh.write(f"file '{os.path.abspath(p)}'\nduration {d:.4f}\n")
        fh.write(f"file '{os.path.abspath(stickers[-1][0])}'\nduration 2.0\n")
        fh.write(f"file '{os.path.abspath(stickers[-1][0])}'\n")
    return lst, stickers[0][0]


def render_segment(seg, script, accent, workdir, idx, mascot=None):
    mascot = seg.get("mascot") or mascot
    """seg: kind, text, audio, bg ({'kind': video|photo|none, 'path'})"""
    pad = seg.get("pad", 0.35)
    dur = duration(seg["audio"]) + pad
    static = os.path.join(workdir, f"s{idx}_static.png")
    static_layer(script, seg, accent, static)
    caps = []
    for j, (txt, a, b) in enumerate([] if seg.get("captions") is False else caption_chunks(seg["text"], dur - pad)):
        p = os.path.join(workdir, f"s{idx}_cap{j}.png")
        if mascot and seg["kind"] == "hook":
            caption_layer(txt, accent, p, y_one=1095, y_two=1060)  # between the title and the mascot
        elif mascot and seg["kind"] in ("outro", "q_hook", "q_outro"):
            caption_layer(txt, accent, p, y_one=905, y_two=880)  # sits above the big mascot
        elif seg["kind"] == "item" and len(script.get("items", [])) > 5:
            caption_layer(txt, accent, p, y_one=1560, y_two=1515)  # below the long list
        else:
            caption_layer(txt, accent, p, y_one=1400, y_two=1360)
        caps.append((p, a, b))
    if seg["kind"] == "q_timer":
        caps += countdown_pngs(accent, workdir, idx)
    if seg.get("follow_at") is not None:
        long_list = seg["kind"] == "item" and len(script.get("items", [])) > 5
        caps.append((follow_badge(workdir, idx, seg["kind"], 1420 if long_list else None,
                                  "LIKE + FOLLOW" if long_list else "FOLLOW FOR MORE"), seg["follow_at"], dur))

    bg = seg.get("bg") or {"kind": "none"}
    inputs, vf = [], ""
    if bg["kind"] == "video":
        ss = ["-ss", f"{seg['bg_ss']:.2f}"] if seg.get("bg_ss") else []
        inputs += ["-stream_loop", "-1", *ss, "-i", bg["path"]]
        vf = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"fps={FPS},setsar=1,eq=brightness=-0.05:saturation=1.12[b0]")
    else:
        img = bg["path"] if bg["kind"] == "photo" else gradient_bg(os.path.join(workdir, f"s{idx}_bg.png"), idx)
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", img]
        vf = (f"[0:v]scale={int(W*1.15)}:{int(H*1.15)}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H}:x='(iw-ow)*t/{dur:.2f}':y='(ih-oh)/2',fps={FPS},setsar=1[b0]")
    inputs += ["-i", static]
    chain = [vf, "[b0][1:v]overlay=0:0[v1]"]
    last, n_in = "v1", 2
    if mascot:
        spot = "item10" if seg["kind"] == "item" and len(script.get("items", [])) > 5 else seg["kind"]
        _, my = MASCOT_SPOTS[spot]
        if isinstance(mascot, dict):  # animated: lip-sync / loops / pose changes
            lst, mp = mascot_track(mascot["frames"], spot, workdir, idx)
            inputs += ["-f", "concat", "-safe", "0", "-i", lst]
        else:
            mp = mascot_png(mascot, spot, workdir)
            inputs += ["-loop", "1", "-framerate", str(FPS), "-i", mp]
        x = f"{W - Image.open(mp).width - 10}" if spot in RIGHT_SIDE else "(W-w)/2"
        # pops up from below at the start, then bobs gently while the narrator talks
        pop_in = "" if seg.get("no_pop") else "+if(lt(t,0.3),(0.3-t)/0.3*420,0)"
        y = f"{my}{pop_in}+9*sin(2*PI*t*1.7)"
        chain.append(f"[{last}][{n_in}:v]overlay=x='{x}':y='{y}':eval=frame:shortest=1[vm]")
        last, n_in = "vm", n_in + 1
    for j, (p, a, b) in enumerate(caps):
        inputs += ["-i", p]
        nxt = f"v{j+2}"
        chain.append(f"[{last}][{n_in}:v]overlay=0:0:enable='between(t,{a:.2f},{b:.2f})'[{nxt}]")
        last, n_in = nxt, n_in + 1
    inputs += ["-i", seg["audio"]]
    audio_idx = n_in
    fx = sfx_for(seg)
    if not fx:
        chain.append(f"[{audio_idx}:a]aresample=44100,aformat=channel_layouts=stereo,{VOICE_FX},apad[a]")
    else:
        chain.append(f"[{audio_idx}:a]aresample=44100,aformat=channel_layouts=stereo,{VOICE_FX}[vo]")
        labels = ["[vo]"]
        for k, (path, at, vol) in enumerate(fx):
            inputs += ["-i", path]
            ms = int(at * 1000)
            chain.append(f"[{audio_idx + 1 + k}:a]aresample=44100,aformat=channel_layouts=stereo,"
                         f"adelay={ms}|{ms},volume={vol}[fx{k}]")
            labels.append(f"[fx{k}]")
        chain.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0,apad[a]")
    out = os.path.join(workdir, f"seg{idx:02d}.mp4")
    run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(chain), "-map", f"[{last}]", "-map", "[a]",
         "-t", f"{dur:.2f}", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", out])
    return out


def normalize_host_clip(src, workdir, idx):
    out = os.path.join(workdir, f"seg{idx:02d}.mp4")
    v = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1"
    if has_audio(src):
        cmd = ["ffmpeg", "-y", "-i", src, "-vf", v, "-af", "aresample=44100,aformat=channel_layouts=stereo"]
    else:
        cmd = ["ffmpeg", "-y", "-i", src, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
               "-vf", v, "-shortest", "-map", "0:v", "-map", "1:a"]
    run(cmd + ["-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", out])
    return out


def finish(segments, workdir, out_path, accent, music=None, music_volume=0.1):
    lst = os.path.join(workdir, "concat.txt")
    with open(lst, "w") as fh:
        fh.writelines(f"file '{os.path.abspath(s)}'\n" for s in segments)
    joined = os.path.join(workdir, "joined.mp4")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", joined])
    total = duration(joined)
    acc = accent.lstrip("#")
    inputs = ["-i", joined, "-f", "lavfi", "-i", f"color=c=0x{acc}:s={W}x14:r={FPS}:d={total:.2f}"]
    fc = [f"[0:v][1:v]overlay=x='-w+w*t/{total:.2f}':y=0:eval=frame:shortest=1[v]"]
    if music:
        inputs += ["-stream_loop", "-1", "-i", music]
        fc.append(f"[2:a]volume={music_volume},afade=t=out:st={max(0, total-1.5):.2f}:d=1.5[m]")
        fc.append("[0:a][m]amix=inputs=2:duration=first:normalize=0[mix]")
        fc.append("[mix]loudnorm=I=-12:TP=-1.0:LRA=9[a]")
    else:
        fc.append("[0:a]loudnorm=I=-12:TP=-1.0:LRA=9[a]")
    run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]",
         "-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-movflags", "+faststart", out_path])
    return total
