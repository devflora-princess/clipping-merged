"""Provider selection, modelled on SupoClip's configuration.

SupoClip (docs/configuration.md) picks a transcription provider with
``TRANSCRIPTION_PROVIDER`` (default ``assemblyai``) and an LLM with
``LLM`` (``google-gla:gemini-3-flash-preview`` etc., or a local Ollama
model). This module keeps the same env-var names so anyone coming
from SupoClip feels at home — but the defaults are flipped to free:

- Transcription: ``whisper`` (local faster-whisper) by default.
  ``assemblyai`` is available if ASSEMBLY_AI_API_KEY is set, via a
  plain-HTTP implementation (no SDK needed). It is a paid service;
  it is never required.
- LLM: ``none`` (free heuristic) by default; ``gemini`` if a Gemini
  key is set; ``ollama`` for a local model. ``openai`` / ``anthropic``
  are recognised names but not implemented in this CLI — they fall
  back to the heuristic with a clear warning instead of failing.

Nothing here calls a paid API unless the user sets the key for it.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

from . import transcribe as _whisper


def transcription_provider() -> str:
    return os.environ.get("TRANSCRIPTION_PROVIDER", "whisper").lower()


def llm_provider() -> str:
    raw = os.environ.get("LLM", "").lower()
    if raw.startswith("google"):
        return "gemini"
    if raw.startswith("ollama") or "ollama" in raw:
        return "ollama"
    if raw in ("openai", "anthropic", "claude", "gpt"):
        print(f"  [providers] LLM '{raw}' is not implemented in this CLI; "
              "using the free heuristic scorer instead.")
        return "none"
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    if os.environ.get("LLM_BASE_URL"):
        return "ollama"
    return "none"


def transcribe_with_provider(video_path: str, workdir: str,
                             model: str = "base") -> dict:
    """Transcribe with the selected provider; always falls back to
    local Whisper if the paid provider fails."""
    provider = transcription_provider()
    if provider == "assemblyai":
        try:
            return _transcribe_assemblyai(video_path, workdir)
        except Exception as exc:  # noqa: BLE001 - paid path must never kill a job
            print(f"  [transcribe] AssemblyAI failed ({exc}); "
                  "falling back to local Whisper.")
    return _whisper.transcribe(video_path, workdir, model=model)


def _transcribe_assemblyai(video_path: str, workdir: str) -> dict:
    """AssemblyAI via plain HTTP (upload -> transcribe -> poll).

    UNTESTED in this VM (no AssemblyAI key was available for the test
    run — see TEST_REPORT.md). Kept small and explicit on purpose.
    """
    api_key = os.environ.get("ASSEMBLY_AI_API_KEY")
    if not api_key:
        raise RuntimeError("ASSEMBLY_AI_API_KEY is not set")
    headers = {"Authorization": api_key}
    wav = _whisper.extract_audio(video_path, os.path.join(workdir, "audio.wav"))
    with open(wav, "rb") as fh:
        data = fh.read()
    req = urllib.request.Request(
        "https://api.assemblyai.com/v2/upload", data=data,
        headers={**headers, "Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        upload_url = json.loads(resp.read())["upload_url"]
    body = json.dumps({
        "audio_url": upload_url, "word_boost": [],
        "speech_model": os.environ.get("ASSEMBLY_AI_SPEECH_MODEL", "universal"),
    }).encode()
    req = urllib.request.Request(
        "https://api.assemblyai.com/v2/transcript", data=body,
        headers={**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        job = json.loads(resp.read())
    job_id = job["id"]
    for _ in range(120):
        time.sleep(5)
        req = urllib.request.Request(
            f"https://api.assemblyai.com/v2/transcript/{job_id}",
            headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            job = json.loads(resp.read())
        if job["status"] == "completed":
            break
        if job["status"] == "error":
            raise RuntimeError(f"AssemblyAI error: {job.get('error')}")
    segments, words = [], []
    for w in job.get("words", []):
        words.append({"w": w["text"], "s": w["start"] / 1000.0,
                      "e": w["end"] / 1000.0})
    # Rebuild segments from word gaps (>0.8 s silence starts a segment).
    cur: list[dict] = []
    for w in words:
        if cur and w["s"] - cur[-1]["e"] > 0.8:
            segments.append({"start": cur[0]["s"], "end": cur[-1]["e"],
                             "text": " ".join(x["w"] for x in cur)})
            cur = []
        cur.append(w)
    if cur:
        segments.append({"start": cur[0]["s"], "end": cur[-1]["e"],
                         "text": " ".join(x["w"] for x in cur)})
    return {"text": " ".join(w["w"] for w in words),
            "segments": segments, "words": words}
