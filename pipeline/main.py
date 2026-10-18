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


SUBSCRIBE_LINES = [
    "I'm a brand new creator, so if you liked this, hit subscribe. It honestly helps a ton!",
    "New channel alert! Subscribe and I'll keep the rankings coming every single day.",
    "Help a new creator out. Hit subscribe for a fresh top five every day!",
    "I just started this channel, so every sub means a lot. Tap subscribe and let's grow together!",
    "Small channel, big rankings. Subscribe so you don't miss tomorrow's!",
    "If you learned something new, smash that subscribe button. I'm just getting started!",
    "Still a tiny channel, but not for long, right? Hit subscribe and be here from day one.",
    "Want a new ranking every day? Subscribe. It really helps a new creator like me!",
]


def subscribe_line(script):
    """Spoken call to action for the outro: the script's own, or one picked per video."""
    return script.get("cta") or random.Random(script["id"] + "cta").choice(SUBSCRIBE_LINES)


EMOTIONS = ["presenting", "shocked", "thinking", "celebrating", "curious", "excited", "exclaim", "nervous"]


def pick_emotions(script, config):
    """Mascot pose per segment: hook / countdown / #1 reveal / outro. Scripts may set item["emotion"]."""
    folder = os.path.join(ROOT, config.get("mascot_dir", "assets/emotions"))
    have = {e: os.path.join(folder, e + ".png") for e in EMOTIONS if os.path.exists(os.path.join(folder, e + ".png"))}
    if not have:
        return {}
    rnd = random.Random(script["id"])
    pick = lambda opts: have.get(rnd.choice([o for o in opts if o in have] or list(have)))
    poses = {"hook": pick(["presenting", "curious"]), "outro": pick(["celebrating", "presenting"])}
    middle = [e for e in ["thinking", "curious", "excited", "nervous"] if e in have]
    rnd.shuffle(middle)
    for k, it in enumerate(sorted(script["items"], key=lambda i: -i["rank"])):
        want = it.get("emotion")
        if want in have:
            poses[it["rank"]] = have[want]
        elif it["rank"] == 1:
            poses[1] = pick(["shocked", "exclaim"])
        else:
            poses[it["rank"]] = have[middle[k % len(middle)]] if middle else pick(EMOTIONS)
    return poses


def make_video(script, config, offline, workdir):
    accent = config["accent_color"]
    random.seed(script["id"])  # same script → same footage picks on a re-render
    used_ids, credits, segments = set(), [], []
    idx = 0
    mascot = os.path.join(ROOT, config.get("mascot", "")) if config.get("mascot") else None
    if mascot and not os.path.exists(mascot):
        mascot = None
    poses = pick_emotions(script, config)

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
        plan.append(("outro", f"{script['outro']} {subscribe_line(script)}", None, script["items"][0]["search"]))

    for kind, text, item, query in plan:
        audio = os.path.join(workdir, f"v{idx}.mp3")
        voice.speak(text, audio, config["voice"], config["voice_rate"], offline=offline,
                    pitch=config.get("voice_pitch", "+0Hz"))
        bg = {"kind": "none"}
        if not offline:
            clip = os.path.join(workdir, f"bg{idx}.mp4")
            got = stock.find_clip(query, clip, render.duration(audio) + 1, used_ids)
            if got:
                bg = {"kind": got["kind"], "path": got.get("path", clip)}
                credits.append(got)
        pose = poses.get("hook" if kind == "hook" else "outro" if kind == "outro" else item["rank"])
        add({"kind": kind, "text": text, "item": item, "audio": audio, "bg": bg, "mascot": pose})
        print(f"  segment {idx}: {kind} {item['name'] if item else ''}")

    if outro:
        segments.append(render.normalize_host_clip(outro, workdir, idx)); idx += 1

    music = sorted(glob.glob(os.path.join(ROOT, "assets", "music", "*.mp3")))
    os.makedirs(OUT, exist_ok=True)
    out_path = os.path.join(OUT, "video.mp4")
    total = render.finish(segments, workdir, out_path, accent,
                          random.choice(music) if music else None, config["music_volume"])
    return out_path, total, credits, bool(intro or outro)


