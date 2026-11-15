"""Publishing helpers — SupoClip's direct-publishing concept, kept honest.

SupoClip can post/schedule to YouTube, TikTok and Instagram from its
full app (OAuth accounts connected in its dashboard). This lightweight
CLI does NOT fake that: there is no free official upload API for
personal TikTok/Instagram accounts, and YouTube upload needs an
OAuth client the user creates in Google Cloud.

What this module does instead (and what is actually useful):
1. Generate per-clip publish metadata (title, description, hashtags,
   tags) from the clip's hook + transcript, ready to paste.
2. ``publish --dry-run`` prints the exact manual steps for each
   platform, so nothing is posted by accident.
3. A performance log (SupoClip's performance loop, manual version):
   ``track.csv`` where the user records views per clip; the next run
   can read it and boost the hook words that performed.

Real auto-upload is listed as NOT implemented in README/TEST_REPORT.
"""

from __future__ import annotations

import csv
import json
import os

HASHTAGS = ["#shorts", "#viral", "#clips"]


def clip_metadata(clip: dict, source_title: str = "") -> dict:
    title = (clip.get("hook_title") or "Watch this").strip()
    if len(title) > 95:
        title = title[:92].rsplit(" ", 1)[0] + "..."
    virality = clip.get("virality", {})
    desc_lines = [
        title,
        "",
        (clip.get("text") or "")[:400],
        "",
        " ".join(HASHTAGS),
    ]
    if source_title:
        desc_lines.append(f"Source: {source_title}")
    return {
        "title": title,
        "description": "\n".join(desc_lines),
        "hashtags": HASHTAGS,
        "tags": ["shorts", "clips", "viral"],
        "virality_total": virality.get("total"),
        "virality_breakdown": virality,
        "start": clip.get("start"),
        "end": clip.get("end"),
    }


def write_metadata(outdir: str, clips: list[dict],
                   source_title: str = "") -> str:
    path = os.path.join(outdir, "publish_metadata.json")
    data = [clip_metadata(c, source_title) for c in clips]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    return path


def dry_run_steps(clip_file: str) -> list[str]:
    return [
        f"1. Open TikTok / Reels / Shorts and upload {clip_file}.",
        "2. Paste the title + description from publish_metadata.json.",
        "3. Turn ON the paid-partnership / branded-content toggle and add #ad",
        "   if this clip is for a paid campaign (Content Rewards / Vyro / etc.).",
        "4. Post natively (do NOT re-upload a TikTok download with its",
        "   watermark to another platform).",
        "5. Submit the post link to the campaign within 30 minutes.",
        "6. Log the views in track.csv (see README) so the next run can",
        "   learn which hooks performed.",
    ]


def log_performance(track_csv: str, clip_file: str, views: int,
                    platform: str = "tiktok") -> str:
    new = not os.path.exists(track_csv)
    with open(track_csv, "a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if new:
            writer.writerow(["clip_file", "platform", "views"])
        writer.writerow([clip_file, platform, views])
    return track_csv
