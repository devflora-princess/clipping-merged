"""Karaoke-style burned-in captions (ASS subtitles for ffmpeg/libass).

Style comes from SupoClip's vendored caption templates
(``vendor/supoclip_caption_templates.py``: default / hormozi / mrbeast /
minimal / tiktok / neon / podcast) and the word annotations from its
vendored ``supoclip_emoji_captions.py`` (rate-limited emoji + emphasis
on power words and numbers). The ASS writing itself is new glue code,
in the Hormozi style both parent projects use: word-by-word highlight,
a few words per line, big bold text near the bottom of the 9:16 frame.
"""

from __future__ import annotations

import os
import re

from .vendor.supoclip_caption_templates import get_template
from .vendor.supoclip_emoji_captions import annotate_caption_words

# --- v2 "pro" caption spec (Hormozi, report §4.2) -----------------------
# 1080x1920 canvas. Montserrat Black, ALL CAPS, ~100 px, white fill,
# ONE yellow keyword per line is the *look* — here the karaoke sweep
# itself provides the yellow. VERIFIED against rendered frames in
# this VM's libass: with \k tags, text BEFORE the sweep position is
# drawn in PrimaryColour and text AFTER it in SecondaryColour — i.e.
# already-spoken words are Primary, upcoming words are Secondary.
# So PrimaryColour is #FFD93D (yellow = spoken) and SecondaryColour
# is white (upcoming). Swapping them makes the highlight run
# backwards (upcoming words yellow, spoken words white).
# Black stroke 10 px. Band centred on y≈1250 (middle third, NOT
# bottom-anchored — bottom captions are the single most frequent
# error in the research). 1-3 words per line, broken on sentence ends.
PRO_FONT = "Montserrat Black"
PRO_FONT_SIZE = 100
PRO_OUTLINE_W = 10
PRO_MARGIN_V = 620  # Alignment 2 (bottom-centre): band sits ~y1200-1300
PRO_PRIMARY = "&H003DD9FF"    # #FFD93D yellow — spoken words
PRO_SECONDARY = "&H00FFFFFF"  # white — upcoming words
PRO_OUTLINE = "&H00000000"    # black


def _chunk_words_pro(words: list[dict]) -> list[list[dict]]:
    """Group words into 1-3-word caption lines, breaking on sentence ends."""
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        cur.append(w)
        ends_sentence = bool(re.search(
            r"[.!?][\"')]*$", w["w"].strip()))
        if len(cur) >= 3 or ends_sentence:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def words_to_ass_pro(words: list[dict], clip_start: float,
                     clip_end: float) -> str:
    r"""Hormozi-spec ASS for one clip, from REAL word timestamps.

    ``words`` are transcript words inside the clip ({"w","s","e"}),
    already remapped to cut-clip time if silence cuts ran. Times are
    shifted so the clip starts at 0. No evenly-divided timings, no
    emoji: the \k karaoke tags carry each word's true duration.
    """
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n"
        "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,{PRO_FONT},{PRO_FONT_SIZE},{PRO_PRIMARY},"
        f"{PRO_SECONDARY},{PRO_OUTLINE},&H80000000,-1,0,0,0,100,100,0,0,1,"
        f"{PRO_OUTLINE_W},0,2,60,60,{PRO_MARGIN_V},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )
    lines = [header]
    for chunk in _chunk_words_pro(words):
        if not chunk:
            continue
        start = chunk[0]["s"] - clip_start
        end = chunk[-1]["e"] - clip_start
        parts = []
        for w in chunk:
            dur_cs = max(1, int(round((w["e"] - w["s"]) * 100)))
            text = w["w"].strip().upper().replace("{", "").replace("}", "")
            parts.append(f"{{\\k{dur_cs}}}{text}")
        lines.append(
            f"Dialogue: 0,{_fmt_time(start)},{_fmt_time(end)},Cap,,0,0,0,,"
            + " ".join(parts) + "\n"
        )
    return "".join(lines)


def _hex_to_ass_bgr(hex_color: str) -> str:
    h = (hex_color or "#FFFFFF").lstrip("#")
    if len(h) == 8:  # AARRGGBB -> drop alpha
        h = h[2:]
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _fmt_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def words_to_ass(words: list[dict], clip_start: float, clip_end: float,
                 template_name: str = "hormozi") -> str:
    """Build an ASS subtitle file for one clip.

    ``words`` are transcript words inside the clip ({"w","s","e"}).
    Times are shifted so the clip starts at 0. The active word is
    highlighted in the template's highlight colour; the rest stays
    white. Emoji annotations (SupoClip) are appended to matching words.
    """
    tpl = get_template(template_name)
    max_words = int(tpl.get("max_words_per_line", 4))
    font_size = int(tpl.get("font_size", 32))
    # ASS is 1080x1920; scale the template's 720p-ish sizes up.
    font_size = int(font_size * 1.9)
    primary = _hex_to_ass_bgr(str(tpl.get("font_color", "#FFFFFF")))
    highlight = _hex_to_ass_bgr(str(tpl.get("highlight_color", "#FFE000")))
    outline = _hex_to_ass_bgr(str(tpl.get("stroke_color", "#000000")))
    outline_w = int(tpl.get("stroke_width", 3))
    margin_v = int((1.0 - float(tpl.get("position_y", 0.80))) * 1920)
    uppercase = bool(tpl.get("uppercase", False))

    ann_words = [{"text": w["w"]} for w in words]
    emoji_by_idx, emphasis = annotate_caption_words(ann_words)

    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n"
        "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,DejaVu Sans,{font_size},{primary},{highlight},{outline},"
        f"&H80000000,-1,0,0,0,100,100,0,0,1,{outline_w},0,2,60,60,{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text\n"
    )
    lines = [header]
    for i in range(0, len(words), max_words):
        chunk = words[i:i + max_words]
        if not chunk:
            continue
        start = chunk[0]["s"] - clip_start
        end = chunk[-1]["e"] - clip_start
        parts = []
        for k, w in enumerate(chunk):
            dur_cs = max(1, int(round((w["e"] - w["s"]) * 100)))
            text = w["w"].strip()
            if uppercase:
                text = text.upper()
            if (i + k) in emoji_by_idx:
                text += emoji_by_idx[i + k]
            text = text.replace("{", "").replace("}", "")
            colour = highlight if (i + k) in emphasis else primary
            # \k = karaoke timing; colour switch highlights the active word.
            parts.append(f"{{\\k{dur_cs}\\1c{colour}}}{text}")
        lines.append(
            f"Dialogue: 0,{_fmt_time(start)},{_fmt_time(end)},Cap,,0,0,0,,"
            + " ".join(parts) + "\n"
        )
    return "".join(lines)


def write_ass(path: str, content: str) -> str:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path
