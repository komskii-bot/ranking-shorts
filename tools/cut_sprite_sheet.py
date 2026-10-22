"""Cut an AI-generated sprite sheet (grid of poses on flat green, optional white/black gutters) into
transparent, trimmed PNG frames. Removes the green screen, gutters, stray specks and green spill."""
import os, sys, numpy as np, cv2
from PIL import Image
from scipy import ndimage as ndi
OUT = "."
# usage: python tools/cut_sprite_sheet.py SHEET.jpg OUT_DIR/FOLDER ROWS COLS name1 name2 ... (reading order)
ALIGN = '--align' in sys.argv
args = [a for a in sys.argv[1:] if a != '--align']
SHEETS = {args[0]: (args[1], int(args[2]), int(args[3]), args[4:])}
def gutters(mask1d, n, size):
    """Cell boundaries: centre of gutter runs near the expected even splits, else even split."""
    b = [0]
    for i in range(1, n):
        exp = size * i // n
        lo, hi = max(0, exp - size // (3 * n)), min(size, exp + size // (3 * n))
        idx = np.where(mask1d[lo:hi])[0]
        b.append(lo + int(idx.mean()) if len(idx) else exp)
    return b + [size]
for key, (folder, R, C, names) in SHEETS.items():
    im = np.array(Image.open(key).convert("RGB"))
    H, W, _ = im.shape
    hsv = cv2.cvtColor(im, cv2.COLOR_RGB2HSV).astype(int)
    green = (hsv[..., 0] > 40) & (hsv[..., 0] < 80) & (hsv[..., 1] > 110) & (hsv[..., 2] > 110)
    whiteblack = (im.min(-1) > 225) | (im.max(-1) < 40)
    gcol = whiteblack.mean(0) > 0.6; grow = whiteblack.mean(1) > 0.6
    has_gutter = gcol.any() or grow.any()
    line = np.zeros((H, W), bool); line[:, gcol] = True; line[grow, :] = True
    xb, yb = gutters(gcol, C, W), gutters(grow, R, H)
    seed = green | line
    lab, _ = ndi.label(seed)
    edge = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) | set(np.unique(lab[line]))
    edge.discard(0)
    bg = np.isin(lab, list(edge)) | line
    # white page margins outside the coloured cells (some sheets have a white frame)
    wl, _ = ndi.label(im.min(-1) > 225)
    bg |= np.isin(wl, list(set(np.unique(np.concatenate([wl[0], wl[-1], wl[:, 0], wl[:, -1]]))) - {0}))
    fg = ~bg
    os.makedirs(os.path.join(OUT, folder), exist_ok=True)
    cells = []
    for r in range(R):
        for c in range(C):
            name = names[r * C + c]
            x0, x1, y0, y1 = xb[c], xb[c + 1], yb[r], yb[r + 1]
            if has_gutter:
                ins = 10
                x0, x1, y0, y1 = x0 + (ins if c else 0), x1 - (ins if c < C - 1 else 0), y0 + (ins if r else 0), y1 - (ins if r < R - 1 else 0)
            cell = fg[y0:y1, x0:x1].copy()
            cl, n = ndi.label(cell)
            if n == 0: print("EMPTY", name); continue
            sizes = ndi.sum(cell, cl, range(1, n + 1))
            frac = 0.5 if name in ('peek_01', 'peek_02') or folder == 'run' else 0.02
            keep = np.isin(cl, [i + 1 for i, s in enumerate(sizes) if s > frac * sizes.max() and s > 1500])
            keep = ndi.binary_fill_holes(keep) & ~(green[y0:y1, x0:x1] & ~ndi.binary_fill_holes(keep) ) | keep
            # remove enclosed bright-green gaps (e.g. between arm and body)
            gz = green[y0:y1, x0:x1]
            if name == 'hold_globe':  # continents are green too: only remove pure screen green
                hz0 = hsv[y0:y1, x0:x1]; gz = gz & (hz0[..., 1] > 200) & (hz0[..., 2] > 200)
            gl, gn = ndi.label(keep & gz)
            if gn:
                gs = ndi.sum(keep, gl, range(1, gn + 1))
                for i, s in enumerate(gs, 1):
                    if s > 150: keep[gl == i] = False
            a = (keep * 255).astype(np.uint8)
            a = cv2.erode(a, np.ones((3, 3), np.uint8)); a = cv2.GaussianBlur(a, (3, 3), 0)
            rgb = im[y0:y1, x0:x1].astype(int).copy()
            # despill green on the outer edge band only
            band = cv2.dilate((a < 250).astype(np.uint8), np.ones((7, 7), np.uint8)).astype(bool) & (a > 0)
            mx = np.maximum(rgb[..., 0], rgb[..., 2])
            hz = hsv[y0:y1, x0:x1]
            strong = (hz[..., 0] > 35) & (hz[..., 0] < 85) & (hz[..., 1] > 60) & (a > 0)
            if name == 'hold_globe': strong[:] = False
            sp = (band & (rgb[..., 1] > mx)) | strong
            rgb[..., 1][sp] = mx[sp]
            rgba = np.dstack([rgb.astype(np.uint8), a])
            cells.append((name, rgba))
    if ALIGN:  # one shared box (cell-relative) for every frame, so loops don't jitter
        hh = min(r.shape[0] for _, r in cells); ww = min(r.shape[1] for _, r in cells)
        boxes = [np.where(r[:hh, :ww, 3] > 0) for _, r in cells]
        y0 = min(b[0].min() for b in boxes); y1 = max(b[0].max() for b in boxes)
        x0 = min(b[1].min() for b in boxes); x1 = max(b[1].max() for b in boxes)
    for name, rgba in cells:
        if ALIGN:
            rgba = rgba[y0:y1 + 1, x0:x1 + 1]
        else:
            ys, xs = np.where(rgba[..., 3] > 0)
            rgba = rgba[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        Image.fromarray(rgba).save(os.path.join(OUT, folder, name + ".png"))
        print(folder, name, rgba.shape[:2])
