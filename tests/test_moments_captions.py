"""Unit tests for the merged clipper's pure parts (no model, no ffmpeg).

Run: python -m pytest tests/   (or: python -m unittest discover tests)
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from merged_clipper import captions, moments, silence
from merged_clipper.face import _ema_smooth


def _words(text, start=0.0, step=0.4):
    out, t = [], start
    for w in text.split():
        out.append({"w": w, "s": t, "e": t + step})
        t += step
    return out


def test_heuristic_finds_question_hook():
    text = ("What is the biggest mistake new clippers make? "
            "They post ten boring clips and wonder why nobody watches. "
            "Here is the secret. You need one insane moment in the first "
            "three seconds, or the viewer is gone. I tested this on fifty "
            "clips and the hook made all the difference.")
    words = _words(text)
    transcript = {"text": text, "segments": [{"start": 0.0, "end": 20.0, "text": text}],
                  "words": words}
    picks = moments.heuristic_picks(transcript, 20.0, n_clips=1)
    assert len(picks) == 1
    assert picks[0]["virality"]["total"] > 0
    assert picks[0]["virality"]["hook_type"] in ("question", "statement", "statistic")
    # v2: default clips are 15-30 s, Hook/Flow/Value/Trend rubric.
    assert 15 <= picks[0]["end"] - picks[0]["start"] <= 30
    assert set(picks[0]["virality"]) >= {"hook", "flow", "value", "trend"}
    assert "not a prediction" in picks[0]["virality"]["note"]


def test_heuristic_starts_on_sentence():
    # First sentence is filler-led; the pick must not open on "So".
    text = ("So, um, here is the thing. What is the biggest mistake new "
            "clippers make? They post ten boring clips and wonder why "
            "nobody watches them at all. Here is the secret. You need one "
            "insane moment in the first three seconds, or the viewer is "
            "gone forever. I tested this on fifty clips.")
    words = _words(text)
    transcript = {"text": text, "segments": [], "words": words}
    picks = moments.heuristic_picks(transcript, 24.0, n_clips=1)
    assert len(picks) == 1
    first = picks[0]["text"].split()[0].strip(".,!?").lower()
    assert first not in ("so", "um", "and", "but", "well")


def test_ass_has_karaoke_tags():
    words = _words("Hello world this is a test clip", start=1.0)
    # default template (hormozi uppercases everything, so "Hello" -> "HELLO")
    ass = captions.words_to_ass(words, clip_start=1.0, clip_end=3.4,
                                template_name="default")
    assert "PlayResX: 1080" in ass
    assert "\\k" in ass  # karaoke timing tags present
    assert "Hello" in ass


def test_pro_ass_spec():
    """Hormozi spec: Montserrat Black, 100 px, y~1250, 1-3 words/line."""
    text = ("What is the biggest mistake new clippers make? "
            "They post ten boring clips and wonder why nobody watches.")
    words = _words(text, start=0.0)
    ass = captions.words_to_ass_pro(words, clip_start=0.0, clip_end=10.0)
    assert "Montserrat Black" in ass
    assert ",100," in ass  # Fontsize 100
    assert "MarginV" in ass and ",620,1" in ass  # band ~y1200-1300
    assert "\\k" in ass
    assert "HELLO" not in ass  # this text has no 'hello'
    assert "BIGGEST" in ass  # ALL CAPS
    assert "&H003DD9FF" in ass  # #FFD93D yellow, ASS BGR
    for line in ass.splitlines():
        if line.startswith("Dialogue:"):
            spoken = line.split(",,", 1)[-1]
            spoken = re.sub(r"\{\\k\d+\}", "", spoken)
            assert 1 <= len(spoken.split()) <= 3, spoken


def test_silence_keep_ranges():
    # 1.0 s gap in the middle: middle cut, 0.15 s pad kept each side.
    ranges = silence.keep_ranges(0.0, 10.0, [(4.0, 5.0)])
    assert ranges == [(0.0, 4.15), (4.85, 10.0)]
    # Short gap (0.2 s) is left alone.
    assert silence.keep_ranges(0.0, 10.0, [(4.0, 4.2)]) == [(0.0, 10.0)]
    # Gap touching the start is trimmed to a 0.15 s pad.
    assert silence.keep_ranges(0.0, 10.0, [(0.0, 1.0)]) == [(0.85, 10.0)]


def test_silence_remap_words():
    words = [{"w": "hello", "s": 1.0, "e": 1.4},
             {"w": "world", "s": 6.0, "e": 6.4}]
    ranges = [(0.0, 4.15), (4.85, 10.0)]
    out = silence.remap_words(words, ranges, 0.0)
    assert out[0] == {"w": "hello", "s": 1.0, "e": 1.4}
    # "world" was at 6.0, after a 0.7 s cut -> 5.3 in cut time.
    assert abs(out[1]["s"] - 5.3) < 1e-6
    assert abs(out[1]["e"] - 5.7) < 1e-6


def test_face_ema_deadband():
    # Small jitter (< 0.02) is ignored; a real move is followed smoothly.
    raw = [0.50, 0.51, 0.49, 0.50, 0.80, 0.81, 0.80]
    out = _ema_smooth(raw)
    assert out[3] == 0.50
    assert 0.50 < out[4] < 0.80  # EMA, not a jump
    assert out[-1] > 0.70  # converges toward the new position


def test_focus_picks_near_times():
    text = ("First topic here. " * 20 + "Second topic here. " * 20
            + "Third topic here. " * 20)
    words = _words(text, step=0.4)
    transcript = {"text": text, "segments": [], "words": words}
    picks = moments.pick_near(transcript, 72.0, [4.0, 36.0, 50.0])
    assert len(picks) == 3
    for p in picks:
        assert 15 <= p["end"] - p["start"] <= 30
