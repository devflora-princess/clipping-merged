"""Input handling: local file passthrough, or download via yt-dlp.

yt-dlp is the same downloader both parent projects use. Only URLs the
user has permission to clip (a campaign brief, their own video, a
Creative Commons / public-domain source) should be fed in here — the
tool does not create a licence.
"""

from __future__ import annotations

import os
import subprocess


def resolve_input(source: str, workdir: str) -> str:
    """Return a local video file path for a URL or a local path."""
    if os.path.isfile(source):
        return os.path.abspath(source)
    if source.startswith(("http://", "https://")):
        return download_url(source, workdir)
    raise FileNotFoundError(f"Input not found and not a URL: {source}")


def download_url(url: str, workdir: str) -> str:
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:  # pragma: no cover - exercised in README, not tests
        raise RuntimeError(
            "yt-dlp is not installed. Install it with: pip install yt-dlp"
        ) from exc

    outtmpl = os.path.join(workdir, "source.%(ext)s")
    opts = {
        # Cap at 720p: plenty for a 1080x1920 vertical crop, far less
        # CPU/disk than 1080p+ on a laptop with no GPU.
        "format": "bv*[height<=720]+ba/b[height<=720]/b",
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
    if not os.path.exists(path):  # merged file lands as .mp4
        path = os.path.splitext(path)[0] + ".mp4"
    return path


def probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def probe_size(path: str) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    )
    w, h = out.stdout.strip().split(",")
    return int(w), int(h)
