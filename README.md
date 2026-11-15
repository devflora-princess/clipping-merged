# clipping-merged — free AI clipper (merged, AGPL-3.0)

Turn a long video (or a YouTube / Twitch / Kick link) into vertical
9:16 clips with karaoke captions, a 3-second hook teaser and an
auto-thumbnail. No watermark, no monthly cap, no paid subscription.
The core path needs **no API key at all**.

> **Licence: AGPL-3.0 overall.** This merged work includes code from
> SupoClip (AGPL-3.0), so the whole merged work is AGPL-3.0. If you
> receive it, or run it as a service for others, you must share the
> source of the merged work under AGPL-3.0. The two MIT-licensed
> parents keep their MIT licences — see `NOTICE` and
> `licenses-MIT-openshorts.txt` / `licenses-MIT-opensource-clipping.txt`.

## What it does

```
video / URL ──▶ download (yt-dlp) ──▶ transcribe (faster-whisper, local)
   ──▶ pick moments (Gemini / Ollama / free heuristic)
   ──▶ 9:16 render + karaoke captions + hook teaser + thumbnail
   ──▶ clip01.mp4 … + clips.json + publish_metadata.json
```

| Feature | Where it came from |
|---|---|
| Download (YouTube / Twitch / Kick / local file) | yt-dlp, as in all three parents; Twitch/Kick import is SupoClip's import list |
| Transcription, word timestamps | faster-whisper (OpenShorts + opensource-clipping) |
| Moment picking, Gemini or local Ollama | OpenShorts |
| Moment picking, free heuristic (no key) | New glue code, scored with SupoClip's breakdown |
| Virality score: hook / engagement / value / shareability (0–25 each, total 0–100) | SupoClip's `ViralityAnalysis` model |
| Clip-length presets (auto/short/medium/long) | SupoClip `clip_duration.py` (vendored verbatim) |
| Caption templates (default/hormozi/mrbeast/minimal/tiktok/neon/podcast) | SupoClip `caption_templates.py` (vendored verbatim) |
| Emoji + emphasis on caption words | SupoClip `emoji_captions.py` (vendored verbatim) |
| Clip-selection helpers (windows, word-snapping, dedup) | OpenShorts `clip_selection.py` (vendored verbatim) |
| 3-second cinematic hook teaser (bars + hook line) | opensource-clipping |
| Auto-thumbnail (frame + dark overlay + title) | opensource-clipping |
| Face-tracked crop (MediaPipe, optional) | opensource-clipping |
| Provider env vars (`TRANSCRIPTION_PROVIDER`, `LLM`) | SupoClip's configuration model |
| Built-in editor (`clip.py edit`) | SupoClip's editor concept, lightweight |
| Publish metadata + dry-run checklist + `track.csv` log | SupoClip's publishing / performance-loop concepts |

## What is new / reimplemented (not copied)

- The pipeline itself (`merged_clipper/`): opensource-clipping's
  studio modules could not be imported directly (they require
  `cv2` + `mediapipe` at import time), and OpenShorts' `main.py` /
  `app.py` are a ~600 KB monolith tied to its dashboard. So the
  karaoke/hook/thumbnail/render code is fresh ffmpeg + Pillow glue
  code written for this repo.
- The heuristic scorer: SupoClip's virality breakdown is produced
  by its LLM; here the same four numbers are computed from wording,
  questions, numbers and audio energy when no LLM is available.
- SupoClip's full Docker stack (Next.js + FastAPI + Postgres +
  Redis, 11 services) was **deliberately NOT ported** — far too
  heavy for a laptop CLI. Use the original SupoClip repo if you
  want the full web app.

## Install

```bash
git clone <this-repo> clipping-merged
cd clipping-merged
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# system ffmpeg + ffprobe required:
#   Ubuntu/Debian: sudo apt install ffmpeg
#   macOS: brew install ffmpeg
#   Windows: choco install ffmpeg
```

Python 3.10+ (tested on 3.12). No GPU needed.

## Use

```bash
# Local file, 3 clips, no key needed:
.venv/bin/python clip.py clip myvideo.mp4 --clips 3 --out out/

# YouTube / Twitch / Kick URL (only footage you have permission to clip):
.venv/bin/python clip.py clip "https://www.youtube.com/watch?v=..." --clips 5

# Faster on a slow CPU (tiny is fastest, small/base are more accurate):
.venv/bin/python clip.py clip myvideo.mp4 --whisper-model tiny

# Different caption style / clip length:
.venv/bin/python clip.py clip myvideo.mp4 --template mrbeast --length short

# Trim a rendered clip (built-in editor):
.venv/bin/python clip.py edit --input out/clip01.mp4 --start 5 --end 35 --out cut.mp4

# Publishing checklist (dry run — nothing is posted automatically):
.venv/bin/python clip.py publish --dry-run out/clip01.mp4
```

