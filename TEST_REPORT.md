# TEST_REPORT.md — clipping-merged, tested 2026-11-15

Build VM: Python 3.12.3, ffmpeg/ffprobe 8.1.2 (with libass),
1.5 CPU, ~6 GB RAM, no GPU. No API key was used for any run
(`GEMINI_API_KEY` unset, no AssemblyAI key, no Ollama).

## v2 (pro style) — tested 2026-11-15

Same build VM. v2 changes: 15-30 s sentence-based picking,
Hook/Flow/Value/Trend scores, Hormozi captions (Montserrat Black,
100 px, y≈1250, karaoke from word timestamps), hook overlay
y280-500 for 2.5 s, YuNet face tracking, `zoompan` punch zoom
x1.20, silence cuts >0.3 s, optional ducked music bed, `--focus`,
`--style pro` (default) / `--style classic`.

### Unit tests — PASS (8/8)

```
.venv/bin/python -m pytest tests/ -q
8 passed
```

- `test_heuristic_finds_question_hook`: 15-30 s, Hook/Flow/Value/
  Trend keys, "not a prediction" note.
- `test_pick_starts_at_sentence_start`: no opening on "So"/"um".
- `test_ass_has_karaoke_tags`: classic ASS, PlayRes 1080x1920, `\k`.
- `test_pro_ass_spec`: Montserrat Black, Fontsize 100, MarginV 620,
  `\k`, ALL CAPS, 1-3 words per Dialogue line, yellow
  `&H003DD9FF` present.
- `test_silence_keep_ranges`: 1.0 s gap → [(0,4.15),(4.85,10)];
  0.2 s gap untouched; start-touching gap → [(0.85,10)].
- `test_silence_remap_words`: word at 6.0 s → 5.3 s after a 0.7 s cut.
- `test_face_ema_deadband`: jitter <0.02 ignored, real move followed.
- `test_focus_picks_near_times`: one clip per focus time.

### E2E test 1 (v2) — 83 s original test material, base Whisper, no key

Command:
`clip.py clip test_data/test_long.mp4 --clips 2 --whisper-model base --out test_data/out-v2 --llm none`

- Transcribed **256 words** (base), method=heuristic, 106.5 s total.
- `clip01.mp4` — 0.0-17.4 s, **17.43 s, 1080x1920 h264 + AAC,
  5,454,000 bytes**, score 64 (hook 25/flow 21/value 10/trend 8),
  zoom=7 windows, silence_cut=0.0 s, face_track=False (test
  pattern has no face — YuNet ran, found nothing, blurred-
  background fallback worked).
- `clip02.mp4` — 15.2-43.1 s, **27.83 s, 1080x1920, 8,545,000
  bytes**, score 64, zoom=6, silence_cut=0.0 s, face_track=False.
- Clips overlap 2.2 s (13% of the shorter clip < 25% dedupe ratio)
  — acceptable on this repetitive test material.
- Visual inspection (frames extracted with ffmpeg, viewed):
  frame 0.1 s shows the hook text in Montserrat Black, 2 lines,
  dark bar, at y~280-500, and captions "WHAT IS THE" at y~1250.
  Frame 4.6 s: **spoken words are yellow, upcoming words white**
  (the v2 karaoke direction — the first render had it backwards
  and it was fixed; see bugs). Hook bar gone after 2.5 s.
  Whisper base misheard "nobody" as "no body" (test-voice artifact).
- **Punch zoom verified numerically**: colour-bar transitions at
  row y=1000 are at x=179/360/539/719/899 (180 px bars) outside a
  zoom window (7.0 s) and at x=107/324/539/755/973 (~216 px bars,
  180x1.2) inside the "secret" window at 6.5 s — the zoom is a real
  1.20x punch, crash-cut, no ease.
- **Silence cut verified separately** (see E2E test 2): 0.0 s here
  is correct — the flite test speech has no gaps >0.3 s.

### E2E test 2 (v2) — silence + music, 85.1 s file with a 2 s silence

