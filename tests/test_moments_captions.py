"""Unit tests for the merged clipper's pure parts (no model, no ffmpeg).

Run: python -m pytest tests/   (or: python -m unittest discover tests)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from merged_clipper import captions, moments


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
    assert 15 <= picks[0]["end"] - picks[0]["start"] <= 60


def test_ass_has_karaoke_tags():
    words = _words("Hello world this is a test clip", start=1.0)
    # default template (hormozi uppercases everything, so "Hello" -> "HELLO")
    ass = captions.words_to_ass(words, clip_start=1.0, clip_end=3.4,
                                template_name="default")
    assert "PlayResX: 1080" in ass
    assert "\\k" in ass  # karaoke timing tags present
    assert "Hello" in ass