Outputs per run: `clip01.mp4` (+ thumbnail `clip01_thumb.jpg`),
`transcript.json`, `clips.json` (timings + virality scores),
`publish_metadata.json` (title/description/hashtags to paste).

## What needs a key vs what works free

| Thing | Key needed? |
|---|---|
| Download, transcribe (local Whisper), heuristic scoring, crop, karaoke captions, hook teaser, thumbnail, editor, metadata | **No — free, works offline** (after the one-time model download) |
| Gemini moment picking | `GEMINI_API_KEY` (free tier is enough to start). Default model is `gemini-3.5-flash` (verified 2026-11-15 — the 2.5 models are retired for new accounts); override with `GEMINI_MODEL=...` if needed. |
| Local LLM moment picking | Ollama running locally, `LLM_BASE_URL=http://localhost:11434/v1`, no key |
| AssemblyAI transcription | `ASSEMBLY_AI_API_KEY` — paid service, **optional only**; local Whisper is the default and is used whenever AssemblyAI is unset or fails |
| OpenAI / Anthropic LLMs | Recognised but **not implemented** — falls back to the free heuristic with a warning |

Set providers with env vars (SupoClip-style):
```bash
TRANSCRIPTION_PROVIDER=whisper        # default; or assemblyai
LLM=gemini                            # or ollama, or none (heuristic)
GEMINI_API_KEY=...                    # for gemini
LLM_BASE_URL=http://localhost:11434/v1  # for ollama
LLM_MODEL=llama3.1                    # ollama model name
```

## Running on Victor's HP laptop (16 GB RAM, no GPU, Windows)

1. Install Python 3.11 or 3.12 from python.org (tick "Add to PATH"),
   and ffmpeg (`choco install ffmpeg`, or download a static build
   and add it to PATH).
2. In PowerShell, in the repo folder:
   ```powershell
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   .venv\Scripts\python clip.py clip "C:\path\to\video.mp4" --clips 3 --whisper-model tiny --out out
   ```
3. Expect roughly 1–2× real time on CPU with `tiny`
   (a 10-minute video ≈ 10–20 minutes), longer with `base`/`small`.
   Close other apps while it runs; 16 GB RAM is plenty.
4. **Colab shortcut (no local install):** opensource-clipping's
   README documents running its pipeline on a free Google Colab
   T4 GPU — the same trick works here: upload this repo to Colab,
   `!pip install -r requirements.txt`, `!apt install ffmpeg`, run
   `clip.py`. Much faster than a laptop CPU.

## Publishing — read this before posting

- Only clip footage a campaign or the creator has authorised you
  to clip. This tool does not create a licence.
- No leaks, no AI-fake "gameplay", no isolated cutscenes, no
  ending/spoilers.
- Paid clips are ads: add `#ad` and turn on the platform's
  paid-partnership / branded-content toggle.
- Never pay to join a clipping campaign, never buy views.
- Direct auto-posting is **not implemented** in this CLI (there is
  no free official upload API for personal TikTok/Instagram
  accounts; YouTube upload needs an OAuth client you create in
  Google Cloud). `publish --dry-run` prints the manual steps, and
  `publish_metadata.json` has the title/description ready to paste.

## Known limitations (honest list)

- The free heuristic scorer is a ranking aid, not a view guarantee.
  It can start a clip mid-sentence; Gemini/Ollama picking snaps to
  sentence starts better. See TEST_REPORT.md.
- Face tracking needs `opencv-python` + `mediapipe` installed; without
  them the crop is centred over a blurred background (still fine).
- Whisper `tiny` mishears words on robotic/low-quality audio
  ("new clippers" → "nuclear powers" in one test); use `base` or
  `small` for real clips.
- AssemblyAI path is implemented but **untested** (no key was
  available in the build VM). Local Whisper is the tested default.
- `clip.py edit` trims with `-c copy` (keyframe accuracy, ~±1 s);
  re-encode for frame accuracy — the command prints how.
- B-roll, AI voice-over and auto-upload from opensource-clipping
  are **not** in this CLI yet (concepts only, documented above).
