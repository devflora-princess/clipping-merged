"""Rendering v2 (pro style) + v1 classic fallback.

Pro layout (from the research report):
  * 1080x1920 9:16, critical content inside the universal safe box
    x90-990 / y260-1660.
  * Face-tracked crop when a face track exists: the crop window pans
    with the speaker (0.5 s samples, EMA + deadband in face.py,
    quantised to 2 s steps for the ffmpeg expression). No face ->
    blurred-background fill (OpenShorts GENERAL layout).
  * Punch-in zoom x1.18-1.22 on emphasised/keyword words, crop
    centred high at 40% of frame height (y = 0.4*1920 - h/2).
    Implemented with ``zoompan`` (per-frame zoom) — ffmpeg's ``crop``
    evaluates w/h once at init, so a time-varying crop size silently
    does nothing (verified 2026-11-15).
  * Hook overlay: 6-10 words at y280-500 from frame 0 for 2.5 s, no
    fade-in. Frame 0 already shows face + hook text, so it IS the
    Shorts thumbnail.
  * Karaoke captions via ffmpeg ``ass=`` + libass, fonts from the
    bundled ``fonts/`` dir (Montserrat Black). If the ffmpeg build
    has no ``ass`` filter, rendering fails with a clear message
    telling the user to install a full ffmpeg build (gyan.dev full
    on Windows) — the Pillow per-word fallback is NOT implemented.
  * Optional music bed: a supplied music file is ducked under the
    voice with ``sidechaincompress``.

Classic style (``--style classic``) keeps the exact v1 behaviour:
centred/blurred crop, cinema bars + hook line for 3 s, SupoClip
caption templates. One ffmpeg call per clip in both styles.
"""

from __future__ import annotations

import os
import subprocess
import textwrap

W, H = 1080, 1920
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
ZOOM_FACTOR = 1.20  # research: x1.18-1.22 on emphasised words
HOOK_SECONDS = 2.5


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fonts_dir() -> str:
    return os.path.join(_repo_root(), "fonts")


def _font_file(name: str) -> str | None:
    for cand in (
        os.path.join(fonts_dir(), name),
        os.path.join(fonts_dir(), "Anton-Regular.ttf"),
        FONT,
    ):
        if os.path.exists(cand):
            return cand
    return None


def _montserrat() -> str | None:
    return _font_file("Montserrat-Black.ttf")


def _escape_drawtext(text: str) -> str:
    # drawtext text is wrapped in single quotes; a raw ' breaks the
    # filter parser (and \' breaks it at the graph level), so use the
    # typographic apostrophe U+2019 — renders identically in
    # Montserrat Black. (Previously apostrophes were stripped —
    # "That's" rendered as "Thats".)
    return (text.replace("\\", "\\\\").replace("'", "\u2019")
            .replace(":", "\\:").replace("%", "\\%"))


def _ass_path_for_filter(ass_path: str) -> str:
    # libass filter argument: escape : and ' in the path.
    return ass_path.replace("\\", "/").replace(":", "\\:").replace("'", "")


def _nested_if_expr(times: list[float], values: list[float]) -> str:
    """Step expression over time: value[i] while t < times[i+1].

    ``times`` are segment start times, ``values`` the value per
    segment. Built as nested if(lt(t,...)) so ffmpeg evaluates it
    per frame. Kept short by quantising to 2 s steps (see face.py).
    """
    if not values:
        return "0.5"
    expr = f"{values[-1]:.3f}"
    for t, v in reversed(list(zip(times[1:], values[:-1]))):
        expr = f"if(lt(t,{t:.2f}),{v:.3f},{expr})"
    return expr


def _zoom_expr(windows: list[tuple[float, float]],
                 tvar: str = "on/30") -> str | None:
    """Zoom factor over time: ZOOM_FACTOR inside keyword windows, else 1.

    ``tvar`` is the time variable for the filter that uses it.
    NOTE: ffmpeg's ``crop`` evaluates w/h ONCE at init, so a
    time-varying crop size silently does nothing (verified 2026-11-15:
    identical frames in/out of a zoom window). Punch zoom therefore
    uses ``zoompan``, whose ``zoom`` is evaluated per frame; its time
    variable is the output frame number ``on`` at 30 fps.
    """
    if not windows:
        return None
    expr = "1"
    for s, e in reversed(windows):
        expr = f"if(between({tvar},{s:.2f},{e:.2f}),{ZOOM_FACTOR},{expr})"
    return expr


def _hook_lines(hook_text: str, max_words: int = 10) -> list[str]:
    words = (hook_text or "").split()[:max_words]
    if not words:
        return []
    # ~22 chars/line at 64 pt Montserrat Black across 1080 px.
    return textwrap.wrap(" ".join(words), width=22)[:2]


def _reframe_chain(src_is_face: bool, face_centres) -> str:
    """Build the 1080x1920 canvas. Returns filter ending in [v0]."""
    if src_is_face and face_centres:
        from .face import quantize_centres

        if isinstance(face_centres, (int, float)):
            cx = min(1.0, max(0.0, float(face_centres)))
            x_expr = f"(iw-1080)*{cx:.3f}"
        else:
            centres = quantize_centres(list(face_centres))
            times = [i * 2.0 for i in range(len(centres))]
            x_expr = f"(iw-1080)*({_nested_if_expr(times, centres)})"
        return (
            "[0:v]scale=-2:1920,"
            f"crop=1080:1920:x='{x_expr}':y=0,"
            "format=yuv420p[v0]"
        )
    # Blurred-background fill (no face / slides / gameplay).
    return (
        "[0:v]split[a][b];"
        "[a]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,gblur=sigma=40[bg];"
        "[b]scale=1080:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v0]"
    )


