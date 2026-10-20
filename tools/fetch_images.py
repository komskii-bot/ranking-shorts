"""Fetch candidate photos for thumbnails (GitHub Actions: the sandbox can't reach image sites).

python tools/fetch_images.py <out_dir> "query one" "query two" ...
Saves up to 6 landscape photos per query from Pixabay (free licence) and Openverse
(CC0 / public domain / CC BY / CC BY-SA, licence + credit recorded) plus credits.json.
"""
import json
import os
import re
import sys

import requests

UA = {"User-Agent": "RankingShorts/1.0 (+https://github.com/komskii-bot/ranking-shorts)"}


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def pixabay(q, n=6):
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return []
    r = requests.get("https://pixabay.com/api/", timeout=60, params={
        "key": key, "q": q, "image_type": "photo", "orientation": "horizontal", "per_page": 30,
        "safesearch": "true", "min_width": 1280, "order": "popular"})
    hits = r.json().get("hits", []) if r.ok else []
    words = [w for w in q.lower().split() if len(w) > 2]
    hits = [h for h in hits if sum(w in h.get("tags", "").lower() for w in words) >= max(1, len(words) - 1)]
    return [{"url": h["largeImageURL"], "page": h["pageURL"], "by": h.get("user"), "site": "Pixabay",
             "license": "Pixabay Content License", "tags": h.get("tags")} for h in hits[:n]]


def openverse(q, n=6):
    r = requests.get("https://api.openverse.org/v1/images/", headers=UA, timeout=60, params={
        "q": q, "license": "cc0,pdm,by,by-sa", "aspect_ratio": "wide", "page_size": 20, "mature": "false"})
    res = r.json().get("results", []) if r.ok else []
    res = [x for x in res if (x.get("width") or 0) >= 1200]
    return [{"url": x["url"], "page": x.get("foreign_landing_url"), "by": x.get("creator"), "site": x.get("source"),
             "license": f"{x.get('license', '').upper()} {x.get('license_version') or ''}".strip(),
             "title": x.get("title")} for x in res[:n]]


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    credits = {}
    for q in sys.argv[2:]:
        for src, items in (("px", pixabay(q)), ("ov", openverse(q))):
            for i, it in enumerate(items):
                name = f"{slug(q)}_{src}{i}.jpg"
                try:
                    r = requests.get(it["url"], headers=UA, timeout=90)
                    r.raise_for_status()
                    open(os.path.join(out, name), "wb").write(r.content)
                    credits[name] = dict(it, query=q)
                    print("saved", name, it["license"], it.get("by"))
                except Exception as e:
                    print("skip", it["url"], e)
    json.dump(credits, open(os.path.join(out, "credits.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
