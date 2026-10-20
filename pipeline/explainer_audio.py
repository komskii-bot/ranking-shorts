"""Voice-over handling for long explainers: clean the narrator's recordings and
find when each script beat (and word) is spoken.

Timing uses faster-whisper word timestamps when the package is available
(GitHub Actions installs it). Offline it falls back to pause detection, which is
good enough for previews.
"""
import difflib
import re
import subprocess
import wave

import numpy as np

SR = 48000
LOG = []  # how each part was timed (written to timing.json)
HEARD = {}  # wav path -> [(word, start, end)] as transcribed
CUTS = {}   # wav path -> [{"start", "end", "text"}] retakes / false starts to remove
CLEAN = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15,"
         "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.25,areverse,"
         "highpass=f=80,afftdn=nf=-28:tn=1,"
         "acompressor=threshold=-22dB:ratio=3:attack=5:release=90:makeup=3,"
         "alimiter=limit=0.89")


def clean(src, out_wav):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-af", CLEAN,
                    "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", out_wav], check=True)
    return read_wav(out_wav)


def read_wav(path):
    with wave.open(path) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    return data


def write_wav(path, samples):
    s = np.clip(samples, -1, 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((s * 32767).astype(np.int16).tobytes())


# ---------------------------------------------------------------- word timing

def _norm(word):
    w = word.lower().replace("’", "'")
    w = re.sub(r"[^a-z0-9']", "", w)
    return {"ok": "okay"}.get(w, w)


def script_words(text):
    return [w for w in re.findall(r"[A-Za-z0-9'’\-\.]+[,.!?]?", text)]


def _whisper_words(wav_path):
    from faster_whisper import WhisperModel  # noqa: only on GitHub
    model = _whisper_words.model = getattr(_whisper_words, "model", None) or \
        WhisperModel("small.en", device="cpu", compute_type="int8")
    # decode ourselves (16 kHz mono float) so faster-whisper never touches PyAV
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", wav_path, "-ac", "1", "-ar", "16000",
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    audio = np.frombuffer(raw, np.float32)
    segs, _ = model.transcribe(audio, word_timestamps=True, vad_filter=False, beam_size=5,
                               condition_on_previous_text=False,
                               initial_prompt="Bojji Explains: data centers, Arizona, Nevada, Abu Dhabi, Stargate UAE, Ceres, Tucson, Project Blue.")
    out = []
    for s in segs:
        for w in s.words or []:
            out.append((w.word.strip(), w.start, w.end))
    return out


def _whisper_dense(wav_path, win=10.0, hop=7.0):
    """Transcribe short overlapping windows independently. Whisper on long audio tends to
    drop repeated phrases (it treats them as hallucinations); short windows keep them."""
    from faster_whisper import WhisperModel  # noqa: only on GitHub
    model = _whisper_words.model = getattr(_whisper_words, "model", None) or \
        WhisperModel("small.en", device="cpu", compute_type="int8")
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", wav_path, "-ac", "1", "-ar", "16000",
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    audio = np.frombuffer(raw, np.float32)
    total = len(audio) / 16000
    out, ws = [], 0.0
    margin = (win - hop) / 2
    while ws < total:
        we = min(total, ws + win)
        lo = 0.0 if ws == 0 else ws + margin
        hi = total if we >= total else we - margin
        chunk = audio[int(ws * 16000): int(we * 16000)]
        segs, _ = model.transcribe(chunk, word_timestamps=True, vad_filter=False, beam_size=5,
                                   condition_on_previous_text=False, without_timestamps=False)
        for sg in segs:
            for w in sg.words or []:
                mid = ws + (w.start + w.end) / 2
                if lo <= mid < hi and w.word.strip():
                    out.append((w.word.strip(), round(ws + w.start, 3), round(ws + w.end, 3)))
        if we >= total:
            break
        ws += hop
    return out


def _pauses(samples, min_gap=0.22):
    """Return (start, end) of pauses longer than min_gap seconds."""
    hop = int(SR * 0.01)
    n = len(samples) // hop
    rms = np.sqrt(np.mean(samples[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    thresh = max(np.percentile(db, 20) + 6, np.percentile(db, 90) - 30)
    quiet = db < thresh
    gaps, start = [], None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) * 0.01 >= min_gap:
                gaps.append((start * 0.01, i * 0.01))
            start = None
    return gaps


def _fallback_times(samples, words):
    """Spread words over the speech, snapping sentence ends to real pauses."""
    total = len(samples) / SR
    gaps = _pauses(samples)
    speech = total - sum(b - a for a, b in gaps)
    weights = np.array([len(_norm(w)) + 2 for w in words], dtype=float)
    cum = np.concatenate([[0], np.cumsum(weights)]) / weights.sum()
    # map "speech time" to real time by skipping pauses
    def real(st):
        t, acc = 0.0, 0.0
        for a, b in gaps:
            if acc + (a - t) >= st:
                return t + (st - acc)
            acc += a - t
            t = b
        return t + (st - acc)
    times = [real(c * speech) for c in cum]
    # snap sentence boundaries to the nearest pause end
    for i, w in enumerate(words):
        if w[-1:] in ".?!" and i + 1 < len(words) and gaps:
            g = min(gaps, key=lambda g: abs(g[1] - times[i + 1]))
            if abs(g[1] - times[i + 1]) < 1.5:
                times[i + 1] = g[1]
    out = [(words[i], times[i], times[i + 1]) for i in range(len(words))]
    for i in range(1, len(out)):  # keep monotonic
        if out[i][1] < out[i - 1][1]:
            out[i] = (out[i][0], out[i - 1][1], max(out[i][2], out[i - 1][1]))
    return out


META = {"repeat", "again", "sorry", "redo", "retake", "oops"}


def _num_norm(w):
    """Make '945', 'nine hundred forty-five' etc. comparable enough: drop pure digits/number words."""
    return w


def edit_and_time(words, heard, total):
    """Sentence by sentence, keep the LAST good take of each script sentence; everything
    between kept takes that is a false start, a repeated take or retake chatter is cut.
    Returns (cuts, [(word, start, end)] for every script word, in uncut-recording time)."""
    sn = [_norm(w) for w in words]
    hn = [_norm(w) for w, _, _ in heard]
    # sentence spans in the script
    spans, s0 = [], 0
    for i, w in enumerate(words):
        if w[-1:] in ".?!" or i == len(words) - 1:
            spans.append((s0, i + 1)); s0 = i + 1
    cuts, times = [], [None] * len(words)
    p = 0                      # heard index after the last kept sentence
    prev_end_t = 0.0
    for a, b in spans:
        target = sn[a:b]
        n = b - a
        limit = min(len(hn), p + 4 * n + 60)
        cands = []
        for st in range(p, limit):
            for ln in (n - 1, n, n + 1, n + 2):
                if ln <= 0 or st + ln > len(hn):
                    continue
                r = difflib.SequenceMatcher(None, target, hn[st:st + ln], autojunk=False).ratio()
                cands.append((r, st, ln))
        if not cands:
            continue
        best = max(c[0] for c in cands)
        if best < 0.45:
            LOG.append(f"sentence not found: {' '.join(words[a:b])[:80]}")
            continue
        # the latest take that scores close to the best one wins; within that take,
        # use its best-scoring window (so we don't start a word late)
        good = [c for c in cands if c[0] >= max(0.6, best - 0.12)]
        last_st = max(c[1] for c in good)
        cluster = [c for c in good if c[1] >= last_st - max(2, n // 2)]
        r, st, ln = max(cluster, key=lambda c: (round(c[0], 3), -c[1]))
        # region between previous kept sentence and this take
        gap = list(range(p, st))
        if gap:
            gw = [hn[k] for k in gap if hn[k]]
            in_sent = sum(1 for w in gw if w in set(target)) / max(1, len(gw))
            meta = any(w in META for w in gw)
            earlier_take = any(c[1] < st and c[1] + c[2] <= st + 1 and c[0] >= 0.5 for c in cands)
            text = " ".join(heard[k][0] for k in gap)
            if gw and (in_sent >= 0.5 or meta or earlier_take):
                # leave ~0.45 s of natural pause across the join
                c0 = heard[gap[0] - 1][2] + 0.25 if gap[0] > 0 else max(0.0, heard[gap[0]][1] - 0.15)
                c1 = heard[st][1] - 0.2
                if c1 - c0 > 0.15:
                    cuts.append({"start": round(c0, 3), "end": round(c1, 3), "text": text})
            elif gw:
                LOG.append(f"kept extra words: {text[:100]}")
        # word times inside the kept take
        sm = difflib.SequenceMatcher(None, target, hn[st:st + ln], autojunk=False)
        last_b = -1
        for blk in sm.get_matching_blocks():
            for k in range(blk.size):
                _, t0, t1 = heard[st + blk.b + k]
                times[a + blk.a + k] = (t0, t1)
                last_b = max(last_b, blk.b + k)
        if times[a] is None:
            times[a] = (heard[st][1], heard[st][2])
        # don't let a padded window swallow the first word of the next sentence
        p = st + (last_b + 1 if last_b >= 0 else ln)
    known = [i for i, x in enumerate(times) if x]
    for i in range(len(times)):
        if times[i] is None:
            prev = max([k for k in known if k < i], default=None)
            nxt = min([k for k in known if k > i], default=None)
            pt = times[prev][1] if prev is not None else 0.0
            nt = times[nxt][0] if nxt is not None else total
            lo = prev if prev is not None else -1
            hi = nxt if nxt is not None else len(times)
            s_ = pt + (nt - pt) * (i - lo) / (hi - lo)
            times[i] = (s_, s_ + (nt - pt) / (hi - lo))
    LOG.append(f"whisper: matched {len(known)}/{len(words)}")
    return cuts, [(words[i], times[i][0], times[i][1]) for i in range(len(words))]


def mumble_cuts(samples, timed, cuts, min_gap=1.5):
    """Untranscribed voice inside a long pause between two script words (a muttered
    false start) -> cut it, leaving a natural pause on each side."""
    hop = int(SR * 0.02)
    n = len(samples) // hop
    db = 20 * np.log10(np.sqrt((samples[: n * hop].reshape(n, hop) ** 2).mean(1) + 1e-12))
    thr = np.percentile(db, 90) - 22
    out = []
    for (_, _, e1), (_, s2, _) in zip(timed, timed[1:]):
        if s2 - e1 < min_gap or any(c["start"] < s2 and c["end"] > e1 for c in cuts):
            continue
        seg = db[int((e1 + 0.15) / 0.02): int((s2 - 0.15) / 0.02)] > thr
        if seg.sum() * 0.02 >= 0.3:
            out.append({"start": round(e1 + 0.35, 3), "end": round(s2 - 0.3, 3), "text": "(mumbled restart in a pause)"})
    return out


ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _n2w(n):
    if n < 20:
        return [ONES[n]]
    if n < 100:
        return [TENS[n // 10]] + ([ONES[n % 10]] if n % 10 else [])
    if n < 1000:
        return [ONES[n // 100], "hundred"] + (_n2w(n % 100) if n % 100 else [])
    if n < 1_000_000:
        return _n2w(n // 1000) + ["thousand"] + (_n2w(n % 1000) if n % 1000 else [])
    return _n2w(n // 1_000_000) + ["million"] + (_n2w(n % 1_000_000) if n % 1_000_000 else [])


def _expand(tok):
    """'2,000' -> two thousand, '2033' -> twenty thirty three, '7.2' -> seven point two, '100%' -> ... percent."""
    t = tok.lower().replace("\u2019", "'").strip(".,!?;:\"'()")
    if "/" in t and re.fullmatch(r"[\d/]+", t):
        return sum((_expand(x) for x in t.split("/") if x), [])
    pct = t.endswith("%")
    t = t.strip("%")
    if re.fullmatch(r"\d{1,3}(,\d{3})+|\d+", t):
        n = int(t.replace(",", ""))
        if 1900 <= n <= 2099 and "," not in t:
            words = _n2w(n // 100) + (_n2w(n % 100) if n % 100 >= 10 else (["oh"] + _n2w(n % 100) if n % 100 else ["hundred"]))
            if n % 100 < 10 and n % 100:
                words = _n2w(n // 100) + _n2w(n % 100)  # 2005 -> twenty five-ish; rare
        else:
            words = _n2w(n)
    elif re.fullmatch(r"\d+\.\d+", t):
        a, b = t.split(".")
        words = _n2w(int(a)) + ["point"] + [ONES[int(c)] for c in b]
    else:
        words = [w for w in re.split(r"[-\s/]+", t) if w]
        words = [re.sub(r"[^a-z0-9']", "", w) for w in words]
        words = [{"ok": "okay", "a": "a"}.get(w, w) for w in words if w]
    return words + (["percent"] if pct else [])


def tok_script(words):
    out = []
    for i, w in enumerate(words):
        for t in _expand(w):
            out.append((t, i))
    return out


def tok_heard(heard):
    # glue whisper's split numbers back together: "2" ",000" / "7" ".2" / "100" "%"
    merged = []
    for w, a, b in heard:
        ws = w.strip()
        if merged and re.fullmatch(r"[,.]\d+%?|%|-\d+\.?", ws) and re.search(r"\d$", merged[-1][0].rstrip(".,")):
            pw, pa, _ = merged[-1]
            joined = pw.rstrip(".,") + (ws if not ws.startswith("-") else " " + ws[1:])
            merged[-1] = (joined, pa, b)
        else:
            merged.append((ws, a, b))
    out = []
    for hi, (w, a, b) in enumerate(merged):
        parts = []
        for piece in w.split(" "):
            parts += _expand(piece.strip(".,!?\"'"))
        parts = parts or [""]
        d = (b - a) / len(parts)
        for k, t in enumerate(parts):
            out.append((t, a + k * d, a + (k + 1) * d, hi))
    return merged, out


def dp_align(S, Hh):
    """Global alignment, script tokens S vs heard tokens Hh. Backtrace from the end prefers
    matches, so for a repeated phrase the LATER take is the one kept."""
    n, m = len(S), len(Hh)
    INF = 10 ** 9
    D = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        D[i][0] = i
    for j in range(1, m + 1):
        D[0][j] = j
    for i in range(1, n + 1):
        si = S[i - 1]; row, prev = D[i], D[i - 1]
        for j in range(1, m + 1):
            c = 0 if si == Hh[j - 1] else 1
            row[j] = min(prev[j - 1] + c, prev[j] + 1, row[j - 1] + 1)
    i, j, pairs = n, m, []
    while i > 0 or j > 0:
        if i > 0 and j > 0 and S[i - 1] == Hh[j - 1] and D[i][j] == D[i - 1][j - 1]:
            pairs.append((i - 1, j - 1, "m")); i -= 1; j -= 1
        elif j > 0 and D[i][j] == D[i][j - 1] + 1:
            pairs.append((None, j - 1, "h")); j -= 1          # heard word not in the script
        elif i > 0 and j > 0 and D[i][j] == D[i - 1][j - 1] + 1:
            pairs.append((i - 1, j - 1, "s")); i -= 1; j -= 1   # spoken differently
        else:
            pairs.append((i - 1, None, "d")); i -= 1          # script word not heard
    return pairs[::-1]


def edit_dp(words, heard, total):
    """Word-level retake removal + timing. Returns (cuts, timed script words)."""
    st = tok_script(words)
    merged, ht = tok_heard(heard)
    S = [t for t, _ in st]; Hh = [t for t, *_ in ht]
    pairs = dp_align(S, Hh)
    used_h = set()          # heard tokens that belong to the kept speech
    times = [None] * len(words)
    for si, hj, op in pairs:
        if hj is not None and op in "ms":
            used_h.add(hj)
            wi = st[si][1]
            _, a, b, _ = ht[hj]
            if times[wi] is None:
                times[wi] = [a, b]
            else:
                times[wi][1] = b
    # heard WORDS whose tokens were all unused -> candidate cuts (runs)
    word_used = [False] * len(merged)
    word_pos = [None] * len(merged)   # script token index nearest to this heard word
    last_si = 0
    for si, hj, op in pairs:
        if si is not None:
            last_si = si
        if hj is not None:
            hw = ht[hj][3]
            word_pos[hw] = last_si
            if hj in used_h:
                word_used[hw] = True
    cuts, k = [], 0
    while k < len(merged):
        if word_used[k]:
            k += 1; continue
        e = k
        while e + 1 < len(merged) and not word_used[e + 1]:
            e += 1
        run = [t for t in sum((_expand(merged[q][0]) for q in range(k, e + 1)), []) if t]
        pos = word_pos[k] or 0
        near = set(S[max(0, pos - 25): pos + 40])
        overlap = sum(1 for t in run if t in near) / max(1, len(run))
        meta = any(t in META for t in run)
        text = " ".join(merged[q][0] for q in range(k, e + 1))
        if run and (overlap >= 0.5 or meta) and (len(run) >= 2 or meta or run[0] in near):
            pe = merged[k - 1][2] if k > 0 else 0.0
            ns = merged[e + 1][1] if e + 1 < len(merged) else total
            rs, re_ = merged[k][1], merged[e][2]
            boundary = (k > 0 and merged[k - 1][0][-1:] in ".?!") or e + 1 >= len(merged)
            keep_before = min(rs - pe, 0.25 if boundary else 0.06)
            keep_after = min(ns - re_, 0.2 if boundary else 0.05)
            c0, c1 = pe + max(0.0, keep_before), ns - max(0.0, keep_after)
            if c1 - c0 > 0.12 and c1 - c0 <= 40:
                cuts.append({"start": round(c0, 3), "end": round(c1, 3), "text": text})
            elif c1 - c0 > 40:
                LOG.append(f"NOT cut (too long): {text[:100]}")
        elif run:
            LOG.append(f"kept extra words: {text[:100]}")
        k = e + 1
    known = [i for i, x in enumerate(times) if x]
    for i in range(len(times)):
        if times[i] is None:
            prev = max([q for q in known if q < i], default=None)
            nxt = min([q for q in known if q > i], default=None)
            pt = times[prev][1] if prev is not None else 0.0
            nt = times[nxt][0] if nxt is not None else total
            lo = prev if prev is not None else -1
            hi = nxt if nxt is not None else len(times)
            s_ = pt + (nt - pt) * (i - lo) / (hi - lo)
            times[i] = [s_, s_ + (nt - pt) / (hi - lo)]
    LOG.append(f"whisper: matched {len(known)}/{len(words)}")
    return cuts, [(words[i], times[i][0], times[i][1]) for i in range(len(words))]


def apply_cuts(samples, cuts, fade=0.03):
    """Remove cut ranges with short fades so the joins don't click."""
    if not cuts:
        return samples
    keep, last = [], 0
    nf = int(fade * SR)
    ramp = np.linspace(0, 1, nf, dtype=np.float32)
    for c in sorted(cuts, key=lambda c: c["start"]):
        a, b = int(c["start"] * SR), int(c["end"] * SR)
        seg = samples[last:a].copy()
        if len(seg) > nf:
            seg[-nf:] *= ramp[::-1]
        keep.append(seg)
        last = b
    tail = samples[last:].copy()
    if len(tail) > nf:
        tail[:nf] *= ramp
    keep.append(tail)
    for k in range(1, len(keep)):
        if len(keep[k]) > nf:
            keep[k][:nf] *= ramp
    return np.concatenate(keep)


def shift_time(t, cuts):
    """Time in the uncut recording -> time after cuts are removed."""
    out = t
    for c in cuts:
        if t > c["start"]:
            out -= min(t, c["end"]) - c["start"]
    return out


def align(wav_path, samples, beats):
    """Attach t0/t1 (seconds in this part) and per-word timings to each beat."""
    words, owner = [], []
    for bi, b in enumerate(beats):
        for w in script_words(b["say"]):
            words.append(w); owner.append(bi)
    timed = None
    try:
        heard = _whisper_dense(wav_path)
        print(f"    whisper heard {len(heard)} words for {len(words)} script words")
        HEARD[wav_path] = heard
        cuts, timed = edit_dp(words, heard, len(samples) / SR)
        CUTS[wav_path] = cuts
        for c in cuts:
            print(f"    cut {c['start']:.2f}-{c['end']:.2f}: {c['text']}")
    except Exception as e:
        print(f"    word timing from pauses ({e.__class__.__name__}: {e})")
        LOG.append(f"pauses: {e.__class__.__name__}: {str(e)[:300]}")
        timed = _fallback_times(samples, words)
    total = len(samples) / SR
    for bi, b in enumerate(beats):
        ws = [timed[i] for i in range(len(words)) if owner[i] == bi]
        b["words"] = ws
        b["t0"] = 0.0 if bi == 0 else max(0.0, ws[0][1] - 0.12)
    for bi, b in enumerate(beats):
        b["t1"] = beats[bi + 1]["t0"] if bi + 1 < len(beats) else total
        if b["t1"] <= b["t0"]:
            b["t1"] = b["t0"] + 0.5
    return beats
