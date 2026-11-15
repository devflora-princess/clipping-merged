"""Orchestration: input -> transcribe -> pick moments -> render clips.

Steps (same pipeline shape in all three parent projects):
  1. Resolve input (local file, or YouTube / Twitch / Kick URL via yt-dlp
     — SupoClip's import sources; yt-dlp handles all three).
  2. Transcribe locally with faster-whisper (or AssemblyAI if
     TRANSCRIPTION_PROVIDER=assemblyai and a key is set).
  3. Pick moments: Gemini / Ollama if configured, else the free
     heuristic scorer with SupoClip's hook/engagement/value/
     shareability breakdown.
  4. For each clip: karaoke ASS captions (SupoClip templates),
     9:16 render with a 3-second hook teaser (opensource-clipping),
     auto-thumbnail, publish metadata.

If transcription finds almost no speech, the run still produces
time-based clips (whole video split into windows) with a clear
warning, instead of failing — but those clips have no captions.
"""

from __future__ import annotations

import json
import os
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
from . import transcribe as tr


def _time_windows(duration: float, n_clips: int, length: str) -> list[dict]:
    from .vendor.supoclip_clip_duration import clip_duration as supo_duration
    from .vendor.supoclip_clip_duration import normalize_clip_length

    preset = supo_duration(normalize_clip_length(length))
    target = min(preset.ideal_maximum, max(preset.ideal_minimum, 30))
    clips = []
    t = 0.0
    while t < duration and len(clips) < n_clips:
        end = min(duration, t + target)
        if end - t < 10:
            break
        clips.append({
            "start": round(t, 3), "end": round(end, 3),
            "predicted_score": 0,
            "virality": {"hook": 0, "engagement": 0, "value": 0,
                         "shareability": 0, "total": 0,
                         "hook_type": "none",
                         "reasoning": "No speech found; time-based fallback window."},
            "hook_title": "Watch this", "text": "",
        })
        t = end
    return clips


def run(source: str, outdir: str, n_clips: int = 3, length: str = "auto",
        whisper_model: str = "base", llm: str = "auto",
        template: str = "hormozi", transcript_path: str | None = None,
        no_hook: bool = False) -> dict:
    started = time.time()
    os.makedirs(outdir, exist_ok=True)
    workdir = os.path.join(outdir, "_work")
    os.makedirs(workdir, exist_ok=True)

    print(f"[1/5] Input: {source}")
    video = dl.resolve_input(source, workdir)
    duration = dl.probe_duration(video)
    print(f"      {video} ({duration:.1f}s)")

    print("[2/5] Transcribing "
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

    print(f"[3/5] Picking {n_clips} moments (llm={prov.llm_provider()}) ...")
    if n_words < 20:
        print("      WARNING: almost no speech found — using time-based "
              "windows with no captions.")
        clips = _time_windows(duration, n_clips, length)
        method = "time-fallback"
    else:
        clips, method = mom.pick_moments(
            transcript, duration, n_clips=n_clips, length=length,
            llm="none" if prov.llm_provider() == "none" else llm,
            energy=energy)
    print(f"      method={method}, clips={len(clips)}")
    for c in clips:
        v = c.get("virality", {})
        print(f"      {c['start']:.1f}-{c['end']:.1f}s score={v.get('total')} "
              f"(hook {v.get('hook')}/eng {v.get('engagement')}/"
              f"value {v.get('value')}/share {v.get('shareability')}) "
              f"{c.get('hook_title','')[:60]}")

    print("[4/5] Face tracking (optional, MediaPipe) ...")
    centres = face_mod.face_centres(video, duration)
    print(f"      {'found face track' if centres else 'not available — centred crop'}")

    print("[5/5] Rendering clips ...")
    rendered = []
    for idx, clip in enumerate(clips, 1):
        clip_words = [w for w in transcript.get("words") or []
                      if clip["start"] - 0.35 <= w["s"] < clip["end"]]
        ass_path = None
        if clip_words:
            ass = cap_mod.words_to_ass(clip_words, clip["start"],
                                       clip["end"], template)
            ass_path = os.path.join(workdir, f"clip{idx}.ass")
            cap_mod.write_ass(ass_path, ass)
        median_cx = None
        if centres:
            lo = max(0, int(clip["start"]))
            hi = min(len(centres), int(clip["end"]) + 1)
            if hi > lo:
                median_cx = statistics.median(centres[lo:hi])
        out = os.path.join(outdir, f"clip{idx:02d}.mp4")
        ren.render_clip(
            video, clip["start"], clip["end"], ass_path, out,
            hook_text="" if no_hook else (clip.get("hook_title") or ""),
            face_centres=median_cx)
        frame = os.path.join(workdir, f"clip{idx}_frame.jpg")
        ren.grab_frame(video, clip["start"] + 1.0, frame)
        thumb = os.path.join(outdir, f"clip{idx:02d}_thumb.jpg")
        ren.make_thumbnail(frame, clip.get("hook_title") or "Watch this", thumb)
        size = os.path.getsize(out)
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,duration",
             "-of", "csv=p=0", out], capture_output=True, text=True)
        rendered.append({**clip, "file": out, "thumbnail": thumb,
                         "bytes": size, "probe": probe.stdout.strip()})
        print(f"      clip{idx:02d}.mp4 ({size/1024:.0f} KB) "
              f"[{probe.stdout.strip()}]")

    pub_mod.write_metadata(outdir, clips)
    manifest = {
        "source": source, "video": video, "duration": duration,
        "method": method, "whisper_model": whisper_model,
        "template": template, "elapsed_seconds": round(time.time() - started, 1),
        "clips": rendered,
    }
    with open(os.path.join(outdir, "clips.json"), "w",
              encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    print(f"Done in {time.time() - started:.1f}s. Outputs in {outdir}")
    return manifest
