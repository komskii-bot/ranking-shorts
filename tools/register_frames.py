"""Line up animation frames on the face: shift/scale each frame so its head matches the base frame.
Usage: python tools/register_frames.py BASE.png OTHER.png ...   (others are rewritten in place, base canvas size)"""
import sys, numpy as np, cv2
from PIL import Image

def load(p):
    return np.array(Image.open(p).convert("RGBA"))

base = load(sys.argv[1])
bh, bw = base.shape[:2]
ys, xs = np.where(base[..., 3] > 0)
# template: hair + glasses + eyes (top ~52% of the figure), not the mouth or hands
y0, y1 = ys.min(), ys.min() + int((ys.max() - ys.min()) * 0.52)
x0, x1 = xs.min() + (xs.max() - xs.min()) // 6, xs.max() - (xs.max() - xs.min()) // 6
gray = lambda a: cv2.cvtColor(np.where(a[..., 3:] > 0, a[..., :3], 0).astype(np.uint8), cv2.COLOR_RGB2GRAY)
tpl = gray(base)[y0:y1, x0:x1]
for p in sys.argv[2:]:
    fr = load(p)
    best = (-1, 1.0, 0, 0)
    for s in np.arange(0.90, 1.101, 0.01):
        sc = cv2.resize(fr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        g = gray(sc)
        if g.shape[0] < tpl.shape[0] or g.shape[1] < tpl.shape[1]:
            continue
        r = cv2.matchTemplate(g, tpl, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(r)
        if mv > best[0]:
            best = (mv, s, ml[0] - x0, ml[1] - y0)
    score, s, dx, dy = best
    sc = cv2.resize(fr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    M = np.float32([[1, 0, -dx], [0, 1, -dy]])
    out = cv2.warpAffine(sc, M, (bw, bh), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    Image.fromarray(out).save(p)
    print(f"{p}: scale {s:.2f} shift ({-dx},{-dy}) match {score:.3f}")