def _ass_stage(last: str, ass_path: str | None) -> tuple[str, str]:
    if not ass_path:
        return last, ""
    stage = (f"[{last}]ass={_ass_path_for_filter(ass_path)}"
             f":fontsdir={_ass_path_for_filter(fonts_dir())}")
    return "v1", stage


def render_clip(src: str, start: float, end: float, ass_path: str | None,
                out_path: str, hook_text: str = "",
                face_centres: float | list[float] | None = None,
                hook_seconds: float = HOOK_SECONDS,
                style: str = "pro",
                zoom_windows: list[tuple[float, float]] | None = None,
                music_path: str | None = None) -> str:
    """Render one vertical clip. Returns the output path."""
    if style == "classic":
        return _render_classic(src, start, end, ass_path, out_path,
                               hook_text, face_centres)
    duration = end - start
    stages: list[str] = [_reframe_chain(bool(face_centres), face_centres)]
    last = "v0"
    # Punch-in zoom via zoompan (per-frame zoom; crop w/h are init-time
    # only in ffmpeg and silently ignore time-varying sizes — see
    # _zoom_expr). Zoom centred, high at 40% of frame height.
    zexpr = _zoom_expr(zoom_windows or [])
    if zexpr:
        stages.append(
            f"[{last}]fps=30,zoompan=z='{zexpr}':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='max(0,ih*0.4-(ih/zoom/2))':d=1:s=1080x1920:fps=30[vz]"
        )
        last = "vz"
    # Karaoke captions (libass).
    if ass_path:
        new_last, stage = _ass_stage(last, ass_path)
        stages.append(stage + f"[{new_last}]")
        last = new_last
    # Hook overlay: solid bar + 6-10 words at y280-500, 0-2.5 s.
    if hook_text:
        lines = _hook_lines(hook_text)
        font = _montserrat()
        font_opt = f"fontfile={font}:" if font else ""
        bar_h = 100 + 90 * len(lines)
        chain = (
            f"[{last}]drawbox=x=0:y=260:w=iw:h={bar_h}:color=black@0.55:"
            f"t=fill:enable='between(t,0,{hook_seconds})'"
        )
        for i, line in enumerate(lines):
            chain += (
                f",drawtext={font_opt}text='{_escape_drawtext(line)}':"
                f"fontsize=64:fontcolor=white:borderw=4:bordercolor=black:"
                f"x=(w-text_w)/2:y={290 + i * 90}:"
                f"enable='between(t,0,{hook_seconds})'"
            )
        chain += "[vout]"
        stages.append(chain)
        last = "vout"
    else:
        stages.append(f"[{last}]null[vout]")
    filter_complex = ";".join(stages)
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", src]
    audio_map = ["-map", "[vout]", "-map", "0:a?"]
    if music_path:
        cmd += ["-stream_loop", "-1", "-i", music_path]
        # Duck the music under the voice, then mix voice + ducked music.
        filter_complex += (
            ";[1:a]volume=0.25[mus];"
            "[mus][0:a]sidechaincompress=threshold=0.02:ratio=4:"
            "attack=200:release=1000[duck];"
            "[0:a][duck]amix=inputs=2:duration=first:normalize=0[aout]"
        )
        audio_map = ["-map", "[vout]", "-map", "[aout]"]
    cmd += ["-filter_complex", filter_complex] + audio_map + [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-r", "30", "-c:a", "aac", "-b:a", "128k",
        "-t", f"{duration:.3f}", "-movflags", "+faststart", out_path,
    ]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "ffmpeg render failed. If the error mentions the 'ass' "
            "filter, your ffmpeg build has no libass — install a full "
            "ffmpeg build (Windows: gyan.dev 'full' build; "
            "macOS: brew install ffmpeg; Ubuntu: apt install ffmpeg). "
            "The Pillow per-word caption fallback is not implemented."
        ) from exc
    return out_path


def _render_classic(src: str, start: float, end: float, ass_path: str | None,
                    out_path: str, hook_text: str = "",
                    face_centres=None) -> str:
    """v1 behaviour, unchanged: blurred/faced crop + 3 s cinema bars."""
    duration = end - start
    if face_centres:
        cx = min(1.0, max(0.0, float(face_centres)
                          if isinstance(face_centres, (int, float))
                          else 0.5))
        fg_filter = ("scale=-2:1920,"
                     f"crop=1080:1920:x='(in_w-1080)*{cx:.3f}':y=0")
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
    stages = [video_chain + "[v0]"]
    if ass_path:
        stages.append(f"[v0]ass={_ass_path_for_filter(ass_path)}[v1]")
        last = "v1"
    else:
        last = "v0"
    if hook_text:
        short = hook_text if len(hook_text) <= 30 else hook_text[:27].rsplit(" ", 1)[0] + "..."
        safe = _escape_drawtext(short)
        font = _font_file("DejaVuSans-Bold.ttf") or FONT
        font_opt = f"fontfile={font}:" if os.path.exists(font) else ""
        stages.append(
            f"[{last}]drawbox=x=0:y=0:w=iw:h=140:color=black@0.75:t=fill:"
            f"enable='between(t,0,3.0)',"
            f"drawbox=x=0:y=ih-140:w=iw:h=140:color=black@0.75:t=fill:"
            f"enable='between(t,0,3.0)',"
            f"drawtext={font_opt}text='{safe}':fontsize=48:"
            f"fontcolor=white:borderw=3:bordercolor=black:"
            f"x=(w-text_w)/2:y=40:enable='between(t,0,3.0)'[vout]"
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
    """Auto-thumbnail: frame + dark overlay + large title text."""
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
        os.path.join(fonts_dir(), "Montserrat-Black.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
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
