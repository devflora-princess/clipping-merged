"""Rendering: ffmpeg-only 9:16 clips + thumbnails.

Layout is the OpenShorts GENERAL layout (blurred background fill with
the source centred on top) when no face track is available, and a
face-tracked crop (opensource-clipping style) when face.py returns
centres. Karaoke captions are burned in with libass. The first 3
seconds carry the opensource-clipping cinematic hook teaser: dark
cinema bars top/bottom plus the hook line in big bold text.

Everything here is one ffmpeg call per clip — no MoviePy, no torch.
"""

from __future__ import annotations

import os
import subprocess

W, H = 1080, 1920
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _font_file() -> str:
    if os.path.exists(FONT):
        return FONT
    return "DejaVu Sans"


def _escape_drawtext(text: str) -> str:
    return (text.replace("\\", "\\\\").replace("'", "")
            .replace(":", "\\:").replace("%", "\\%"))


def _ass_path_for_filter(ass_path: str) -> str:
    # libass filter argument: escape : and ' in the path.
    return ass_path.replace("\\", "/").replace(":", "\\:").replace("'", "")


def render_clip(src: str, start: float, end: float, ass_path: str | None,
                out_path: str, hook_text: str = "",
                face_centres: float | None = None,
                hook_seconds: float = 3.0) -> str:
    """Render one vertical clip. Returns the output path.

    ``face_centres`` is the median face centre (0..1) over the clip's
    seconds, from face.face_centres(), or None for a centred crop.
    """
    duration = end - start
    # Blurred-background layout (OpenShorts GENERAL):
    #   split -> bg scaled up + cropped + blurred; fg scaled to width.
    # If a face track exists, pan the foreground crop instead: crop a
    # 1080-wide window out of a 1920-high scaled frame, x following the
    # face centre. (Implemented as a crop on the foreground branch.)
    if face_centres:
        # Face-tracked crop (opensource-clipping style): the pipeline
        # passes the MEDIAN face centre over this clip's seconds, so the
        # 1080-wide window is centred on the speaker instead of the
        # middle of the frame. One fixed window per clip — no fake pan.
        cx = min(1.0, max(0.0, float(face_centres)))
        fg_filter = (
            "scale=-2:1920,"
            f"crop=1080:1920:x='(in_w-1080)*{cx:.3f}':y=0"
        )
        video_chain = (
            "[0:v]split[a][b];"
            "[a]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,gblur=sigma=40[bg];"
            f"[b]{fg_filter}[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p"
        )
    else:
        video_chain = (
            "[0:v]split[a][b];"
            "[a]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,gblur=sigma=40[bg];"
            "[b]scale=1080:-2[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p"
        )
    # Chain the stages as separate filtergraph links ending in [vout],
    # and map ONLY [vout] + audio. (An earlier version mapped 0:v, which
    # silently kept the raw source as a second video stream in the MP4.)
    stages = [video_chain + "[v0]"]
    if ass_path:
        stages.append(f"[v0]ass={_ass_path_for_filter(ass_path)}[v1]")
        last = "v1"
    else:
        last = "v0"
    if hook_text:
        # One line at fontsize 48 fits ~30 chars across 1080px; truncate.
        # (Earlier runs overflowed at 64pt/90 chars and at 56pt/38 chars.)
        short = hook_text if len(hook_text) <= 30 else hook_text[:27].rsplit(" ", 1)[0] + "..."
        safe = _escape_drawtext(short)
        font = _font_file()
        font_opt = f"fontfile={font}:" if os.path.exists(font) else ""
        # Cinema bars + hook line, only for the first `hook_seconds`.
        stages.append(
            f"[{last}]drawbox=x=0:y=0:w=iw:h=140:color=black@0.75:t=fill:"
            f"enable='between(t,0,{hook_seconds})',"
            f"drawbox=x=0:y=ih-140:w=iw:h=140:color=black@0.75:t=fill:"
            f"enable='between(t,0,{hook_seconds})',"
            f"drawtext={font_opt}text='{safe}':fontsize=48:"
            f"fontcolor=white:borderw=3:bordercolor=black:"
            f"x=(w-text_w)/2:y=40:enable='between(t,0,{hook_seconds})'[vout]"
        )
        last = "vout"
    else:
        stages.append(f"[{last}]null[vout]")
    filter_complex = ";".join(stages)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", src,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-r", "30", "-c:a", "aac", "-b:a", "128k",
        "-t", f"{duration:.3f}", "-movflags", "+faststart", out_path,
    ]
    subprocess.run(cmd, check=True)
    return out_path


def grab_frame(src: str, t: float, out_path: str) -> str:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", src,
         "-frames:v", "1", "-q:v", "2", out_path],
        check=True)
    return out_path


def make_thumbnail(frame_path: str, title: str, out_path: str) -> str:
    """Auto-thumbnail (opensource-clipping idea): frame + dark overlay
    + large title text. Pillow only; font falls back gracefully on
    Windows (Victor's laptop) and Linux."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(frame_path).convert("RGB")
    img = img.resize((W, H))
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([0, H - 620, W, H], fill=(0, 0, 0, 170))
    img = Image.alpha_composite(img.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(img)
    font = None
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/Arial.ttf",
    ):
        try:
            font = ImageFont.truetype(candidate, 84)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    words = (title or "WATCH THIS").split()
    lines, cur = [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) > W - 120 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    y = H - 580
    for line in lines[:3]:
        draw.text((60, y), line, font=font, fill=(255, 255, 255),
                  stroke_width=3, stroke_fill=(0, 0, 0))
        y += 110
    img.convert("RGB").save(out_path, quality=90)
    return out_path
