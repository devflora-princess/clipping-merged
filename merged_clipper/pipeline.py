"""Orchestration v2: input -> transcribe -> pick moments -> render clips.

Pro pipeline (report §7.1, adapted):
  1. Resolve input (local file, or YouTube / Twitch / Kick URL via yt-dlp).
  2. Transcribe locally with faster-whisper (or AssemblyAI if set).
  3. Pick moments: Gemini / Ollama if configured, else the free
     heuristic — sentence-based, 15-30 s (prefer 20-30 s), Hook /
     Flow / Value / Trend rubric (ranking aid, not a prediction).
  4. Per clip: silence/jump-cut gaps >0.3 s (word timestamps remapped
     through the cuts), face track (0.5 s samples, EMA + deadband),
     punch-zoom windows on emphasised words, Hormozi ASS captions
     (Montserrat Black, 100 px, y~1250), hook overlay at y280-500
     for 2.5 s, optional ducked music bed, auto-thumbnail from
     frame 0 (face + hook text already on it).

If transcription finds almost no speech, the run still produces
time-based clips (25 s windows) with a clear warning, instead of
failing — but those clips have no captions.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import subprocess
import time

from . import captions as cap_mod
from . import download as dl
from . import face as face_mod
from . import moments as mom
from . import providers as prov
from . import publish as pub_mod
from . import render as ren
from . import silence as sil
from . import transcribe as tr

EMPHASIS_WORDS = set(mom.HOOK_WORDS) | set(mom.EMOTIONAL_WORDS)


def _time_windows(duration: float, n_clips: int, length: str) -> list[dict]:
    target = 25.0
    clips = []
    t = 0.0
    while t < duration and len(clips) < n_clips:
        end = min(duration, t + target)
        if end - t < 10:
            break
        clips.append({
            "start": round(t, 3), "end": round(end, 3),
            "predicted_score": 0,
            "virality": {"hook": 0, "flow": 0, "value": 0, "trend": 0,
                         "total": 0, "hook_type": "none",
                         "reasoning": "No speech found; time-based fallback window.",
                         "note": "Ranking aid within this batch only, "
                                 "not a prediction of views."},
            "hook_title": "Watch this", "text": "",
        })
        t = end
    return clips


def _zoom_windows(words: list[dict], max_windows: int = 8) -> list[tuple[float, float]]:
    """Punch-zoom windows on emphasised words (numbers, power words).

    Each window covers the word plus 0.25 s after it, so the zoom
    lands on the word and holds to the end of its clause.
    """
    scored = []
    for w in words:
        tok = re.sub(r"[^A-Za-z0-9]", "", w["w"]).lower()
        score = 0
        if re.search(r"\d", w["w"]):
            score += 3
        if tok in EMPHASIS_WORDS:
            score += 2
        if w["w"].strip().isupper() and len(tok) > 2:
            score += 1
        if score:
            scored.append((score, w))
    scored.sort(key=lambda p: -p[0])
    windows = []
    for _score, w in scored[:max_windows]:
        windows.append((max(0.0, float(w["s"]) - 0.05),
                        float(w["e"]) + 0.25))
    windows.sort()
    # Merge overlapping windows.
    merged: list[list[float]] = []
    for s, e in windows:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def run(source: str, outdir: str, n_clips: int = 3, length: str = "auto",
        whisper_model: str = "base", llm: str = "auto",
        template: str = "hormozi", transcript_path: str | None = None,
        no_hook: bool = False, style: str = "pro",
        music_path: str | None = None, no_zoom: bool = False,
        no_silence_cut: bool = False,
        focus_times: list[float] | None = None) -> dict:
    started = time.time()
    os.makedirs(outdir, exist_ok=True)
    workdir = os.path.join(outdir, "_work")
    os.makedirs(workdir, exist_ok=True)

    print(f"[1/6] Input: {source}")
    video = dl.resolve_input(source, workdir)
    duration = dl.probe_duration(video)
    print(f"      {video} ({duration:.1f}s)")

    print("[2/6] Transcribing "
          f"(provider={prov.transcription_provider()}, model={whisper_model}) ...")
    if transcript_path:
        with open(transcript_path, encoding="utf-8") as fh:
            transcript = json.load(fh)
        print("      using supplied transcript, skipping Whisper")
        energy = None
    else:
        transcript = prov.transcribe_with_provider(video, workdir,
                                                   model=whisper_model)
        wav = os.path.join(workdir, "audio.wav")
        energy = tr.audio_energy_per_second(wav) if os.path.exists(wav) else None
    n_words = len(transcript.get("words") or [])
    print(f"      {n_words} words transcribed")
    with open(os.path.join(outdir, "transcript.json"), "w",
              encoding="utf-8") as fh:
        json.dump(transcript, fh, indent=2, ensure_ascii=False)

    print(f"[3/6] Picking {n_clips} moments (llm={prov.llm_provider()}, "
          f"style={style}) ...")
    if n_words < 20:
        print("      WARNING: almost no speech found — using time-based "
              "windows with no captions.")
        clips = _time_windows(duration, n_clips, length)
        method = "time-fallback"
    else:
        clips, method = mom.pick_moments(
            transcript, duration, n_clips=n_clips, length=length,
            llm="none" if prov.llm_provider() == "none" else llm,
            energy=energy, focus_times=focus_times)
    print(f"      method={method}, clips={len(clips)}")
    for c in clips:
        v = c.get("virality", {})
        print(f"      {c['start']:.1f}-{c['end']:.1f}s "
              f"({c['end'] - c['start']:.1f}s) score={v.get('total')} "
              f"(hook {v.get('hook')}/flow {v.get('flow')}/"
              f"value {v.get('value')}/trend {v.get('trend')}) "
              f"{c.get('hook_title','')[:60]}")
        print("        [score] ranking aid within this batch, "
              "not a prediction of views.")

    print("[4/6] Rendering clips (pro: silence cuts, face track, "
          "punch zoom, Hormozi captions, hook overlay) ...")
    rendered = []
    for idx, clip in enumerate(clips, 1):
        clip_src = video
        clip_start = clip["start"]
        clip_end = clip["end"]
        clip_words = [w for w in transcript.get("words") or []
                      if clip_start - 0.35 <= w["s"] < clip_end]
        cut_seconds = 0.0
        # --- silence / jump cuts (pro only) ---
        if style == "pro" and not no_silence_cut and clip_words:
            silences = sil.detect_silences(video, clip_start, clip_end)
            ranges = sil.keep_ranges(clip_start, clip_end, silences)
            removed = (clip_end - clip_start) - sil.cut_duration(ranges)
            if removed > 0.2 and len(ranges) >= 1:
                cut_path = os.path.join(workdir, f"clip{idx}_cut.mp4")
                sil.cut_segments(video, ranges, cut_path, workdir,
                                 f"clip{idx}")
                clip_words = sil.remap_words(clip_words, ranges, clip_start)
                clip_src = cut_path
                clip_start, clip_end = 0.0, sil.cut_duration(ranges)
                cut_seconds = removed
                print(f"      clip{idx:02d}: cut {removed:.1f}s of silence "
                      f"({len(silences)} gaps >0.3s, {len(ranges)} keep-ranges)")
        # --- face track on the (possibly cut) clip ---
        centres = None
        if style == "pro":
            dur = clip_end - clip_start
            centres = face_mod.face_centres(clip_src, dur)
        print(f"      clip{idx:02d}: face track = "
              f"{face_mod.face_detector_name() if centres else 'none — blurred-background fallback'}")
        # --- captions ---
        ass_path = None
        if clip_words:
            if style == "pro":
                ass = cap_mod.words_to_ass_pro(clip_words, clip_start, clip_end)
            else:
                ass = cap_mod.words_to_ass(clip_words, clip_start,
                                           clip_end, template)
            ass_path = os.path.join(workdir, f"clip{idx}.ass")
            cap_mod.write_ass(ass_path, ass)
        # --- punch zoom windows (clip-relative times!) ---
        # clip_words are absolute source times unless a silence cut
        # remapped them (then clip_start is 0). The renderer's clock
        # starts at the clip start, so shift before scoring windows.
        rel_words = [{**w, "s": float(w["s"]) - clip_start,
                      "e": float(w["e"]) - clip_start} for w in clip_words]
        zoom = [] if (no_zoom or style != "pro") else _zoom_windows(rel_words)
        out = os.path.join(outdir, f"clip{idx:02d}.mp4")
        ren.render_clip(
            clip_src, clip_start, clip_end, ass_path, out,
            hook_text="" if no_hook else (clip.get("hook_title") or ""),
            face_centres=centres, style=style,
            zoom_windows=zoom, music_path=music_path)
        frame = os.path.join(workdir, f"clip{idx}_frame.jpg")
        ren.grab_frame(out, 0.1, frame)  # frame 0: hook text is the thumbnail
        thumb = os.path.join(outdir, f"clip{idx:02d}_thumb.jpg")
        ren.make_thumbnail(frame, clip.get("hook_title") or "Watch this", thumb)
        size = os.path.getsize(out)
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,duration",
             "-of", "csv=p=0", out], capture_output=True, text=True)
        rendered.append({**clip, "file": out, "thumbnail": thumb,
                         "bytes": size, "probe": probe.stdout.strip(),
                         "silence_cut_seconds": round(cut_seconds, 2),
                         "zoom_windows": len(zoom),
                         "face_track": bool(centres)})
        print(f"      clip{idx:02d}.mp4 ({size/1024:.0f} KB) "
              f"[{probe.stdout.strip()}] zoom={len(zoom)} "
              f"silence_cut={cut_seconds:.1f}s")

    pub_mod.write_metadata(outdir, clips)
    manifest = {
        "source": source, "video": video, "duration": duration,
        "method": method, "whisper_model": whisper_model,
        "template": template, "style": style,
        "elapsed_seconds": round(time.time() - started, 1),
        "clips": rendered,
    }
    with open(os.path.join(outdir, "clips.json"), "w",
              encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    print(f"Done in {time.time() - started:.1f}s. Outputs in {outdir}")
    return manifest
