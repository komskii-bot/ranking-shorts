"""Turn green-screen Bojji art into transparent, trimmed PNGs.

python tools/key_frames.py <image> <name> [--split N]
--split N cuts a sprite sheet into N figures (left to right) named <name>_01.. .
Output: assets/bojji_full/<name>.png (feet at the bottom edge, small side padding).
"""
import argparse
import os

import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "bojji_full")


def key(img):
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    green = g - np.maximum(r, b)              # how much greener than the other channels
    alpha = np.clip(1 - (green - 40) / 70, 0, 1)  # >110 = background, <40 = solid
    # remove green fringe on soft edges (despill)
    spill = g > np.maximum(r, b)
    a[..., 1] = np.where(spill, np.maximum(r, b) + (g - np.maximum(r, b)) * 0.15, g)
    # drop specks: keep only big connected blobs
    solid = alpha > 0.5
    lab, n = ndimage.label(solid)
    if n:
        sizes = ndimage.sum(solid, lab, range(1, n + 1))
        keep = np.isin(lab, [i + 1 for i, s in enumerate(sizes) if s > 0.01 * sizes.max()])
        keep = ndimage.binary_dilation(keep, iterations=3)
        alpha = alpha * keep
    rgba = np.dstack([a, alpha * 255]).clip(0, 255).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def figures(rgba, n):
    """Split by empty columns into n figures."""
    al = np.asarray(rgba)[..., 3] > 20
    cols = al.any(axis=0)
    runs, start = [], None
    for x, c in enumerate(cols):
        if c and start is None:
            start = x
        if not c and start is not None:
            runs.append((start, x)); start = None
    if start is not None:
        runs.append((start, len(cols)))
    runs = [r for r in runs if r[1] - r[0] > 40]
    while len(runs) > n:  # merge the closest neighbours (e.g. a hand separated from a body)
        gaps = [runs[i + 1][0] - runs[i][1] for i in range(len(runs) - 1)]
        i = int(np.argmin(gaps))
        runs[i:i + 2] = [(runs[i][0], runs[i + 1][1])]
    if len(runs) != n:
        raise SystemExit(f"found {len(runs)} figures, expected {n}")
    return [rgba.crop((x0, 0, x1, rgba.height)) for x0, x1 in runs]


def trim(img, pad=12):
    box = img.getchannel("A").point(lambda v: 255 if v > 20 else 0).getbbox()
    x0, y0, x1, y1 = box
    return img.crop((max(0, x0 - pad), max(0, y0 - pad), min(img.width, x1 + pad), y1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image"); ap.add_argument("name")
    ap.add_argument("--split", type=int, default=1)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    rgba = key(Image.open(a.image))
    parts = figures(rgba, a.split) if a.split > 1 else [rgba]
    for i, p in enumerate(parts, 1):
        name = f"{a.name}_{i:02d}" if a.split > 1 else a.name
        t = trim(p)
        t.save(os.path.join(OUT, name + ".png"))
        print(name, t.size)


if __name__ == "__main__":
    main()
