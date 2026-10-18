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


LIST_TOP, ROW_H = 300, 118


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

    if seg["kind"] == "hook":
        tag = font(70)
        label = "TOP 5"
        tw = d.textlength(label, font=tag)
        d.rounded_rectangle([(W - tw) / 2 - 34, 250, (W + tw) / 2 + 34, 350], 26, fill=hex_rgba(HIGHLIGHT[0]))
        d.text(((W - tw) / 2, 256), label, font=tag, fill=WHITE)
        rich_title(d, tokens, 395, 110, 64, 4)
    elif seg["kind"] == "outro":
        f = font(118)
        parts = [("WHAT'S YOUR", WHITE), ("#1?", hex_rgba(HIGHLIGHT[3]))]
        y = 330
        for t, c in parts:
            x = (W - d.textlength(t, font=f)) / 2
            d.text((x, y), t, font=f, fill=c, stroke_width=9, stroke_fill=(0, 0, 0, 255))
            y += 130
        f2 = font(70)
        t = "COMMENT BELOW"
        d.text(((W - d.textlength(t, font=f2)) / 2, y + 20), t, font=f2, fill=hex_rgba(HIGHLIGHT[1]),
               stroke_width=7, stroke_fill=(0, 0, 0, 255))
    else:
        item = seg["item"]
        rich_title(d, tokens, 150, 50, 34, 2, max_w=960, stroke=5)
        # big ranking list: #1 at the top, names revealed as the countdown reaches them
        panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(panel).rounded_rectangle([30, LIST_TOP - 16, W - 30, LIST_TOP + 5 * ROW_H + 4], 36,
                                                fill=(0, 0, 0, 120))
        img.alpha_composite(panel)
        for it in sorted(script["items"], key=lambda i: i["rank"]):
            r = it["rank"]
            y = LIST_TOP + (r - 1) * ROW_H
            current = r == item["rank"]
            shown = r >= item["rank"]
            if current:
                d.rounded_rectangle([44, y + 4, W - 44, y + ROW_H - 8], 28, fill=acc)
            col = hex_rgba(RANK_COLORS.get(r, "#FFFFFF"))
            nf = font(76)
            num = f"{r}."
            d.text((70, y + 8), num, font=nf, fill=(20, 20, 20, 255) if current else col,
                   stroke_width=0 if current else 6, stroke_fill=(0, 0, 0, 255))
            name = it["name"].upper() if shown else "?????"
            size = 72
            while size > 44 and d.textlength(name, font=font(size)) > W - 230:
                size -= 4
            f = font(size)
            while d.textlength(name, font=f) > W - 230 and len(name) > 4:
                name = name[:-2].rstrip() + "…"
            ny = y + 8 + (76 - size) * 0.6
            if current:
                d.text((175, ny), name, font=f, fill=(15, 15, 15, 255))
            else:
                d.text((175, ny), name, font=f, fill=WHITE if shown else (255, 255, 255, 140),
                       stroke_width=6, stroke_fill=(0, 0, 0, 255))
        if item.get("stat"):
            fs, sl = fit_text(d, item["stat"], 600, 66, 40, 1)
            sw = d.textlength(sl[0], font=fs)
            sy = LIST_TOP + 5 * ROW_H + 40
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
    "hook": (640, 1120),
    "outro": (640, 1120),
}


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


def render_segment(seg, script, accent, workdir, idx, mascot=None):
    mascot = seg.get("mascot") or mascot
    """seg: kind, text, audio, bg ({'kind': video|photo|none, 'path'})"""
    dur = duration(seg["audio"]) + 0.35
    static = os.path.join(workdir, f"s{idx}_static.png")
    static_layer(script, seg, accent, static)
    caps = []
    for j, (txt, a, b) in enumerate(caption_chunks(seg["text"], dur - 0.35)):
        p = os.path.join(workdir, f"s{idx}_cap{j}.png")
        if mascot and seg["kind"] in ("hook", "outro"):
            caption_layer(txt, accent, p, y_one=905, y_two=880)  # sits above the big mascot
        else:
            caption_layer(txt, accent, p, y_one=1400, y_two=1360)
        caps.append((p, a, b))

    bg = seg.get("bg") or {"kind": "none"}
    inputs, vf = [], ""
    if bg["kind"] == "video":
        inputs += ["-stream_loop", "-1", "-i", bg["path"]]
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
        mp = mascot_png(mascot, seg["kind"], workdir)
        _, my = MASCOT_SPOTS[seg["kind"]]
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", mp]
        x = f"{W - Image.open(mp).width - 10}" if seg["kind"] == "item" else "(W-w)/2"
        # pops up from below at the start, then bobs gently while the narrator talks
        y = (f"{my}+if(lt(t,0.3),(0.3-t)/0.3*420,0)+9*sin(2*PI*t*1.7)")
        chain.append(f"[{last}][{n_in}:v]overlay=x='{x}':y='{y}':eval=frame:shortest=1[vm]")
        last, n_in = "vm", n_in + 1
    for j, (p, a, b) in enumerate(caps):
        inputs += ["-i", p]
        nxt = f"v{j+2}"
        chain.append(f"[{last}][{n_in}:v]overlay=0:0:enable='between(t,{a:.2f},{b:.2f})'[{nxt}]")
        last, n_in = nxt, n_in + 1
    inputs += ["-i", seg["audio"]]
    audio_idx = n_in
    chain.append(f"[{audio_idx}:a]aresample=44100,aformat=channel_layouts=stereo,apad[a]")
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
        fc.append("[mix]loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    else:
        fc.append("[0:a]loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]",
         "-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-movflags", "+faststart", out_path])
    return total
