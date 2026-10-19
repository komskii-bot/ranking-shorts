"""Free stock footage: Pexels (PEXELS_API_KEY) or Pixabay (PIXABAY_API_KEY).
Whichever keys exist are tried in that order."""
import os
import random

import requests

API = "https://api.pexels.com"


def _get(path, params):
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return None
    r = requests.get(API + path, params=params, headers={"Authorization": key}, timeout=60)
    if r.status_code >= 400:
        print(f"  Pexels {r.status_code} for {params.get('query')}")
        return None
    return r.json()


def _best_file(video, landscape=False):
    files = [f for f in video.get("video_files", []) if f.get("link") and f.get("height")]
    if landscape:
        pool = [f for f in files if f.get("width", 0) > f["height"]] or files
        pool.sort(key=lambda f: abs(f.get("width", 0) - 1920) + (5000 if f.get("width", 0) > 2600 else 0))
        return pool[0] if pool else None
    portrait = [f for f in files if f["height"] >= f.get("width", 0)]
    pool = portrait or files
    # closest to 1920 tall without going huge (keeps downloads fast)
    pool.sort(key=lambda f: abs(f["height"] - 1920) + (5000 if f["height"] > 2600 else 0))
    return pool[0] if pool else None


def _download(url, out_path):
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(out_path, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)


def _pixabay(query, out_path, min_secs, used_ids, landscape=False):
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return None
    try:
        r = requests.get("https://pixabay.com/api/videos/", timeout=60,
                         params={"key": key, "q": query[:100], "per_page": 20, "safesearch": "true"})
        hits = [h for h in r.json().get("hits", []) if h["id"] not in used_ids] if r.ok else []
    except Exception as e:
        print(f"  Pixabay error: {e}")
        hits = []
    long_enough = [h for h in hits if h.get("duration", 0) >= min_secs] or hits
    # prefer tall clips, then random for variety
    random.shuffle(long_enough)
    tall = lambda h: h["videos"]["medium"].get("height", 0) >= h["videos"]["medium"].get("width", 1)
    long_enough.sort(key=lambda h: (1 if tall(h) else 0) if landscape else (0 if tall(h) else 1))
    for h in long_enough[:4]:
        v = h["videos"].get("large") or {}
        if not v.get("url") or v.get("size", 0) > 60_000_000:
            v = h["videos"].get("medium") or {}
        if not v.get("url"):
            continue
        try:
            _download(v["url"], out_path)
            used_ids.add(h["id"])
            return {"kind": "video", "by": h.get("user", "Pixabay"), "url": h.get("pageURL"), "site": "Pixabay"}
        except Exception as e:
            print(f"  download failed: {e}")
    try:  # photo fallback
        r = requests.get("https://pixabay.com/api/", timeout=60,
                         params={"key": key, "q": query[:100], "image_type": "photo",
                                 "orientation": "horizontal" if landscape else "vertical", "per_page": 10, "safesearch": "true"})
        hits = r.json().get("hits", []) if r.ok else []
        if hits:
            h = random.choice(hits[:5])
            img = out_path.rsplit(".", 1)[0] + ".jpg"
            _download(h["largeImageURL"], img)
            return {"kind": "photo", "path": img, "by": h.get("user", "Pixabay"), "url": h.get("pageURL"), "site": "Pixabay"}
    except Exception as e:
        print(f"  Pixabay photo error: {e}")
    return None


def _openverse(query, out_path, used_ids, landscape=False):
    """No key needed. Public-domain / CC0 photos only (free for commercial use)."""
    for aspect in (("wide" if landscape else "tall"), None):
        params = {"q": query[:200], "license": "cc0,pdm", "page_size": 20, "mature": "false"}
        if aspect:
            params["aspect_ratio"] = aspect
        try:
            r = requests.get("https://api.openverse.org/v1/images/", params=params, timeout=60,
                             headers={"User-Agent": "RankingShorts/1.0 (+https://komskii-bot.github.io/ranking-shorts/)"})
            results = r.json().get("results", []) if r.ok else []
        except Exception as e:
            print(f"  Openverse error: {e}")
            results = []
        results = [x for x in results if x.get("url") and x["id"] not in used_ids
                   and (x.get("height") or 0) >= 900]
        random.shuffle(results)
        for x in results[:4]:
            img = out_path.rsplit(".", 1)[0] + ".jpg"
            try:
                _download(x["url"], img)
                from PIL import Image
                Image.open(img).verify()
                used_ids.add(x["id"])
                return {"kind": "photo", "path": img, "by": x.get("creator") or "Openverse",
                        "url": x.get("foreign_landing_url"), "site": "Openverse"}
            except Exception as e:
                print(f"  image skipped: {e}")
    return None


def find_clip(query, out_path, min_secs, used_ids, landscape=False):
    """Download a clip (portrait for Shorts, landscape for explainers). Returns credit dict, or None."""
    return (_pexels(query, out_path, min_secs, used_ids, landscape)
            or _pixabay(query, out_path, min_secs, used_ids, landscape)
            or _openverse(query, out_path, used_ids, landscape))


def _pexels(query, out_path, min_secs, used_ids, landscape=False):
    if not os.environ.get("PEXELS_API_KEY"):
        return None
    for orientation in (("landscape" if landscape else "portrait"), None):
        params = {"query": query, "per_page": 15, "size": "medium"}
        if orientation:
            params["orientation"] = orientation
        data = _get("/videos/search", params)
        if not data:
            continue
        videos = [v for v in data.get("videos", []) if v["id"] not in used_ids]
        long_enough = [v for v in videos if v.get("duration", 0) >= min_secs] or videos
        random.shuffle(long_enough)
        for v in long_enough[:4]:
            f = _best_file(v, landscape)
            if not f:
                continue
            try:
                with requests.get(f["link"], stream=True, timeout=120) as r:
                    r.raise_for_status()
                    with open(out_path, "wb") as fh:
                        for chunk in r.iter_content(1 << 20):
                            fh.write(chunk)
                used_ids.add(v["id"])
                return {"kind": "video", "by": v.get("user", {}).get("name", "Pexels"), "url": v.get("url"), "site": "Pexels"}
            except Exception as e:
                print(f"  download failed: {e}")
    # fall back to a photo (rendered with a slow zoom)
    data = _get("/v1/search", {"query": query, "per_page": 10, "orientation": "landscape" if landscape else "portrait"})
    if data and data.get("photos"):
        p = random.choice(data["photos"][:5])
        img = out_path.rsplit(".", 1)[0] + ".jpg"
        r = requests.get(p["src"]["large2x"], timeout=60)
        if r.ok:
            open(img, "wb").write(r.content)
            return {"kind": "photo", "path": img, "by": p.get("photographer", "Pexels"), "url": p.get("url"), "site": "Pexels"}
    return None
