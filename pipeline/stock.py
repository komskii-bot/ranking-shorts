"""Free stock footage from Pexels (free API key at pexels.com/api)."""
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


def _best_file(video):
    files = [f for f in video.get("video_files", []) if f.get("link") and f.get("height")]
    portrait = [f for f in files if f["height"] >= f.get("width", 0)]
    pool = portrait or files
    # closest to 1920 tall without going huge (keeps downloads fast)
    pool.sort(key=lambda f: abs(f["height"] - 1920) + (5000 if f["height"] > 2600 else 0))
    return pool[0] if pool else None


def find_clip(query, out_path, min_secs, used_ids):
    """Download a portrait-friendly clip. Returns credit dict, or None."""
    for orientation in ("portrait", None):
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
            f = _best_file(v)
            if not f:
                continue
            try:
                with requests.get(f["link"], stream=True, timeout=120) as r:
                    r.raise_for_status()
                    with open(out_path, "wb") as fh:
                        for chunk in r.iter_content(1 << 20):
                            fh.write(chunk)
                used_ids.add(v["id"])
                return {"kind": "video", "by": v.get("user", {}).get("name", "Pexels"), "url": v.get("url")}
            except Exception as e:
                print(f"  download failed: {e}")
    # fall back to a photo (rendered with a slow zoom)
    data = _get("/v1/search", {"query": query, "per_page": 10, "orientation": "portrait"})
    if data and data.get("photos"):
        p = random.choice(data["photos"][:5])
        img = out_path.rsplit(".", 1)[0] + ".jpg"
        r = requests.get(p["src"]["large2x"], timeout=60)
        if r.ok:
            open(img, "wb").write(r.content)
            return {"kind": "photo", "path": img, "by": p.get("photographer", "Pexels"), "url": p.get("url")}
    return None
