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
    quiz = script.get("format") == "quiz"
    lines.append("How many did you get right? Comment your score!" if quiz
                 else "What would you put at #1? Tell us in the comments!")
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
    lines.append(" ".join(config.get("quiz_hashtags", ["#shorts", "#quiz", "#trivia", "#facts"]) if quiz
                          else config["default_hashtags"]))
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


CTA_LINES = [
    "I'm a brand new creator, so if you liked this, hit subscribe. It honestly helps a ton!",
    "New channel alert! Subscribe and I'll keep these coming every single day.",
    "Help a small creator out. Like, share and subscribe. It's free and it makes my day!",
    "I just started this channel, so every sub means a lot. Tap subscribe and let's grow together!",
    "Small channel, big facts. Follow so you don't miss tomorrow's!",
    "If you learned something new, smash like and share it with a friend. I'm just getting started!",
    "Still a tiny channel, but not for long, right? Hit subscribe and be here from day one.",
    "A like and a follow would honestly make me so happy. Thanks for supporting a small creator!",
    "Send this to a friend and see if they can beat you. And hey, subscribe, it's free!",
    "Supporting a small creator costs nothing. Like, follow, and I'll see you tomorrow!",
]


def subscribe_line(script):
    """Spoken call to action for the outro: the script's own, or one picked per video."""
    return script.get("cta") or random.Random(script["id"] + "cta").choice(CTA_LINES)


MID_FOLLOW_LINES = [
    "Enjoying this? Follow for more!",
    "If you're enjoying this, hit follow for more!",
    "Quick favor: follow if you're liking this!",
    "Liking it so far? Tap follow!",
    "Having fun? Follow for a new one every day!",
    "Don't forget to follow for more like this!",
]


INTRO_LINES = [
    "And we're back with another Top 10! Today we're ranking {topic}.",
    "Welcome back to another Bojji Top 10! Today it's {topic}.",
    "Another day, another Top 10! This time we're ranking {topic}.",
    "It's Top 10 time! Today we're counting down {topic}.",
    "We're back! Today's Top 10 is {topic}.",
    "Okay, new Top 10! Let's rank {topic}.",
]
INTRO_SUBS = [
    " If you haven't yet, hit subscribe so you never miss one.",
    " New here? Subscribe, there's a new one every day.",
    " Not subscribed yet? Let's fix that!",
    "",  # some days: no ask, straight into the countdown
    "",
]


def intro_topic(script):
    t = script.get("intro_topic")
    if not t:
        import re as _re
        t = _re.sub(r"(?i)^top\s+\d+\s+", "", script["title"])
        t = _re.sub(r"\s*\((?:19|20)\d\d\)|#\w+", "", t).strip(" .!?")
        keep = {"iphone": "iPhone", "unesco": "UNESCO", "olympic": "Olympic", "android": "Android"}
        t = " ".join(keep.get(w.lower(), w.lower()) for w in t.split())
        if not t.lower().startswith(("the ", "a ")):
            t = "the " + t
    return t


def intro_line(script):
    """Spoken right after the hook: 'And we're back with another Top 10…' (varies per video)."""
    rnd = random.Random(script["id"] + "intro")
    return rnd.choice(INTRO_LINES).format(topic=intro_topic(script)) + rnd.choice(INTRO_SUBS)


# Top 10s: a "before the top five" ask after #6 — a cliffhanger and a like/follow request in one
MID_TOP5_LINES = [
    "Before the top five: if you're enjoying this, a like and a follow honestly mean the world to a small creator like me.",
    "Quick one before the top five! If you're liking this, tap like and follow. It really helps me keep making these.",
    "Okay, top five coming up. If you're enjoying it, a like and a follow seriously makes my day as a new creator.",
    "Before we hit the top five: a like and a follow costs you nothing and means so much to me. Thank you!",
    "Top five is next! If this is fun so far, like and follow. Every single one helps a small channel like mine grow.",
    "Real talk before the top five: every like and follow tells me you want more of these. So if you do, tap them!",
]


