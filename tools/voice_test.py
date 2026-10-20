"""Voice samples for Emilio to compare (runs on GitHub Actions).
1) his cloned voice via Chatterbox (MIT), several energy settings
2) the current edge-tts voice, faster/higher/louder
"""
import asyncio, os, subprocess, sys, time

OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)
LINES = [
    "One passport opens almost every door on Earth. And we're back with another Top 10! Today we're ranking the countries with the strongest passports.",
    "Number three. Japan! Its citizens can travel to one hundred ninety two countries without a visa. Wild, right?",
    "Before the top five: if you're enjoying this, a like and a follow honestly mean the world to a small creator like me!",
]
FX = "highpass=f=70,equalizer=f=3500:t=q:w=1.2:g=3,acompressor=threshold=-20dB:ratio=3:attack=5:release=80:makeup=4,loudnorm=I=-12:TP=-1"
log = open(os.path.join(OUT, "timing.txt"), "w")


def post(src, dst):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-af", FX, "-ar", "44100", dst], check=True)


async def edge(text, path, rate, pitch, volume, voice):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume).save(path)

for name, rate, pitch, vol, voice in [("A_current_voice_louder_faster", "+20%", "+6Hz", "+20%", "en-US-BrianNeural"),
                                      ("B_alt_voice_andrew", "+18%", "+4Hz", "+20%", "en-US-AndrewNeural")]:
    raw = os.path.join(OUT, name + "_raw.mp3")
    asyncio.run(edge(" ".join(l.replace(".", "!", 1) if i else l for i, l in enumerate(LINES)), raw, rate, pitch, vol, voice))
    post(raw, os.path.join(OUT, name + ".mp3")); os.remove(raw)

try:
    import torch, torchaudio as ta
    from chatterbox.tts import ChatterboxTTS
    t0 = time.time()
    model = ChatterboxTTS.from_pretrained(device="cpu")
    log.write(f"model load {time.time() - t0:.0f}s\n")
    ref = sys.argv[2]
    for tag, ex, cfg in [("C_your_voice_energetic", 0.7, 0.3), ("D_your_voice_extra_hype", 0.95, 0.3), ("E_your_voice_natural", 0.5, 0.5)]:
        parts = []
        t0 = time.time()
        for i, line in enumerate(LINES):
            wav = model.generate(line, audio_prompt_path=ref, exaggeration=ex, cfg_weight=cfg)
            p = os.path.join(OUT, f"{tag}_{i}.wav"); ta.save(p, wav, model.sr); parts.append(p)
        secs = sum(float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                                        capture_output=True, text=True).stdout) for p in parts)
        log.write(f"{tag}: {secs:.1f}s of audio in {time.time() - t0:.0f}s\n"); log.flush()
        lst = os.path.join(OUT, f"{tag}.txt")
        open(lst, "w").write("".join(f"file '{os.path.abspath(p)}'\n" for p in parts))
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, os.path.join(OUT, tag + "_raw.wav")], check=True)
        post(os.path.join(OUT, tag + "_raw.wav"), os.path.join(OUT, tag + ".mp3"))
        for p in parts + [lst, os.path.join(OUT, tag + "_raw.wav")]:
            os.remove(p)
except Exception as e:
    log.write(f"chatterbox failed: {e.__class__.__name__}: {e}\n")
log.close()
