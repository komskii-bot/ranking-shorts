"""Free text-to-speech. edge-tts (Microsoft neural voices) in production;
espeak-ng only for offline test renders."""
import asyncio
import os
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FALLBACK_VOICE = "en-US-GuyNeural"

# Emilio's cloned voice (Chatterbox, MIT licence). main.py sets these from config.json.
ENGINE = "edge"          # "edge" or "chatterbox"
CLONE = {"ref": "assets/voice/emilio_ref.wav", "exaggeration": 0.5, "cfg_weight": 0.5}
_model = None


def _clone(text, out_path):
    global _model
    import torchaudio as ta
    from chatterbox.tts import ChatterboxTTS
    if _model is None:
        _model = ChatterboxTTS.from_pretrained(device="cpu")
    ref = CLONE["ref"] if os.path.isabs(CLONE["ref"]) else os.path.join(ROOT, CLONE["ref"])
    wav = _model.generate(text, audio_prompt_path=ref, exaggeration=CLONE["exaggeration"],
                          cfg_weight=CLONE["cfg_weight"])
    tmp = out_path.rsplit(".", 1)[0] + "_clone.wav"
    ta.save(tmp, wav, _model.sr)
    # trim the model's leading/trailing silence so the pacing stays tight
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp, "-af",
                    "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
                    "silenceremove=start_periods=1:start_threshold=-45dB,areverse", out_path], check=True)
    return out_path


async def _edge(text, out_path, voice, rate, pitch="+0Hz"):
    import edge_tts

    await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(out_path)


def speak(text, out_path, voice, rate="+0%", offline=False, pitch="+0Hz"):
    if offline:
        if not shutil.which("espeak-ng"):  # silent placeholder sized like speech
            secs = max(1.5, len(text.split()) / 2.7)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "anullsrc=r=44100:cl=stereo", "-t", f"{secs:.2f}", out_path], check=True)
            return out_path
        wav = out_path.rsplit(".", 1)[0] + ".wav"
        subprocess.run(["espeak-ng", "-s", "175", "-w", wav, text], check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, out_path], check=True)
        return out_path
    last = None
    if ENGINE == "chatterbox":
        try:
            return _clone(text, out_path)
        except Exception as e:  # never lose a video over the clone: fall back to the AI voice
            print(f"::warning::voice clone failed ({e.__class__.__name__}: {e}); using edge-tts")
    for v in (voice, FALLBACK_VOICE):
        for _ in range(3):
            try:
                asyncio.run(_edge(text, out_path, v, rate, pitch))
                return out_path
            except Exception as e:  # network hiccup or unknown voice
                last = e
    raise RuntimeError(f"Text-to-speech failed: {last}")
