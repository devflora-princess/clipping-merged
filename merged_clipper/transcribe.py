"""Transcription with faster-whisper (word-level timestamps).

Same engine and same decode settings both parent projects use
(OpenShorts subtitles.py: beam 5, VAD filter on, no conditioning on
previous text, word timestamps on). The model runs locally on CPU —
no audio leaves the machine.

Transcript shape (shared with moments.py and the vendored
clip_selection helpers):
    {"text": str,
     "segments": [{"start": float, "end": float, "text": str}],
     "words": [{"w": str, "s": float, "e": float}]}
"""

from __future__ import annotations

import os
import subprocess

DEFAULT_MODEL = "base"  # tiny = fastest, small = better, large-v3 = best/slowest
DEFAULT_COMPUTE = "int8"  # int8 is the CPU-friendly default from OpenShorts


def extract_audio(video_path: str, wav_path: str) -> str:
    """Extract mono 16 kHz WAV for Whisper (and for the energy scorer)."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", video_path,
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav_path],
        check=True,
    )
    return wav_path


def transcribe(video_path: str, workdir: str, model: str = DEFAULT_MODEL,
               device: str = "cpu", compute_type: str = DEFAULT_COMPUTE) -> dict:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper is not installed. Install it with: "
            "pip install faster-whisper"
        ) from exc

    wav_path = os.path.join(workdir, "audio.wav")
    extract_audio(video_path, wav_path)
    fw = WhisperModel(model, device=device, compute_type=compute_type)
    # NOTE: pass audio as a numpy array, not a file path. faster-whisper
    # 1.2.1 decodes file paths with PyAV, and the av 19.x wheel pip
    # installs on Python 3.12 removed the `metadata_errors` kwarg it
    # calls with (TypeError on every run). Decoding the 16 kHz mono WAV
    # with the stdlib `wave` module avoids PyAV entirely.
    audio = _read_wav_mono16k(wav_path)
    segments, _info = fw.transcribe(
        audio,
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
        word_timestamps=True,
    )
    out_segments, out_words, texts = [], [], []
    for seg in segments:
        out_segments.append({
            "start": float(seg.start), "end": float(seg.end),
            "text": seg.text.strip(),
        })
        texts.append(seg.text.strip())
        for w in (seg.words or []):
            out_words.append({"w": w.word, "s": float(w.start), "e": float(w.end)})
    return {"text": " ".join(texts), "segments": out_segments, "words": out_words}


def _read_wav_mono16k(wav_path: str):
    """Read a mono 16 kHz WAV into a float32 numpy array (for Whisper)."""
    import wave

    import numpy as np

    with wave.open(wav_path, "rb") as wf:
        raw = wf.readframes(wf.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def audio_energy_per_second(wav_path: str) -> list[float]:
    """RMS energy per second of the mono 16 kHz WAV (0..1 normalised).

    Lets the heuristic scorer favour loud, energetic moments (laughter,
    shouting, applause) without any ML model. Pure stdlib + numpy.
    """
    import wave

    import numpy as np

    with wave.open(wav_path, "rb") as wf:
        n = wf.getnframes()
        raw = wf.readframes(n)
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if len(samples) == 0:
        return []
    per_sec = []
    sr = 16000
    for i in range(0, len(samples), sr):
        chunk = samples[i:i + sr]
        per_sec.append(float(np.sqrt(np.mean(chunk ** 2))) if len(chunk) else 0.0)
    peak = max(per_sec) if per_sec else 1.0
    return [v / peak if peak else 0.0 for v in per_sec]
