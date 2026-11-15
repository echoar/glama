#!/usr/bin/env python3
"""0-Token Local Voice-In (faster-whisper CPU int8) & Voice-Out (edge-tts + offline SAPI/say fallback -> Opus .ogg).
Usage:
  python voice_helper.py transcribe <audio_path>
  python voice_helper.py synthesize <text_or_file> <output_ogg_path>
"""
import asyncio
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_HF_CACHE = r"D:\.cache\huggingface" if os.path.exists("D:\\") else os.path.expanduser("~/.cache/huggingface")
os.environ.setdefault("HF_HOME", DEFAULT_HF_CACHE)
DEFAULT_VOICE = os.environ.get("GLAMA_TTS_VOICE", "en-US-ChristopherNeural")
DEFAULT_WHISPER_MODEL = os.environ.get("GLAMA_WHISPER_MODEL", "base.en")


def clean_text_for_speech(text: str) -> str:
    """Strip footers, markdown formatting, and URLs so TTS reads cleanly."""
    # Remove trailing [qwen3:8b | ...] status footer
    text = re.sub(r"\n*\[[a-zA-Z0-9_.:-]+\s*\|[^\]]*\]\s*$", "", text.strip())
    # Remove leading transcript echo if reading back a message
    text = re.sub(r"^🎙\s*\"[^\"]+\"\s*\n+", "", text)
    # Strip code blocks into brief spoken notice or inline text
    text = re.sub(r"```[a-zA-Z0-9_-]*\n([\s\S]*?)```", r"\1", text)
    # Remove markdown formatting chars
    text = re.sub(r"[*`#_~>]+", "", text)
    # Replace markdown links [label](url) with label
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Replace arrows
    text = text.replace("->", " to ").replace(">=", " greater than or equal to ").replace("<=", " less than or equal to ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:3500]


def transcode_to_opus_ogg(src_path: str, dst_ogg_path: str) -> None:
    """Convert any audio file (.mp3, .wav, etc.) to Telegram-compatible Opus .ogg via PyAV."""
    import av

    in_container = av.open(src_path)
    out_container = av.open(dst_ogg_path, mode="w", format="ogg")
    out_stream = out_container.add_stream("libopus", rate=48000)
    out_stream.layout = "mono"
    out_stream.bit_rate = 48000

    resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)

    for frame in in_container.decode(audio=0):
        frame.pts = None
        for rframe in resampler.resample(frame):
            for packet in out_stream.encode(rframe):
                out_container.mux(packet)

    for rframe in resampler.resample(None):
        for packet in out_stream.encode(rframe):
            out_container.mux(packet)

    for packet in out_stream.encode(None):
        out_container.mux(packet)

    out_container.close()
    in_container.close()


def offline_tts_to_wav(text: str, wav_path: str) -> None:
    """100% offline OS TTS fallback (Windows SAPI or macOS say)."""
    sys_name = platform.system()
    if sys_name == "Windows":
        ps_script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.SetOutputToWaveFile('{wav_path.replace(chr(39), chr(39)*2)}'); "
            "$t = [Console]::In.ReadToEnd(); "
            "$s.Speak($t); "
            "$s.Dispose()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            input=text.encode("utf-8"),
            check=True,
            timeout=45,
        )
    elif sys_name == "Darwin":
        subprocess.run(
            ["say", "-o", wav_path, "--data-format=LEI16@22050", text],
            check=True,
            timeout=45,
        )
    else:
        raise RuntimeError(f"No offline TTS fallback configured for {sys_name}")


async def _edge_tts_save(text: str, mp3_path: str, voice: str) -> None:
    import edge_tts
    comm = edge_tts.Communicate(text, voice)
    await comm.save(mp3_path)


def synthesize(text: str, out_ogg_path: str) -> str:
    """Synthesize speech into an Opus .ogg file. Returns engine name ('edge-tts' or 'offline-sapi')."""
    t0 = time.time()
    clean = clean_text_for_speech(text)
    if not clean:
        clean = "Empty response."

    tmp_dir = r"D:\.cache\temp" if os.path.exists("D:\\") else tempfile.gettempdir()
    os.makedirs(tmp_dir, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(out_ogg_path)), exist_ok=True)

    engine = "edge-tts"
    tmp_mp3 = os.path.join(tmp_dir, f"tts_{os.getpid()}_{int(time.time()*1000)}.mp3")
    tmp_wav = os.path.join(tmp_dir, f"tts_{os.getpid()}_{int(time.time()*1000)}.wav")
    try:
        try:
            asyncio.run(_edge_tts_save(clean, tmp_mp3, DEFAULT_VOICE))
            transcode_to_opus_ogg(tmp_mp3, out_ogg_path)
        except Exception as e:
            engine = "offline-sapi"
            offline_tts_to_wav(clean, tmp_wav)
            transcode_to_opus_ogg(tmp_wav, out_ogg_path)
    finally:
        for p in (tmp_mp3, tmp_wav):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass

    elapsed = round(time.time() - t0, 2)
    print(f"[{engine} | {elapsed}s | 0 USD]", file=sys.stderr)
    return engine


def transcribe(audio_path: str) -> str:
    """Transcribe an audio file (.ogg, .mp3, .wav, .m4a) using faster-whisper CPU int8."""
    t0 = time.time()
    import av
    _orig_av_open = av.open
    def _compat_av_open(*a, **kw):
        kw.pop("metadata_errors", None)
        return _orig_av_open(*a, **kw)
    av.open = _compat_av_open
    from faster_whisper import WhisperModel

    os.makedirs(DEFAULT_HF_CACHE, exist_ok=True)
    model = WhisperModel(
        DEFAULT_WHISPER_MODEL,
        device="cpu",
        compute_type="int8",
        download_root=DEFAULT_HF_CACHE,
    )
    segments, info = model.transcribe(audio_path, beam_size=3, vad_filter=True)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    elapsed = round(time.time() - t0, 2)
    print(f"[faster-whisper:{DEFAULT_WHISPER_MODEL} CPU-int8 | {elapsed}s | 0 MB VRAM | 0 USD]", file=sys.stderr)
    return text


def main():
    if len(sys.argv) < 3:
        print("Usage: voice_helper.py <transcribe|synthesize> ...", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1].lower()
    if cmd == "transcribe":
        audio_path = sys.argv[2]
        out = transcribe(audio_path)
        print(out)
    elif cmd == "synthesize":
        if len(sys.argv) < 4:
            print("Usage: voice_helper.py synthesize <text> <output_ogg_path>", file=sys.stderr)
            sys.exit(1)
        raw_text = sys.argv[2]
        out_path = sys.argv[3]
        if os.path.exists(raw_text) and raw_text.endswith(".txt"):
            raw_text = Path(raw_text).read_text(encoding="utf-8", errors="replace")
        eng = synthesize(raw_text, out_path)
        print(out_path)
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
