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
    return w


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


def find_retakes(script_norm, heard, total):
    """Heard words that aren't part of the kept take but repeat nearby script words
    (false starts, restarted sentences, stutters) -> time ranges to cut.
    The longest-match alignment naturally keeps the complete (usually last) take."""
    h = [_norm(w) for w, _, _ in heard]
    sm = difflib.SequenceMatcher(None, script_norm, h, autojunk=False)
    matched = {}
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            matched[blk.b + k] = blk.a + k
    cuts, i = [], 0
    while i < len(h):
        if i in matched:
            i += 1; continue
        j = i
        while j + 1 < len(h) and (j + 1) not in matched:
            j += 1
        run = [w for w in h[i:j + 1] if w]
        nxt = j + 1 if j + 1 < len(h) else None
        sn = matched[nxt] if nxt is not None else len(script_norm)
        window = set(script_norm[max(0, sn - 10): sn + 30])
        overlap = sum(1 for w in run if w in window) / max(1, len(run))
        start = (heard[i - 1][2] + 0.06) if i > 0 else max(0.0, heard[i][1] - 0.15)
        end = (heard[nxt][1] - 0.12) if nxt is not None else total
        text = " ".join(w for w, _, _ in heard[i:j + 1])
        if run and overlap >= 0.5 and end - start > 0.15:
            if end - start <= 25:
                cuts.append({"start": round(start, 3), "end": round(end, 3), "text": text})
            else:
                LOG.append(f"NOT cut (too long, {end - start:.1f}s): {text[:120]}")
        elif run:
            LOG.append(f"kept extra words: {text[:120]}")
        i = j + 1
    return cuts


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
        heard = _whisper_words(wav_path)
        print(f"    whisper heard {len(heard)} words for {len(words)} script words")
        a = [_norm(w) for w in words]
        HEARD[wav_path] = heard
        CUTS[wav_path] = find_retakes(a, heard, len(samples) / SR)
        for c in CUTS[wav_path]:
            print(f"    cut {c['start']:.2f}-{c['end']:.2f}: {c['text']}")
        h = [_norm(w) for w, _, _ in heard]
        sm = difflib.SequenceMatcher(None, a, h, autojunk=False)
        t = [None] * len(words)
        for blk in sm.get_matching_blocks():
            for k in range(blk.size):
                _, s, e = heard[blk.b + k]
                t[blk.a + k] = (s, e)
        known = [i for i, x in enumerate(t) if x]
        if len(known) < 0.4 * len(words):
            raise RuntimeError("too few words matched")
        total = len(samples) / SR
        for i in range(len(t)):  # interpolate gaps
            if t[i] is None:
                prev = max([k for k in known if k < i], default=None)
                nxt = min([k for k in known if k > i], default=None)
                p = t[prev][1] if prev is not None else 0.0
                n = t[nxt][0] if nxt is not None else total
                lo = prev if prev is not None else -1
                hi = nxt if nxt is not None else len(t)
                frac = (i - lo) / (hi - lo)
                s = p + (n - p) * frac
                t[i] = (s, s + (n - p) / (hi - lo))
        timed = [(words[i], t[i][0], t[i][1]) for i in range(len(words))]
        print(f"    matched {len(known)}/{len(words)} words")
        LOG.append(f"whisper: matched {len(known)}/{len(words)}")
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
