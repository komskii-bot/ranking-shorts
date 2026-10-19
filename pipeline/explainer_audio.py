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
