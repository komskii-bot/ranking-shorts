"""Generates the channel's sound effects from scratch (royalty-free, no downloads).
Run: python tools/make_sfx.py  → assets/sfx/*.wav"""
import os
import wave

import numpy as np
from scipy import signal

SR = 44100
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "sfx")
rng = np.random.default_rng(7)


def save(name, stereo, peak_db=-4.0):
    stereo = stereo / (np.abs(stereo).max() + 1e-9) * 10 ** (peak_db / 20)
    data = (np.clip(stereo, -1, 1) * 32767).astype(np.int16)
    with wave.open(os.path.join(OUT, name), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(data.reshape(-1).tobytes())


def env(n, attack, release_pow=2.0):
    t = np.linspace(0, 1, n)
    a = np.clip(t / attack, 0, 1)
    return a * (1 - t) ** release_pow


def whoosh(dur=0.5):
    """Noise that sweeps from low to high (crossfaded bands, no clicks), panned left to right."""
    n = int(SR * dur)
    noise = rng.standard_normal(n + SR // 10)
    bands = [(250, 1000), (900, 3000), (2800, 8000)]
    filt = [signal.sosfiltfilt(signal.butter(2, b, btype="band", fs=SR, output="sos"), noise)[-n:] for b in bands]
    t = np.linspace(0, 1, n)
    w = [np.clip(1 - 2 * t, 0, 1), 1 - np.abs(2 * t - 1), np.clip(2 * t - 1, 0, 1)]
    out = sum(f * wi for f, wi in zip(filt, w))
    out *= np.sin(np.pi * t) ** 1.5
    return np.stack([out * (1 - 0.6 * t), out * (0.4 + 0.6 * t)], axis=1)


def ding(dur=1.3):
    n = int(SR * dur)
    t = np.arange(n) / SR
    tone = (np.sin(2 * np.pi * 1318.5 * t) + 0.45 * np.sin(2 * np.pi * 2637 * t)
            + 0.2 * np.sin(2 * np.pi * 3955.5 * t) + 0.25 * np.sin(2 * np.pi * 1976 * t))
    tone *= np.exp(-t * 3.2) * np.clip(t / 0.004, 0, 1)
    return np.stack([tone, tone], axis=1)


def boom(dur=0.9):
    n = int(SR * dur)
    t = np.arange(n) / SR
    f = 95 * np.exp(-t * 4) + 38  # pitch drop
    sub = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 4.5)
    hit = signal.sosfilt(signal.butter(2, 2500, fs=SR, output="sos"), rng.standard_normal(n)) * np.exp(-t * 30)
    x = sub + 0.5 * hit
    return np.stack([x, x], axis=1)


def pop(dur=0.16):
    n = int(SR * dur)
    t = np.arange(n) / SR
    f = 500 + 1400 * (t / dur)
    x = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 28) * np.clip(t / 0.002, 0, 1)
    return np.stack([x, x], axis=1)


def tick(dur=0.07):
    n = int(SR * dur)
    t = np.arange(n) / SR
    x = (np.sin(2 * np.pi * 2200 * t) + 0.6 * np.sin(2 * np.pi * 3300 * t)) * np.exp(-t * 90)
    return np.stack([x, x], axis=1)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    save("whoosh.wav", whoosh(), -6)
    save("ding.wav", ding(), -5)
    save("boom.wav", boom(), -3)
    save("pop.wav", pop(), -7)
    save("tick.wav", tick(), -9)
    print("saved to", OUT)