def mid_follow(script, choices):
    """Pick where (and which) mid-video ask goes, the same every re-render."""
    rnd = random.Random(script["id"] + "mid")
    if len(script.get("items", [])) > 5:
        return 6, rnd.choice(MID_TOP5_LINES)
    return rnd.choice(choices), rnd.choice(MID_FOLLOW_LINES)


def join_audio(first, second, out, gap=0.15):
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", first, "-f", "lavfi", "-t", f"{gap}",
                    "-i", "anullsrc=r=24000:cl=mono", "-i", second, "-filter_complex",
                    "[0:a]aresample=44100,aformat=channel_layouts=stereo[a0];"
                    "[1:a]aresample=44100,aformat=channel_layouts=stereo[a1];"
                    "[2:a]aresample=44100,aformat=channel_layouts=stereo[a2];"
                    "[a0][a1][a2]concat=n=3:v=0:a=1[o]", "-map", "[o]", out], check=True)
    return out


EMOTIONS = ["presenting", "shocked", "thinking", "celebrating", "curious", "excited", "exclaim", "nervous",
            # Oct 22 expression set (busts on transparent background)
            "angry", "annoyed", "bored", "chill", "cringe", "disappointed", "embarrassed", "hopeful", "joyful",
            "laughing", "mischievous", "panicked", "sad", "shy", "sleepy", "surprised", "suspicious", "sweaty",
            "unsure", "wink", "worried"]
# Poses picked automatically; the gloomier ones (angry, sad, bored...) are only used when a script asks for them.
AUTO_HOOK = ["presenting", "curious", "mischievous", "wink", "suspicious"]
AUTO_MIDDLE = ["thinking", "curious", "excited", "nervous", "hopeful", "unsure", "suspicious", "surprised",
               "mischievous", "wink", "chill", "laughing"]
AUTO_TOP = ["shocked", "exclaim", "surprised", "panicked"]
AUTO_OUTRO = ["celebrating", "presenting", "joyful", "laughing", "wink"]


def emotion_files(config):
    folder = os.path.join(ROOT, config.get("mascot_dir", "assets/emotions"))
    return {e: os.path.join(folder, e + ".png") for e in EMOTIONS if os.path.exists(os.path.join(folder, e + ".png"))}


def pick_emotions(script, config):
    """Mascot pose per segment: hook / countdown / #1 reveal / outro. Scripts may set item["emotion"]."""
    have = emotion_files(config)
    if not have:
        return {}
    rnd = random.Random(script["id"])
    pick = lambda opts: have.get(rnd.choice([o for o in opts if o in have] or list(have)))
    poses = {"hook": pick(AUTO_HOOK), "outro": pick(AUTO_OUTRO)}
    middle = [e for e in AUTO_MIDDLE if e in have]
    rnd.shuffle(middle)
    for k, it in enumerate(sorted(script["items"], key=lambda i: -i["rank"])):
        want = it.get("emotion")
        if want in have:
            poses[it["rank"]] = have[want]
        elif it["rank"] == 1:
            poses[1] = pick(AUTO_TOP)
        else:
            poses[it["rank"]] = have[middle[k % len(middle)]] if middle else pick(EMOTIONS)
    return poses


def silence(path, secs):
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-t", f"{secs:.2f}", path], check=True)
    return path


