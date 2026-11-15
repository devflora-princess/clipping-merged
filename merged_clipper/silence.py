"""Silence / jump-cut handling (v2, pro style).

Research (report §3/§7): paid tools tighten gaps >0.3 s (Submagic
Smart Trim) — "tightening pauses is the single highest-yield edit
pass". For VIDEO the correct method is: detect silences, build
keep-ranges with a small pad inside each gap, cut BOTH streams.
Never use audio-only ``silenceremove`` on video — it desyncs.

Pipeline per clip:
  1. ``detect_silences`` runs ffmpeg silencedetect on the clip's
     segment and parses silence_start/silence_end.
  2. ``keep_ranges`` turns silences into keep-ranges: the middle of
     any gap longer than ``min_gap`` (default 0.3 s) is cut, keeping
     ``pad`` (default 0.15 s) of silence at each end of the gap so
     cuts do not clip word onsets/offsets. Edge silences are trimmed
     to the same pad.
  3. ``cut_segments`` re-encodes each keep-range and concats them
     (concat demuxer, stream copy — same codec, no desync).
  4. ``remap_words`` shifts transcript word timestamps through the
     keep-ranges, so karaoke captions stay in sync with the cut clip
     without re-transcribing. Words falling inside a removed middle
     are dropped (they should not exist — the region was silent).
"""

from __future__ import annotations

import os
import re
import subprocess

DEFAULT_NOISE_DB = -50.0
DEFAULT_MIN_GAP = 0.3
DEFAULT_PAD = 0.15


def detect_silences(video: str, start: float, end: float,
                    noise_db: float = DEFAULT_NOISE_DB,
                    min_duration: float = DEFAULT_MIN_GAP) -> list[tuple[float, float]]:
    """Return [(silence_start, silence_end)] inside [start, end]."""
    cmd = ["ffmpeg", "-v", "info", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
           "-i", video, "-af",
           f"silencedetect=noise={noise_db}dB:d={min_duration}",
           "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    text = proc.stderr or ""
    starts = [float(x) for x in re.findall(r"silence_start:\s*([0-9.]+)", text)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", text)]
    out: list[tuple[float, float]] = []
    for s, e in zip(starts, ends):
        # silencedetect times are relative to the -ss start.
        out.append((start + s, start + e))
    if len(ends) < len(starts):
        # Trailing silence runs to the end of the segment.
        out.append((start + starts[-1], end))
    return [(s, e) for s, e in out if e > s]


def keep_ranges(start: float, end: float,
                silences: list[tuple[float, float]],
                min_gap: float = DEFAULT_MIN_GAP,
                pad: float = DEFAULT_PAD) -> list[tuple[float, float]]:
    """Keep-ranges after removing the middles of long silences."""
    ranges = [(start, end)]
    for gs, ge in silences:
        gap = ge - gs
        if gap <= min_gap:
            continue
        new_ranges: list[tuple[float, float]] = []
        for rs, re_ in ranges:
            # Silence fully inside this range: split it, keeping `pad`
            # of silence at each end of the gap.
            if gs > rs and ge < re_:
                if gs + pad < ge - pad:
                    new_ranges.append((rs, gs + pad))
                    new_ranges.append((ge - pad, re_))
                else:
                    new_ranges.append((rs, re_))
            # Silence touches the start: trim its middle, keep `pad`.
            elif gs <= rs and ge < re_:
                new_ranges.append((max(rs, ge - pad), re_))
            # Silence touches the end: trim its middle, keep `pad`.
            elif gs > rs and ge >= re_:
                new_ranges.append((rs, min(re_, gs + pad)))
            else:
                new_ranges.append((rs, re_))
        ranges = new_ranges
    # Drop slivers left over from trimming.
    return [(s, e) for s, e in ranges if e - s > 0.05]


def cut_segments(src: str, ranges: list[tuple[float, float]],
                 out_path: str, workdir: str, tag: str) -> str:
    """Re-encode each keep-range, concat into out_path. Returns out_path."""
    if len(ranges) == 1:
        s, e = ranges[0]
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{s:.3f}", "-to", f"{e:.3f}",
             "-i", src, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-r", "30", "-c:a", "aac", "-b:a", "128k", out_path],
            check=True)
        return out_path
    parts = []
    for i, (s, e) in enumerate(ranges):
        part = os.path.join(workdir, f"{tag}_part{i}.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{s:.3f}", "-to", f"{e:.3f}",
             "-i", src, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-r", "30", "-c:a", "aac", "-b:a", "128k", part],
            check=True)
        parts.append(part)
    lst = os.path.join(workdir, f"{tag}_concat.txt")
    with open(lst, "w", encoding="utf-8") as fh:
        for p in parts:
            fh.write(f"file '{p}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", lst, "-c", "copy", out_path],
        check=True)
    return out_path


def remap_words(words: list[dict], ranges: list[tuple[float, float]],
                clip_start: float) -> list[dict]:
    """Shift word timestamps into cut-clip time (clip starts at 0).

    ``ranges`` are absolute (source) keep-ranges, in order. A word
    keeps its time if it lies inside a kept range, shifted by the
    total removed time before it; words in removed middles are dropped.
    """
    out: list[dict] = []
    # Build (kept_start, kept_end, new_start) triples.
    triples = []
    new_t = 0.0
    for s, e in ranges:
        triples.append((s, e, new_t))
        new_t += e - s
    for w in words:
        s, e = float(w["s"]), float(w["e"])
        hit = None
        for ks, ke, ns in triples:
            if ks - 0.05 <= s < ke:
                hit = (ks, ke, ns)
                break
        if hit is None:
            continue  # word was inside a removed silence middle
        ks, _ke, ns = hit
        out.append({"w": w["w"], "s": ns + (s - ks), "e": ns + (e - ks)})
    return out


def cut_duration(ranges: list[tuple[float, float]]) -> float:
    return sum(e - s for s, e in ranges)
