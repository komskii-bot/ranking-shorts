"""Render one sample Top 10 Short end-to-end (stock footage + configured voice) without touching the queue."""
import json, os, shutil, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import main as M
cfg = json.load(open("config.json")); M.setup_voice(cfg)
names = [("Malaysia", "190 destinations"), ("Belgium", "190 destinations"), ("Austria", "191 destinations"),
         ("Netherlands", "191 destinations"), ("Denmark", "192 destinations"), ("France", "192 destinations"),
         ("Germany", "192 destinations"), ("South Korea", "193 destinations"), ("Japan", "193 destinations"),
         ("Singapore", "195 destinations")]
items = [{"rank": 10 - i, "name": n, "stat": st, "line": f"{n} gets you into {st.split()[0]} destinations visa-free. Not bad!",
          "search": f"{n.lower()} city"} for i, (n, st) in enumerate(names)]
sc = {"id": "smoke-test-passports", "title": "Top 10 Countries With the Strongest Passports (SAMPLE)",
      "hook": "One passport opens almost every door on Earth.", "items": items,
      "outro": "Which country surprised you the most?", "description": "sample", "tags": ["sample"],
      "highlight": ["Strongest", "Passports"], "highlight_colors": ["#FFD23F", "#1DE9D6"], "hook_style": "guess"}
os.makedirs("out/smoke", exist_ok=True)
out, total, _, synth = M.make_video(sc, cfg, False, "out/smoke/work")
shutil.copy(out, "out/smoke/sample_top10.mp4")
open("out/smoke/info.txt", "w").write(f"duration {total:.1f}s synthetic={synth}\nNOTE: placeholder numbers, not fact-checked\n")
print("done", total)
