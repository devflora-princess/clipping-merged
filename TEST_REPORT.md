# TEST_REPORT.md — clipping-merged, tested 2026-11-15

Build VM: Python 3.12.3, ffmpeg/ffprobe 8.1.2 (with libass),
1.5 CPU, ~6 GB RAM, no GPU. No API key was used for any run
(`GEMINI_API_KEY` unset, no AssemblyAI key, no Ollama).

## Test material (no copyrighted footage)

- Script: an original 127-word text about clipping mistakes,
  written for this test (`test_data/script.txt`).
- Speech: rendered offline with ffmpeg's built-in `flite` filter
  (voice `slt`) — `test_data/speech.wav`, 40.4 s.
- Video: ffmpeg `testsrc2` pattern, 1280x720, 30 fps, + speech
  audio → `test_data/test.mp4` (40.4 s, 13.9 MB) and a doubled
  version `test_data/test_long.mp4` (83.0 s, speech repeated).
- No GTA 6 footage, trailers, or streams were used anywhere.

## Unit tests — PASS (2/2)

```
.venv/bin/python -m pytest tests/ -q
2 passed in 0.03s
```

- `test_heuristic_finds_question_hook`: heuristic picks a clip
  from a question-led transcript, score > 0, 15–60 s long.
- `test_ass_has_karaoke_tags`: ASS output has PlayRes 1080x1920
  and `\k` karaoke timing tags.

## End-to-end test 1 — 40.4 s source, `--whisper-model base`, no key

Command:
`clip.py clip test_data/test.mp4 --clips 2 --whisper-model base --out test_data/out`

- Transcribed **129 words** (base model; the earlier `tiny` run
  misheard "new clippers" as "nuclear powers" — see bugs).
- Picked **1 clip** (0.0–40.4 s, score 57: hook 25 / engagement 18 /
  value 10 / shareability 7). One clip is correct here: a 40 s
  source only contains one 25–50 s window.
- Output `clip01.mp4`: **1080x1920, h264, 40.433 s, 12,581,856 bytes
  (12.6 MB)**, 1 video stream + 1 AAC audio stream (verified with
  ffprobe). Total run time **53.5 s**.
- `clip01_thumb.jpg` (163,158 bytes) generated; frames extracted at
  1.5 s and 5 s and **visually inspected**: hook bar + hook text at
  the top for the first 3 s, blurred-background 9:16 layout, karaoke
  captions burned in with green-highlighted active words
  ("BIGGEST MISTAKE NEW", "WHY NO BODY").

## End-to-end test 2 — 83.0 s source, 2 clips requested

Command:
`clip.py clip test_data/test_long.mp4 --clips 2 --whisper-model base --out test_data/out2`

- Transcribed **256 words**.
- Picked **2 clips**: clip01 3.7–53.6 s (49.9 s, score 60),
  clip02 45.0–81.0 s (36.1 s, score 56).
- `clip01.mp4`: 1080x1920, 49.9 s, 15,588,294 bytes.
  `clip02.mp4`: 1080x1920, 36.1 s, 11,263,539 bytes.
  Both single video stream + audio. Total run time **112.1 s**.

## Subcommand checks — PASS

- `clip.py edit --input out/clip01.mp4 --start 5 --end 15` →
  10.13 s trimmed file (keyframe-accurate copy, as documented).
- `clip.py publish --dry-run out/clip01.mp4` → prints the 6-step
  manual checklist, states auto-posting is not implemented.

## Bugs found by testing, and fixed

1. **faster-whisper 1.2.1 + PyAV 19 = crash.** `TypeError: open()
   got an unexpected keyword argument 'metadata_errors'` on every
   run. Fix: decode the WAV with stdlib `wave` and pass a numpy
   array to Whisper — PyAV is never called now (documented in
   `requirements.txt` and `transcribe.py`).
2. **av 14.4.0 has no Python 3.12 wheel here** and fails to build
   from source — same fix as (1); do not pin av.
3. **Pillow was missing from the test venv** (it is in
   `requirements.txt` but had not been installed there) — run died
   at the thumbnail step. Installed; re-ran green.
4. **Output MP4 contained TWO video streams** (filtered 1080x1920
   + raw 1280x720 source) because the ffmpeg command mapped `0:v`.
   File was 27.5 MB and players could show the wrong stream. Fix:
   filter graph now ends in a labelled `[vout]` and only `[vout]` +
   audio are mapped. Re-verified: 1 video stream, 12.6 MB.
5. **Hook teaser text overflowed the frame** — first at 64 pt /
   90 chars, then at 56 pt / 38 chars. Fix: 48 pt, truncate to
   30 chars. Visually verified: "clips and wonder why …" fits
   centred in the top bar.

## What was NOT tested (no key / no service available)

- Gemini moment picking (no `GEMINI_API_KEY`), Ollama picking
  (no local model running), AssemblyAI transcription (no key —
  code path exists, plain-HTTP, clearly marked UNTESTED).
- yt-dlp URL download (no test URL was run; the code path is the
  standard yt-dlp call both parents use).
- Face tracking (MediaPipe + OpenCV not installed in the VM) —
  the centred-crop fallback is what was tested.
- Any real YouTube/Twitch/Kick source, and any posting to
  TikTok/Reels/Shorts (manual step by design).

## Known limitations observed in the tests

- The free heuristic can start a clip mid-sentence (test 2 clip01
  opens "clips and wonder why no body watches…"). Gemini/Ollama
  picking snaps to sentence starts better.
- `--transcript` reuse skips the audio-energy input, so scores
  differ slightly from a full run (53 vs 60 for the same clip).
- `edge-tts` (Microsoft TTS) is **blocked by this VM's egress
  proxy** (SSL error on speech.platform.bing.com) — that is why
  the offline flite voice was used for test speech. It does not
  affect the clipper itself.
- Whisper model download on first run needs network (Hugging Face)
  and takes ~1–2 min for `base`; later runs reuse the cache.
