"""Writes new ranking scripts with Gemini (free API key from Google AI Studio).

Uses Google Search grounding so current topics (AI, tech, business) get
up-to-date numbers. Each script stores the sources it used so you can audit it.
"""
import datetime as dt
import json
import os
import re
import time

import requests

SCHEMA_HINT = """Return ONLY a JSON object, no markdown fences, in exactly this shape:
{
  "id": "kebab-case-unique-id",
  "title": "YouTube title, max 70 characters, curiosity-driven",
  "hook": "One spoken sentence, max 16 words, that teases #1 without revealing it",
  "items": [
    {"rank": 5, "name": "Short name (max 4 words)", "stat": "Key number, max 4 words, e.g. 946 MW",
     "line": "One spoken sentence, 12-20 words, with the key number and one surprising detail",
     "emotion": "optional mascot reaction for this item: thinking, curious, excited, nervous, shocked or exclaim",
     "search": "2-4 word stock-footage search term that would show this item"},
    ... ranks 4, 3, 2, 1 in that order ...
  ],
  "outro": "One short spoken line asking viewers what they'd put at #1",
  "highlight": ["1-2 key phrases copied EXACTLY from the title to colour, e.g. the superlative and the subject: ['Deadliest', 'Animals']"],
  "highlight_colors": ["one hex colour per highlight phrase that FITS THE TOPIC and pops on dark video, from: #FF3B3B red, #1DE9D6 teal, #FF9F1C orange, #FFD23F yellow, #C86BFF purple, #4DA3FF blue, #3DDC84 green (e.g. danger=red, AI/tech=teal, space=purple, desert=orange, ocean=blue)"],
  "description": "2-3 sentence YouTube description stating the data source and date",
  "tags": ["8-12 lowercase tags"],
  "as_of": "Month YYYY the data refers to, or empty string for evergreen facts",
  "sources": ["URLs or publication names you relied on"]
}"""

RULES = """Rules:
- Exactly 5 items, ordered rank 5 down to rank 1.
- Every number must come from a reputable source (official stats, research groups, major publications). If sources disagree, say "about" or "estimated".
- For anything that changes over time, use the newest data you can find and set "as_of".
- Voice: a young, hype, charismatic narrator talking to a Gen Z audience. Punchy, playful, a bit cheeky ("no joke", "wild, right?", "let that sink in"), at most one slang touch per line, never cringe overload, never mean. Hooks should create curiosity in the first 2 seconds.
- Spoken lines must sound natural read aloud: no symbols, no parentheses, spell out units ("megawatts", "kilometers").
- Do NOT pick topics that require movie, TV, music or sports footage.
- Do NOT rank real private individuals; companies, products, countries and nature are fine.
- Keep it neutral: no political, religious or medical-advice topics."""


def _call_gemini(prompt, model, api_key, use_search=True):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.9},
    }
    if use_search:
        body["tools"] = [{"google_search": {}}]
    r = requests.post(url, params={"key": api_key}, json=body, timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"Gemini error {r.status_code}: {r.text[:500]}")
    data = r.json()
    cand = data["candidates"][0]
    text = "".join(p.get("text", "") for p in cand["content"]["parts"])
    grounded = []
    for chunk in cand.get("groundingMetadata", {}).get("groundingChunks", []) or []:
        web = chunk.get("web") or {}
        if web.get("title"):
            grounded.append(web["title"])
    return text, grounded


def _extract_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start : end + 1])


def validate(script):
    for key in ("id", "title", "hook", "items", "outro", "description", "tags"):
        if key not in script:
            raise ValueError(f"script missing '{key}'")
    items = script["items"]
    if len(items) != 5 or [i["rank"] for i in items] != [5, 4, 3, 2, 1]:
        raise ValueError("items must be ranks 5,4,3,2,1")
    for i in items:
        for key in ("name", "line", "search"):
            if not str(i.get(key, "")).strip():
                raise ValueError(f"item {i.get('rank')} missing '{key}'")
        i.setdefault("stat", "")
    script["title"] = script["title"][:95]
    script.setdefault("as_of", "")
    script.setdefault("sources", [])
    return script


def write_script(config, used_titles, wanted_topic=None):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set, so new scripts can't be written")
    today = dt.date.today().strftime("%B %d, %Y")
    avoid = "\n".join(f"- {t}" for t in used_titles[-80:]) or "- (none yet)"
    topic_line = (
        f"Topic to use: {wanted_topic}"
        if wanted_topic
        else f"Pick ONE fresh topic. {config['topic_mix']}"
    )
    prompt = (
        f"You write scripts for a YouTube Shorts channel that posts 'Top 5' ranking videos. "
        f"Today is {today}. Search the web for current data before writing.\n\n"
        f"{topic_line}\n\nTopics already used (do not repeat or closely copy):\n{avoid}\n\n"
        f"{RULES}\n\n{SCHEMA_HINT}"
    )
    last_err = None
    for attempt in range(3):
        try:
            text, grounded = _call_gemini(prompt, config["gemini_model"], api_key)
            script = validate(_extract_json(text))
            script["sources"] = list(dict.fromkeys(script["sources"] + grounded))[:10]
            script["written_by"] = config["gemini_model"]
            script["written_on"] = dt.date.today().isoformat()
            return script
        except Exception as e:  # retry on bad JSON / transient errors
            last_err = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Could not write a script after 3 tries: {last_err}")
