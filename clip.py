#!/usr/bin/env python3
"""Merged free clipper — CLI entry point.

    python clip.py <video-file-or-URL> [--clips 3] [--out out/]
    python clip.py edit --input clip.mp4 --start 5 --end 35 --out cut.mp4
    python clip.py publish --dry-run out/clip01.mp4

No API key needed for the core path: local Whisper + heuristic
scoring. Set GEMINI_API_KEY for Gemini moment picking, or
LLM_BASE_URL for a local Ollama model. See README.md.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from merged_clipper import __version__  # noqa: E402
from merged_clipper.pipeline import run  # noqa: E402
from merged_clipper.publish import dry_run_steps  # noqa: E402


def _edit(args: argparse.Namespace) -> int:
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-ss", str(args.start), "-to", str(args.end), "-i", args.input,
           "-c", "copy", args.out]
    subprocess.run(cmd, check=True)
    print(f"Trimmed {args.input} [{args.start}-{args.end}s] -> {args.out}")
    print("Note: -c copy trims on keyframes; re-encode for frame accuracy:")
    print(f"  ffmpeg -ss {args.start} -to {args.end} -i {args.input} "
          f"-c:v libx264 -c:a aac {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="clip.py",
        description=f"Merged free clipper v{__version__} "
                    "(OpenShorts + opensource-clipping + SupoClip, AGPL-3.0).")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("clip", help="Cut clips from a video/URL (default).")
    p.add_argument("input", help="Local video file, or YouTube/Twitch/Kick URL.")
    p.add_argument("--clips", type=int, default=3)
    p.add_argument("--out", default="out")
    p.add_argument("--length", default="auto",
                   choices=["auto", "short", "medium", "long"],
                   help="SupoClip clip-length preset.")
    p.add_argument("--whisper-model", default="base",
                   help="tiny|base|small|medium|large-v3 (CPU: tiny/base fastest).")
    p.add_argument("--llm", default="auto",
                   choices=["auto", "gemini", "ollama", "none"])
    p.add_argument("--template", default="hormozi",
                   choices=["default", "hormozi", "mrbeast", "minimal",
                            "tiktok", "neon", "podcast"],
                   help="SupoClip caption template.")
    p.add_argument("--transcript",
                   help="Skip transcription; use this transcript JSON.")
    p.add_argument("--no-hook", action="store_true",
                   help="Skip the 3-second hook teaser overlay.")

    e = sub.add_parser("edit", help="Trim a rendered clip (built-in editor).")
    e.add_argument("--input", required=True)
    e.add_argument("--start", type=float, required=True)
    e.add_argument("--end", type=float, required=True)
    e.add_argument("--out", required=True)

    pb = sub.add_parser("publish", help="Publishing checklist (dry run).")
    pb.add_argument("--dry-run", action="store_true", default=True)
    pb.add_argument("clip", help="Clip file to publish.")

    args = parser.parse_args(argv)
    if args.command == "edit":
        return _edit(args)
    if args.command == "publish":
        for step in dry_run_steps(args.clip):
            print(step)
        print("\nDirect auto-posting is NOT implemented in this CLI — "
              "see README.md (Publishing).")
        return 0
    if args.command == "clip" or args.command is None:
        if args.command is None:
            # Allow `clip.py <input>` with no subcommand.
            args = parser.parse_args(["clip"] + (argv or []))
        run(args.input, args.out, n_clips=args.clips, length=args.length,
            whisper_model=args.whisper_model, llm=args.llm,
            template=args.template, transcript_path=args.transcript,
            no_hook=args.no_hook)
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
