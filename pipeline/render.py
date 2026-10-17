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


def static_layer(script, seg, accent, path):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    # darken top and caption zones so text always reads
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(H):
        if y < 1200:
            a = int(165 * (1 - y / 1200) ** 1.2)
        elif 1150 < y < 1450:
            a = int(110 * (1 - abs(y - 1300) / 150))
        else:
            a = 0
        if a:
            gd.line([(0, y), (W, y)], fill=(0, 0, 0, a))
    img.alpha_composite(grad)

    txt = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt)
    acc = hex_rgba(accent)

    if seg["kind"] == "hook":
        tag = font(70)
        label = "TOP 5"
        tw = d.textlength(label, font=tag)
        d.rounded_rectangle([(W - tw) / 2 - 34, 330, (W + tw) / 2 + 34, 430], 26, fill=acc)
        d.text(((W - tw) / 2, 336), label, font=tag, fill=(15, 15, 15, 255))
        f, lines = fit_text(d, script["title"].upper(), 900, 104, 60, 3)
        centered(d, lines, f, 480, WHITE, stroke=7)
    elif seg["kind"] == "outro":
        f, lines = fit_text(d, "WHAT'S YOUR #1?", 900, 120, 70, 2)
        centered(d, lines, f, 430, acc, stroke=8)
        f2, l2 = fit_text(d, "COMMENT BELOW", 900, 72, 50, 1)
        centered(d, l2, f2, 710, WHITE, stroke=6)
    else:
        item = seg["item"]
        # small title strip
        f, lines = fit_text(d, script["title"].upper(), 860, 44, 30, 2)
        centered(d, lines, f, 175, (255, 255, 255, 230), stroke=4)
        # rank badge
        badge = font(250)
        label = f"#{item['rank']}"
        bw = d.textlength(label, font=badge)
        d.text(((W - bw) / 2, 235), label, font=badge, fill=acc, stroke_width=10, stroke_fill=(0, 0, 0, 255))
        fn, nl = fit_text(d, item["name"].upper(), 940, 90, 56, 2)
        y = centered(d, nl, fn, 545, WHITE, stroke=7)
        if item.get("stat"):
            fs, sl = fit_text(d, item["stat"], 900, 62, 40, 1)
            sw = d.textlength(sl[0], font=fs)
            d.rounded_rectangle([(W - sw) / 2 - 26, y + 10, (W + sw) / 2 + 26, y + 18 + fs.size * 1.25], 20,
                                fill=(0, 0, 0, 170))
            d.text(((W - sw) / 2, y + 14), sl[0], font=fs, fill=acc)
        # ranking ladder: revealed items, #1 stays hidden until its turn
        lf = font(38)
        ly = 900
        for it in sorted(script["items"], key=lambda i: i["rank"]):
            shown = it["rank"] >= item["rank"]
            name = it["name"] if shown else "???"
            col = acc if it["rank"] == item["rank"] else (255, 255, 255, 235 if shown else 150)
            row = f"{it['rank']}.  {name}"
            while d.textlength(row, font=lf) > 620 and len(row) > 8:
                row = row[:-2].rstrip() + "…"
            d.text((90, ly), row, font=lf, fill=col, stroke_width=4, stroke_fill=(0, 0, 0, 255))
            ly += 52
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
    # kind: (height px, x position or None for centred, top y)
    "item": (340, None, 815),
    "hook": (580, None, 1080),
    "outro": (580, None, 1080),
}


def mascot_png(src, kind, workdir):
    """Sized 'sticker' version of the mascot: white outline + soft shadow."""
    h, _, _ = MASCOT_SPOTS[kind]
    out = os.path.join(workdir, f"mascot_{kind}.png")
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
    # the mascot is cut off at the shirt, so let the bottom edge run off cleanly
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
    """seg: kind, text, audio, bg ({'kind': video|photo|none, 'path'})"""
    dur = duration(seg["audio"]) + 0.35
    static = os.path.join(workdir, f"s{idx}_static.png")
    static_layer(script, seg, accent, static)
    caps = []
    for j, (txt, a, b) in enumerate(caption_chunks(seg["text"], dur - 0.35)):
        p = os.path.join(workdir, f"s{idx}_cap{j}.png")
        if mascot and seg["kind"] in ("hook", "outro"):
            caption_layer(txt, accent, p, y_one=880, y_two=850)  # sits above the big mascot
        else:
            caption_layer(txt, accent, p)
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
        _, mx, my = MASCOT_SPOTS[seg["kind"]]
        inputs += ["-loop", "1", "-framerate", str(FPS), "-i", mp]
        x = f"{W - Image.open(mp).width + 10}" if seg["kind"] == "item" else "(W-w)/2"
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