def make_quiz_video(script, config, offline, workdir):
    """Quiz Short: hook → for each question (ask, 3 s countdown, reveal) → score outro."""
    accent = config.get("quiz_accent", "#1DE9D6")
    random.seed(script["id"])
    rnd = random.Random(script["id"] + "pose")
    have = emotion_files(config)
    pose = lambda opts: have.get(rnd.choice([o for o in opts if o in have] or list(have))) if have else None
    used_ids, credits, segments = set(), [], []
    qs = script["questions"]
    idx = 0

    clips = 0

    def speak(text):
        nonlocal clips
        clips += 1  # every line gets its own file (question and answer used to collide)
        audio = os.path.join(workdir, f"voice{clips:02d}.mp3")
        voice.speak(text, audio, config["voice"], config["voice_rate"], offline=offline,
                    pitch=config.get("voice_pitch", "+0Hz"))
        return audio

    def footage(query, need):
        if offline:
            return {"kind": "none"}
        clip = os.path.join(workdir, f"bg{idx}.mp4")
        got = stock.find_clip(query, clip, need, used_ids)
        if not got:
            return {"kind": "none"}
        credits.append(got)
        return {"kind": got["kind"], "path": got.get("path", clip)}

    def add(seg):
        nonlocal idx
        segments.append(render.render_segment(seg, script, accent, workdir, idx))
        idx += 1

    a = speak(script["hook"])
    add({"kind": "q_hook", "text": script["hook"], "item": None, "audio": a,
         "bg": footage(qs[0].get("search", "technology"), 6), "mascot": pose(["presenting", "curious"])})
    follow_q, follow_text = mid_follow(script, [2, 3, 4])
    for n, q in enumerate(qs, 1):
        ask = speak(f"Question {n}. {q['q']}")
        letter = "ABCD"[q["answer"]]
        reveal_text = q.get("say") or f"It's {letter}. {q['options'][q['answer']]}!"
        rev = speak(reveal_text)
        follow_at = None
        if n == follow_q:
            extra = speak(follow_text)
            follow_at = render.duration(rev) + 0.9
            rev = join_audio(rev, extra, os.path.join(workdir, f"reveal{n}_all.mp3"), gap=0.8)
            reveal_text = f"{reveal_text} {follow_text}"
        t_ask, t_rev = render.duration(ask) + 0.3, render.duration(rev) + 1.7
        bg = footage(q.get("search", "abstract background"), t_ask + 3 + t_rev + 1)
        base = {"question": q, "n": n, "total": len(qs), "item": None, "captions": False, "bg": bg}
        add(dict(base, kind="q_ask", text=q["q"], audio=ask, pad=0.3,
                 mascot=pose(["thinking", "curious"])))
        timer = silence(os.path.join(workdir, f"timer{n}.mp3"), 2.65)
        add(dict(base, kind="q_timer", text="", audio=timer, bg_ss=t_ask, no_pop=True,
                 mascot=pose(["nervous", "thinking"])))
        add(dict(base, kind="q_reveal", text=reveal_text, audio=rev, pad=1.7, bg_ss=t_ask + 3, no_pop=True,
                 follow_at=follow_at,
                 mascot=pose(q.get("emotion", "").split() or ["excited", "celebrating", "shocked"])))
        print(f"  question {n}: {q['q'][:50]}")
    outro = f"{script['outro']} {subscribe_line(script)}"
    a = speak(outro)
    add({"kind": "q_outro", "text": outro, "item": None, "audio": a,
         "bg": footage(qs[-1].get("search", "technology"), 10), "mascot": pose(["celebrating", "presenting"])})
    os.makedirs(OUT, exist_ok=True)
    out_path = os.path.join(OUT, "video.mp4")
    total = render.finish(segments, workdir, out_path, accent, None, config["music_volume"])
    return out_path, total, credits, False


