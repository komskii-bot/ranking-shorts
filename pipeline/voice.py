"""Free text-to-speech. edge-tts (Microsoft neural voices) in production;
espeak-ng only for offline test renders."""
import asyncio
import shutil
import subprocess

FALLBACK_VOICE = "en-US-GuyNeural"


async def _edge(text, out_path, voice, rate):
    import edge_tts

    await edge_tts.Communicate(text, voice, rate=rate).save(out_path)


def speak(text, out_path, voice, rate="+0%", offline=False):
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
    for v in (voice, FALLBACK_VOICE):
        for _ in range(3):
            try:
                asyncio.run(_edge(text, out_path, v, rate))
                return out_path
            except Exception as e:  # network hiccup or unknown voice
                last = e
    raise RuntimeError(f"Text-to-speech failed: {last}")