def next_monday(today=None):
    today = today or (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()  # Manila date
    return today + dt.timedelta(days=(7 - today.weekday()) % 7 or 7)


def sheet_block(n, day, slot, post, file_name):
    return "\n".join([
        f"## {n}. {day:%a %b %d} · {slot} — {post['title']}",
        f"File: `{file_name}` · {post.get('duration', '?')} s",
        "", "**Title**", "```", post["title"], "```",
        "**Description**", "```", post["description"], "```",
        "**Tags**", "```", ", ".join(post["tags"]), "```",
        f"Not made for kids · Altered/synthetic content: **{'Yes' if post['ai_generated'] else 'No'}** · "
        f"Schedule: **{day:%a %b %d}, {slot}** (Manila)",
        "",
    ])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="test render: no keys, no internet")
    ap.add_argument("--count", type=int, default=1, help="how many videos to make in this run")
    ap.add_argument("--start", help="first posting date YYYY-MM-DD (default: next Monday)")
    ap.add_argument("--write", type=int, help="only write scripts until the queue has this many, then stop")
    args = ap.parse_args()

    config = load(os.path.join(ROOT, "config.json"), {})
    slot = config.get("posting_slot", "8:00 AM")
    qpath, hpath = os.path.join(STATE, "queue.json"), os.path.join(STATE, "history.json")
    queue, history = load(qpath, []), load(hpath, [])
    first = dt.date.fromisoformat(args.start) if args.start else next_monday()

    if args.write:
        before = len(queue)
        try:
            refill(config, queue, history, args.write)
        except Exception as e:
            print(f"::error::Script writer failed: {str(e)[:900]}")
        save(qpath, queue)
        print(f"::notice::Queue {before} -> {len(queue)} scripts")
        return

    if len(queue) < args.count and not args.offline:
        print(f"Queue has {len(queue)}, need {args.count}: writing scripts…")
        try:
            refill(config, queue, history, args.count)
        except Exception as e:
            print(f"  (couldn't write enough scripts: {e})")
    if not queue:
        sys.exit("queue is empty")

    batch_dir = os.path.join(OUT, "batch")
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(batch_dir)
    sheet = [f"# Bojji (@bojjiii) — {first:%b %d} to {first + dt.timedelta(days=args.count - 1):%b %d}",
             "", "Upload each file in YouTube Studio **on the Bojji channel** and set Visibility → Schedule "
             f"for its date at {slot} (Manila).", ""]
    done, failed = [], []
    for n in range(args.count):
        if not queue:
            break
        script = queue[0]
        day = first + dt.timedelta(days=n)
        print(f"[{n + 1}/{args.count}] {day} — {script['title']}")
        workdir = os.path.join(ROOT, "work")
        shutil.rmtree(workdir, ignore_errors=True)
        os.makedirs(workdir)
        try:
            video, total, credits, synthetic = make_video(script, config, args.offline, workdir)
        except Exception as e:
            print(f"  FAILED: {e}")
            failed.append(script["title"])
            queue.append(queue.pop(0))  # try it again next batch
            continue
        post = build_post(script, config, credits, synthetic)
        post["duration"] = round(total, 1)
        name = f"{n + 1}_{day:%a-%b%d}_{script['id']}.mp4"
        shutil.move(video, os.path.join(batch_dir, name))
        save(os.path.join(batch_dir, name.replace(".mp4", ".json")), dict(post, post_on=str(day), slot=slot))
        sheet.append(sheet_block(n + 1, day, slot, post, name))
        done.append(name)
        queue.pop(0)
        history.append({"id": script["id"], "title": script["title"], "topic": script.get("topic", ""),
                        "rendered_on": dt.date.today().isoformat(), "post_on": str(day), "status": "rendered"})
    if failed:
        sheet.insert(4, f"⚠️ Couldn't make: {', '.join(failed)} (kept for next week).\n")
    with open(os.path.join(OUT, "upload_sheet.md"), "w") as fh:
        fh.write("\n".join(sheet))
    print(f"Done: {len(done)} videos, {len(failed)} failed")
    if args.offline:
        return
    try:  # keep enough ready scripts for next week; never fail the run over this
        refill(config, queue, history, config.get("queue_min_size", 8))
    except Exception as e:
        print(f"::warning::Couldn't top up the script queue: {str(e)[:900]}")
    save(qpath, queue)
    save(hpath, history)
    if not done:
        sys.exit("no videos were made")


if __name__ == "__main__":
    main()