def make_video(script, config, offline, workdir):
    if script.get("format") == "quiz":
        return make_quiz_video(script, config, offline, workdir)
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

    hook_text = script["hook"] + (" " + intro_line(script) if len(script["items"]) > 5 else "")
    plan = [("hook", hook_text, None, script["items"][-1]["search"])]
    plan += [("item", f"Number {i['rank']}. {i['name']}. {i['line']}", i, i["search"]) for i in script["items"]]
    if not outro:
        plan.append(("outro", f"{script['outro']} {subscribe_line(script)}", None, script["items"][0]["search"]))

    follow_rank, follow_text = mid_follow(script, [4, 3, 2])
    for kind, text, item, query in plan:
        audio = os.path.join(workdir, f"v{idx}.mp3")
        voice.speak(text, audio, config["voice"], config["voice_rate"], offline=offline,
                    pitch=config.get("voice_pitch", "+0Hz"))
        follow_at = None
        if item and item["rank"] == follow_rank:
            extra = os.path.join(workdir, f"v{idx}_follow.mp3")
            voice.speak(follow_text, extra, config["voice"], config["voice_rate"], offline=offline,
                        pitch=config.get("voice_pitch", "+0Hz"))
            follow_at = render.duration(audio) + 0.1
            audio = join_audio(audio, extra, os.path.join(workdir, f"v{idx}_all.mp3"))
            text = f"{text} {follow_text}"
        bg = {"kind": "none"}
        if not offline:
            clip = os.path.join(workdir, f"bg{idx}.mp4")
            got = stock.find_clip(query, clip, render.duration(audio) + 1, used_ids)
            if got:
                bg = {"kind": got["kind"], "path": got.get("path", clip)}
                credits.append(got)
        pose = poses.get("hook" if kind == "hook" else "outro" if kind == "outro" else item["rank"])
        add({"kind": kind, "text": text, "item": item, "audio": audio, "bg": bg, "mascot": pose,
             "follow_at": follow_at})
        print(f"  segment {idx}: {kind} {item['name'] if item else ''}")

    if outro:
        segments.append(render.normalize_host_clip(outro, workdir, idx)); idx += 1

    music = sorted(glob.glob(os.path.join(ROOT, "assets", "music", "*.mp3")))
    os.makedirs(OUT, exist_ok=True)
    out_path = os.path.join(OUT, "video.mp4")
    total = render.finish(segments, workdir, out_path, accent,
                          random.choice(music) if music else None, config["music_volume"])
    return out_path, total, credits, bool(intro or outro) or voice.ENGINE == "chatterbox"