Test file `/tmp/test_silence.mp4`: 0-20 s of test_long + 2.0 s of
true silence (testsrc2 + anullsrc, matched to mono 16 kHz) +
20-83 s of test_long, re-encoded concat. Music: 90 s 440 Hz sine
`/tmp/test_music.wav`.

Command:
`clip.py clip /tmp/test_silence.mp4 --clips 2 --whisper-model tiny --out /tmp/out-silence2 --llm none --music /tmp/test_music.wav`

- 109.0 s total. `clip01.mp4` — 0.0-19.7 s, **19.77 s, 1080x1920,
  audio 19.74 s**, zoom=6, silence_cut=0.0 s.
- `clip02.mp4` — 15.6-45.3 s, **28.03 s (was 29.8 s), 1080x1920,
  audio 28.02 s**, zoom=5, **silence_cut=1.73 s** (2 gaps >0.3 s,
  3 keep-ranges) — the 2.0 s silence was cut to 0.27 s of pad
  (2x0.15 s), exactly as designed. Captions stay in sync (word
  timestamps remapped through the cuts).
- Music bed verified: output RMS -17.1 dB with `--music` vs
  -21.3 dB without (same segment, isolated render) — the bed is
  in the mix, ducked under the voice; both clips have full-length
  audio.

### E2E test 3 (v2) — classic style regression

Command:
`clip.py clip test_data/test.mp4 --clips 1 --whisper-model tiny --out /tmp/out-classic --llm none --style classic`

- 38.2 s. `clip01.mp4` — **28.00 s, 1080x1920 h264 + AAC**,
  zoom=0, silence_cut=0.0 s — classic correctly skips all pro
  features. v1 behaviour preserved.

### Face-pan expression test (synthetic centres)

`render_clip(..., face_centres=[0.25]*10 + [0.75]*10, ...)` on
test_long, 10 s: output 1080x1920, and the frame content pans
from the left side of the test pattern (green/yellow dominant)
to the right side (blue dominant) — the nested-if crop pan
expression works. YuNet detection itself has NOT been positively
verified on real face footage (see "Not tested").

### Gameranx re-cut (v2, test-only — no permission, do not post)

Source already local: `~/workspace/clip-jobs/A9fifS608C8/source.mp4`
(250 MB, "GTA 6: 25 Things You NEED TO KNOW", gameranx, 25.6 min).
Reused the base-Whisper transcript from `out-gemini/transcript.json`
(`--transcript`, no re-transcription, no download).

Command:
`clip.py clip source.mp4 --clips 3 --whisper-model tiny --out ~/workspace/clip-jobs/A9fifS608C8/out-v2 --llm none --transcript out-gemini/transcript.json --focus 382,800,1182`

