"""Daily run: pick the next ranking script, render it, hand it off for posting.

python -m pipeline.main            # normal run (used by GitHub Actions)
python -m pipeline.main --offline  # test render with no internet / no keys
"""
import argparse
import datetime as dt
import glob
import json
import os
import random
import shutil
import sys

from . import render, stock, voice, writer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "state")
OUT = os.path.join(ROOT, "out")


def load(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


def save(path, data):
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def wanted_topics(history, queue):
    path = os.path.join(ROOT, "topics.txt")
    if not os.path.exists(path):
        return []
    used = {h["title"].lower() for h in history} | {q["title"].lower() for q in queue}
    used |= {h.get("topic", "").lower() for h in history + queue}
    lines = [l.strip() for l in open(path) if l.strip() and not l.startswith("#")]
    return [l for l in lines if l.lower() not in used]


def refill(config, queue, history, target):
    while len(queue) < target:
        topics = wanted_topics(history, queue)
        topic = topics[0] if topics else None
        used = [h["title"] for h in history] + [q["title"] for q in queue]
        s = writer.write_script(config, used, topic)
        if topic:
            s["topic"] = topic
        if any(s["id"] == q["id"] for q in queue + history):
            s["id"] += "-" + dt.date.today().strftime("%m%d")
        queue.append(s)
        print(f"  queued: {s['title']}")


def pick_host_clip(kind):
    clips = sorted(glob.glob(os.path.join(ROOT, "host_clips", kind, "*.mp4")))
    return random.choice(clips) if clips else None


def build_post(script, config, credits, synthetic):
    title = script["title"].strip()
    if "#shorts" not in title.lower() and len(title) <= 88:
        title += " #shorts"
    lines = [script["description"].strip()]
    if script.get("as_of"):
        lines.append(f"Data as of {script['as_of']}.")
    lines.append("")
    lines.append("What would you put at #1? Tell us in the comments!")
    if script.get("sources"):
        lines.append("")
        lines.append("Sources: " + "; ".join(script["sources"][:5]))
    if credits:
        for site in dict.fromkeys(c.get("site", "Pexels") for c in credits):
            names = ", ".join(dict.fromkeys(c["by"] for c in credits if c.get("site", "Pexels") == site))
            domain = "openverse.org" if site == "Openverse" else f"{site.lower()}.com"
            lines.append(f"Images and footage from {site} ({domain}): {names}")
    tags = [t.lstrip("#") for t in script["tags"]]
    lines.append("")
    lines.append(" ".join(config["default_hashtags"]))
    return {
        "title": title[:100],
        "description": "\n".join(lines)[:4900],
        "tags": tags[:15],
        "made_for_kids": config["made_for_kids"],
        "ai_generated": synthetic,
        "script_id": script["id"],
    }


def write_upload_notes(post, script):
    """Copy-paste sheet for a manual YouTube Studio upload (used as the release notes)."""
    lines = [
        "**Upload to Bojji (@bojjiii)** — check the channel name in the top-right of YouTube Studio first.",
        "",
        "### Title",
        "```", post["title"], "```",
        "### Description",
        "```", post["description"], "```",
        "### Tags",
        "```", ", ".join(post["tags"]), "```",
        "### Settings",
        "- Audience: **No, it's not made for kids**",
        f"- Altered or synthetic content: **{'Yes' if post['ai_generated'] else 'No'}**",
        "- Visibility: **Schedule** for your posting slot",
        f"- Length: {post.get('duration', '?')} s",
    ]
    if script.get("sources"):
        lines += ["", "Sources used for the facts: " + "; ".join(script["sources"][:5])]
    with open(os.path.join(OUT, "upload_notes.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")


def make_video(script, config, offline, workdir):
    accent = config["accent_color"]
    used_ids, credits, segments = set(), [], []
    idx = 0
    mascot = os.path.join(ROOT, config.get("mascot", "")) if config.get("mascot") else None
    if mascot and not os.path.exists(mascot):
        mascot = None

    def add(seg):
        nonlocal idx
        segments.append(render.render_segment(seg, script, accent, workdir, idx, mascot))
        idx += 1

    intro = None if offline else pick_host_clip("intro")
    outro = None if offline else pick_host_clip("outro")
    if intro:
        segments.append(render.normalize_host_clip(intro, workdir, idx)); idx += 1

    plan = [("hook", script["hook"], None, script["items"][-1]["search"])]
    plan += [("item", f"Number {i['rank']}. {i['name']}. {i['line']}", i, i["search"]) for i in script["items"]]
    if not outro:
        plan.append(("outro", script["outro"], None, script["items"][0]["search"]))

    for kind, text, item, query in plan:
        audio = os.path.join(workdir, f"v{idx}.mp3")
        voice.speak(text, audio, config["voice"], config["voice_rate"], offline=offline)
        bg = {"kind": "none"}
        if not offline:
            clip = os.path.join(workdir, f"bg{idx}.mp4")
            got = stock.find_clip(query, clip, render.duration(audio) + 1, used_ids)
            if got:
                bg = {"kind": got["kind"], "path": got.get("path", clip)}
                credits.append(got)
        add({"kind": kind, "text": text, "item": item, "audio": audio, "bg": bg})
        print(f"  segment {idx}: {kind} {item['name'] if item else ''}")

    if outro:
        segments.append(render.normalize_host_clip(outro, workdir, idx)); idx += 1

    music = sorted(glob.glob(os.path.join(ROOT, "assets", "music", "*.mp3")))
    os.makedirs(OUT, exist_ok=True)
    out_path = os.path.join(OUT, "video.mp4")
    total = render.finish(segments, workdir, out_path, accent,
                          random.choice(music) if music else None, config["music_volume"])
    return out_path, total, credits, bool(intro or outro)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="test render: no keys, no internet")
    ap.add_argument("--script-id", help="render this queued script instead of the next one")
    ap.add_argument("--check-channel", action="store_true", help="only verify which channel the login uploads to")
    args = ap.parse_args()

    config = load(os.path.join(ROOT, "config.json"), {})
    if not args.offline and config.get("upload_mode") == "youtube":
        from . import youtube
        youtube.check_channel(config)  # stops here, before any work, if it's the wrong channel
        if args.check_channel:
            return
    qpath, hpath = os.path.join(STATE, "queue.json"), os.path.join(STATE, "history.json")
    queue, history = load(qpath, []), load(hpath, [])

    if not queue:
        if args.offline:
            sys.exit("queue is empty")
        print("Queue empty, writing a script…")
        refill(config, queue, history, 1)

    script = next((q for q in queue if q["id"] == args.script_id), None) if args.script_id else queue[0]
    if not script:
        sys.exit(f"no queued script with id {args.script_id}")
    print(f"Rendering: {script['title']}")

    workdir = os.path.join(ROOT, "work")
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir)
    video, total, credits, synthetic = make_video(script, config, args.offline, workdir)
    post = build_post(script, config, credits, synthetic)
    post["duration"] = round(total, 1)
    save(os.path.join(OUT, "post.json"), post)
    write_upload_notes(post, script)
    print(f"Done: {video} ({total:.1f}s)")

    if args.offline:
        return

    entry = {"id": script["id"], "title": script["title"], "topic": script.get("topic", ""),
             "rendered_on": dt.date.today().isoformat(), "status": "rendered"}
    if config.get("upload_mode") == "youtube":
        from . import youtube
        script["yt_title"], script["yt_description"] = post["title"], post["description"]
        entry["youtube_id"] = youtube.upload(video, script, config, synthetic)
        entry["status"] = "uploaded"
        print(f"Uploaded: https://youtube.com/shorts/{entry['youtube_id']}")

    queue = [q for q in queue if q["id"] != script["id"]]
    history.append(entry)
    try:  # keep a buffer of ready scripts; never fail the run over this
        refill(config, queue, history, config.get("queue_min_size", 5))
    except Exception as e:
        print(f"  (couldn't top up queue: {e})")
    save(qpath, queue)
    save(hpath, history)


if __name__ == "__main__":
    main()