def next_monday(today=None):
    today = today or (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()  # Manila date
    return today + dt.timedelta(days=(7 - today.weekday()) % 7 or 7)


def next_sunday(today=None):
    """Weeks run Sunday → Saturday (Emilio's schedule)."""
    today = today or (dt.datetime.utcnow() + dt.timedelta(hours=8)).date()
    return today + dt.timedelta(days=(6 - today.weekday()) % 7 or 7)


KINDS = {
    "ranking": {"queue": "queue.json", "slot_key": "posting_slot", "slot": "8:00 AM", "label": "Ranking"},
    "quiz": {"queue": "quiz_queue.json", "slot_key": "quiz_slot", "slot": "9:00 PM", "label": "Quiz"},
}


def slot24(slot):
    return dt.datetime.strptime(slot, "%I:%M %p").strftime("%H%M")


def sheet_block(day, slot, label, post, file_name):
    return "\n".join([
        f"## {day:%a %b %d} · {slot} · {label} — {post['title']}",
        f"File: `{file_name}` · {post.get('duration', '?')} s",
        "", "**Title**", "```", post["title"], "```",
        "**Description**", "```", post["description"], "```",
        "**Tags**", "```", ", ".join(post["tags"]), "```",
        f"Not made for kids · Altered/synthetic content: **{'Yes' if post['ai_generated'] else 'No'}** · "
        f"Schedule: **{day:%a %b %d}, {slot}** (Manila)",
        "",
    ])


def merge_sheet():
    """Combine every rendered video's block into one upload sheet, in posting order."""
    batch_dir = os.path.join(OUT, "batch")
    posts = [load(p, {}) for p in glob.glob(os.path.join(batch_dir, "*.json"))]
    posts.sort(key=lambda p: (p["post_on"], slot24(p["slot"])))
    if not posts:
        sys.exit("no videos were made")
    first, last = posts[0]["post_on"], posts[-1]["post_on"]
    fmt = lambda d: dt.date.fromisoformat(d).strftime("%a %b %d")
    lines = [f"# Bojji (@bojjiii) — {fmt(first)} to {fmt(last)}", "",
             "Upload each file in YouTube Studio **on the Bojji channel** and set Visibility → Schedule "
             "for its date and time (Manila).", ""]
    for f in sorted(glob.glob(os.path.join(OUT, "failed_*.txt"))):
        lines.append("⚠️ " + open(f).read().strip() + "\n")
    lines += [p["sheet_block"] for p in posts]
    with open(os.path.join(OUT, "upload_sheet.md"), "w") as fh:
        fh.write("\n".join(lines))
    print(f"Upload sheet: {len(posts)} videos")


def setup_voice(config):
    voice.ENGINE = config.get("tts_engine", "edge")
    voice.CLONE.update(config.get("voice_clone", {}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="test render: no keys, no internet")
    ap.add_argument("--kind", choices=list(KINDS), default="ranking")
    ap.add_argument("--count", type=int, default=1, help="how many videos to make in this run")
    ap.add_argument("--start", help="first posting date YYYY-MM-DD (default: next Monday)")
    ap.add_argument("--merge", action="store_true", help="only build out/upload_sheet.md from rendered videos")
    ap.add_argument("--write", type=int, help="only write ranking scripts until the queue has this many (Gemini)")
    args = ap.parse_args()
    if args.merge:
        return merge_sheet()

    config = load(os.path.join(ROOT, "config.json"), {})
    setup_voice(config)
    kind = KINDS[args.kind]
    slot = config.get(kind["slot_key"], kind["slot"])
    qpath, hpath = os.path.join(STATE, kind["queue"]), os.path.join(STATE, "history.json")
    queue, history = load(qpath, []), load(hpath, [])
    first = dt.date.fromisoformat(args.start) if args.start else next_sunday()

    if args.write:
        before = len(queue)
        try:
            refill(config, queue, history, args.write)
        except Exception as e:
            print(f"::error::Script writer failed: {str(e)[:900]}")
        save(qpath, queue)
        print(f"::notice::Queue {before} -> {len(queue)} scripts")
        return

    if args.count < 1:
        return
    if not queue:
        print(f"::warning::{kind['label']} queue is empty — nothing to make")
        return

    batch_dir = os.path.join(OUT, "batch")
    os.makedirs(batch_dir, exist_ok=True)
    done, failed = [], []
    for n in range(args.count):
        if not queue:
            break
        script = queue[0]
        day = first + dt.timedelta(days=n)
        print(f"[{kind['label']} {n + 1}/{args.count}] {day} — {script['title']}")
        workdir = os.path.join(ROOT, "work")
        shutil.rmtree(workdir, ignore_errors=True)
        os.makedirs(workdir)
        try:
            video, total, credits, synthetic = make_video(script, config, args.offline, workdir)
        except Exception as e:
            print(f"::warning::Couldn't make '{script['title']}': {str(e)[:500]}")
            failed.append(script["title"])
            queue.append(queue.pop(0))  # try it again next batch
            continue
        post = build_post(script, config, credits, synthetic)
        post["duration"] = round(total, 1)
        name = f"{day:%m%d}_{day:%a}_{slot24(slot)}_{args.kind}_{script['id']}.mp4"
        shutil.move(video, os.path.join(batch_dir, name))
        save(os.path.join(batch_dir, name.replace(".mp4", ".json")),
             dict(post, post_on=str(day), slot=slot, kind=args.kind,
                  sheet_block=sheet_block(day, slot, kind["label"], post, name)))
        done.append(name)
        queue.pop(0)
        history.append({"id": script["id"], "title": script["title"], "topic": script.get("topic", ""),
                        "kind": args.kind, "rendered_on": dt.date.today().isoformat(), "post_on": str(day),
                        "status": "rendered"})
    if failed:
        with open(os.path.join(OUT, f"failed_{args.kind}.txt"), "w") as fh:
            fh.write(f"Couldn't make {kind['label'].lower()}: {', '.join(failed)} (kept for next week).")
    print(f"Done: {len(done)} {args.kind} videos, {len(failed)} failed")
    if args.offline:
        return
    if config.get("auto_write") and args.kind == "ranking":
        try:
            refill(config, queue, history, config.get("queue_min_size", 8))
        except Exception as e:
            print(f"::warning::Couldn't top up the script queue: {str(e)[:900]}")
    save(qpath, queue)
    save(hpath, history)


if __name__ == "__main__":
    main()