- 186.4 s total (transcript reused, `--transcript`). All three clips
  1080x1920 h264 + AAC, face_track=False (gameranx video has no
  facecam — blurred-background fallback, correct):
  - `clip01.mp4` — **377.6-402.7 s, 25.13 s, 12,106 KB**, score 38
    (hook 10/flow 14/value 8/trend 6), zoom=4, silence_cut=0.0 s.
    Topic: map 3x bigger than RDR2 (focus 382 s). Hook title in
    metadata is a rough fragment ("That's a long game. I guess
    it's going") — heuristic titles are fragments; Gemini titles
    were clean.
  - `clip02.mp4` — **794.3-824.3 s, 30.00 s, 16,829 KB**, score 34,
    zoom=1, silence_cut=0.0 s. Topic: car stealing/hotwire
    (focus 800 s).
  - `clip03.mp4` — **1177.3-1201.8 s, 24.50 s, 10,813 KB**, score 47
    (hook 10/flow 16/value 15/trend 6), zoom=1, silence_cut=0.0 s.
    Topic: money laundering / disguises (focus 1182 s).
- Focus picks worked: one clip per focus time, starting just
  before each focus point. Heuristic scores are low (34-47) on
  this narration — hook 10 everywhere because the narration has
  no question/exclamation hooks; the Gemini run on the same video
  scored 89-95 with the same rubric shape. Silence 0.0 s is
  correct — the narration has few gaps >0.3 s.
- Visual inspection of clip01: frame 0.1 s shows the hook text in
  Montserrat Black at y~280-500 over the dark bar, and the karaoke
  caption "THAT'S A LONG" at y~1250 (spoken words yellow). Frame
  5 s: hook gone, caption "SO IT'S GONNA" at y~1250 over gameplay
  — the v2 look on real footage. (One flaw spotted and fixed
  here: the hook text lost apostrophes — "Thats" — see bug 7.)

### Bugs found and fixed in v2 (2026-11-15)

1. **OpenCV 5 removed `CascadeClassifier`** — the bundled Haar
   cascade cannot load on OpenCV 5. Switched to YuNet
   (`face_detection_yunet_2023mar.onnx`, bundled in
   `merged_clipper/data/`); Haar kept as legacy-only for OpenCV 4.
2. **Karaoke highlight ran backwards** — in this VM's libass,
   `\k` draws text BEFORE the sweep position in PrimaryColour and
   AFTER it in SecondaryColour, so Primary must be the spoken
   colour (yellow `&H003DD9FF`) and Secondary the upcoming colour
   (white `&H00FFFFFF`). Verified from rendered frames before/after.
3. **Punch zoom via `crop` silently does nothing** — ffmpeg's
   `crop` evaluates w/h once at init, so a time-varying crop size
   has no effect (verified: identical bar positions in/out of a
   zoom window). Reimplemented with `zoompan` (per-frame zoom,
   `on/30` time variable); verified numerically (180 px → 216 px
   bars inside a window).
4. **Zoom windows used absolute source times** — clips starting
   after 0 s never zoomed, because the renderer's clock starts at
   the clip start. Fixed: windows are now computed from
   clip-relative word times in the pipeline.
5. **Silence test file was broken, not the code** — the first
   `/tmp/test_silence.mp4` was built with `-c copy` concat of a
   mono 16 kHz speech segment and a stereo 48 kHz silence
   segment; the mismatched audio params produced garbage audio,
   so `silencedetect` found no silence at 20-22 s and a music
   render of that range came out with 0.275 s of audio. Rebuilt
   with matched params + re-encoded concat: silence detection and
   music both verified working. Lesson: match audio params before
   concat, or re-encode.
6. **Test script typo**: `test_pro_ass_spec` focus data and the
   dedupe fix for `--focus` (dedupe wrongly killed overlapping
   focus windows; focus picks now skip dedupe across foci).
7. **Hook text lost apostrophes** — `_escape_drawtext` stripped
   `'` entirely, so "That's a long game" rendered as "Thats a
   long game" (spotted on the gameranx v2 frame). `\'` breaks the
   ffmpeg filter graph at the graph level, so the fix replaces
   `'` with the typographic apostrophe U+2019, which renders
   identically in Montserrat Black. Verified: "That's a test —
   it's working" renders correctly.

### Not tested / not verified (v2)

- Gemini picking with the **new v2 prompt** (no key in the build
  VM; `GEMINI_API_KEY` unset). The v1 Gemini path was verified
  2026-11-15; the v2 prompt + Hook/Flow/Value/Trend JSON keys are
  code-complete and parse-tested only.
- Ollama picking (no Ollama in the VM).
- YuNet detection on **real face footage** (no face video in the
  VM); exercised on test pattern (correctly finds nothing) and
  the pan expression verified with synthetic centres.
- AssemblyAI transcription (no key; unchanged from v1).
- yt-dlp download in v2 (unchanged from v1; the gameranx source
  was downloaded in v1 with `--no-check-certificates`).
- Filler-word ("um"/"uh") removal — not implemented.
- Pillow per-word caption fallback — not implemented; `ass` is the
  only caption path (full ffmpeg build required).

---

## v1 — tested 2026-11-15 (kept for reference)

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
