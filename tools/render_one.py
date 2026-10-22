"""Render ONE script file (e.g. state/one_off/<id>.json) as a Short, without touching the weekly queue.

Usage: python tools/render_one.py state/one_off/<id>.json
Writes out/one/<id>.mp4 and out/one/upload_notes.md, and logs it in state/history.json.
"""
import datetime as dt, json, os, shutil, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import main as M
from pipeline.writer import validate

path = sys.argv[1]
script = validate(json.load(open(path)))
cfg = json.load(open("config.json"))
if os.environ.get("ANIMATED") == "1":  # per-run override from one-short.yml
    cfg["animated_mascot"] = True
M.setup_voice(cfg)
os.makedirs("out/one/work", exist_ok=True)
out, total, credits, synthetic = M.make_video(script, cfg, False, "out/one/work")
dest = f"out/one/{script['id']}.mp4"
shutil.copy(out, dest)
post = M.build_post(script, cfg, credits, synthetic)
post["duration"] = round(total, 1)
M.write_upload_notes(post, script)
shutil.copy(os.path.join(M.OUT, "upload_notes.md"), "out/one/upload_notes.md")
hist = json.load(open("state/history.json"))
hist.append({"id": script["id"], "title": script["title"], "topic": "", "kind": "one-off",
             "rendered_on": dt.date.today().isoformat(), "status": "rendered"})
json.dump(hist, open("state/history.json", "w"), indent=1, ensure_ascii=False)
script["status"] = "rendered"
json.dump(script, open(path, "w"), indent=1, ensure_ascii=False)
print("done", dest, f"{total:.1f}s")
