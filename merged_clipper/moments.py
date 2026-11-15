"""Moment picking v2: Gemini / Ollama LLM, else a free heuristic scorer.

Pro-style rules (from the research report, §1–§2):
  * Default clip length 15-30 s, prefer 20-30 s; up to 60 s only if
    the idea earns it. Never pad to a length — cut where the thought
    ends.
  * Start/end snap to sentence boundaries using word timestamps
    (±0.1-0.3 s pad via ``snap_clip_to_words``).
  * Skip intros / filler / sponsors / tangents.
  * Zero-context test: a clip must make sense with no prior context.
    Openings on a pronoun or conjunction pointing at missing context
    are moved to the next sentence, or the window is penalised.
  * Hook in the first 1-3 s.

Scoring is a TRANSPARENT rubric, four 0-25 sub-scores totalling
0-99 — Hook / Flow / Value / Trend, copied from OpusClip's four
pillars as the report suggests. It is a RANKING AID inside one
batch, NOT a prediction of views: no vendor publishes weights or
validation, and a 92 does not reliably beat a 78. The score is
labelled that way everywhere it is shown.

The LLM prompt is the Clipper University prompt from report §2,
adapted: the niche line is generalised (any topic, not crypto
streamers) and the length rule is 15-30 s instead of 20-60 s.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import asdict, dataclass

from .vendor import clip_selection as cs

HOOK_WORDS = {
    "secret", "insane", "nobody", "never", "always", "why", "how", "stop",
    "watch", "biggest", "first", "worst", "best", "free", "mistake",
    "truth", "crazy", "shocking", "unbelievable", "exactly", "finally",
}
QUESTION_WORDS = {"what", "why", "how", "when", "who", "which", "really"}
PRACTICAL_WORDS = {
    "step", "tip", "trick", "learn", "money", "free", "save", "make",
    "build", "start", "easy", "guide", "hack", "method", "strategy",
}
EMOTIONAL_WORDS = {
    "amazing", "crazy", "unbelievable", "hilarious", "shocking", "wild",
    "incredible", "ridiculous", "brutal", "legendary", "epic", "wow",
}
TREND_WORDS = {
    "new", "just", "now", "today", "latest", "breaking", "finally",
    "announced", "released", "update",
}
FILLER_STARTS = {
    "um", "uh", "so", "and", "but", "well", "like", "yeah", "okay", "right",
}
PRONOUN_STARTS = {
    "he", "she", "they", "it", "this", "that", "these", "those", "his",
    "her", "their", "its",
}
CONTRAST_WORDS = {"but", "however", "instead", "actually", "though", "yet"}
INTRO_PATTERNS = (
    "welcome back", "welcome to", "hi folks", "hey everyone", "hey guys",
    "today on", "in this video", "sponsor", "sponsored", "subscribe",
    "like and subscribe", "smash that", "intro", "let's get into",
)


def v2_bounds(length: str = "auto") -> tuple[float, float, float, float]:
    """(min, max, ideal_min, ideal_max) seconds for v2 picking.

    auto: 15-30 s, ideal 20-30. short: 15-25. medium: 20-45 (an idea
    that earns more time). long: 30-60 — the only band that may reach
    60 s, and only when the sentences need it.
    """
    table = {
        "auto": (15.0, 30.0, 20.0, 30.0),
        "short": (15.0, 25.0, 15.0, 25.0),
        "medium": (20.0, 45.0, 25.0, 40.0),
        "long": (30.0, 60.0, 35.0, 55.0),
    }
    return table.get((length or "auto").lower(), table["auto"])


@dataclass
class Virality:
    hook: int = 0
    flow: int = 0
    value: int = 0
    trend: int = 0
    hook_type: str = "none"
    reasoning: str = ""

    @property
    def total(self) -> int:
        return max(0, min(99, self.hook + self.flow + self.value + self.trend))

    def as_dict(self) -> dict:
        d = asdict(self)
        d["total"] = self.total
        # Ranking aid, not a prediction — say so in the data itself.
        d["note"] = ("Ranking aid within this batch only, "
                     "not a prediction of views.")
        return d


def _clamp25(x: float) -> int:
    return max(0, min(25, int(round(x))))


def _score_window_text(text: str, first_text: str, energy: float,
                       n_words: int, duration: float) -> Virality:
    words = re.findall(r"[A-Za-z0-9%']+", text.lower())
    first = re.findall(r"[A-Za-z0-9%']+", first_text.lower())
    word_set = set(words)
    hook = 6.0
    if "?" in text:
        hook += 7
    if any(w in first for w in QUESTION_WORDS):
        hook += 5
    hook += 2.0 * len(word_set & HOOK_WORDS)
    if re.search(r"\d", text):
        hook += 4
    if any(w in first for w in ("you", "your")):
        hook += 3
    if first and first[0] in PRONOUN_STARTS:
        hook -= 5  # zero-context fail: opens on a pronoun
    flow = 6.0
    flow += 3.0 * len(word_set & CONTRAST_WORDS)  # setup -> payoff turn
    if re.search(r"[.!?]", text.strip()[-1:]):
        flow += 4  # ends on a resolved sentence
    if 120 <= (n_words / duration * 60 if duration else 0) <= 200:
        flow += 4  # lively conversational pace
    flow += 10.0 * min(1.0, energy)  # delivery energy
    value = 4.0
    value += 2.0 * len(word_set & PRACTICAL_WORDS)
    if re.search(r"\d", text):
        value += 4
    if any(w in word_set for w in ("story", "happened", "then", "remember")):
        value += 3
    trend = 4.0
    trend += 3.0 * len(word_set & EMOTIONAL_WORDS)
    trend += 2.0 * len(word_set & TREND_WORDS)
    if re.search(r"\d", text):
        trend += 2
    hook_type = "none"
    if "?" in first_text or any(w in first for w in QUESTION_WORDS):
        hook_type = "question"
    elif re.search(r"\d", first_text):
        hook_type = "statistic"
    elif any(w in first for w in HOOK_WORDS):
        hook_type = "statement"
    return Virality(
        hook=_clamp25(hook), flow=_clamp25(flow),
        value=_clamp25(value), trend=_clamp25(trend),
        hook_type=hook_type,
        reasoning="Heuristic score (no LLM key): Hook/Flow/Value/Trend "
                  "from wording, questions, numbers, pacing and audio "
                  "energy. Ranking aid within this batch, not a prediction.",
    )


def _sentence_spans(words: list[dict]) -> list[tuple[int, int]]:
    """Group word indices into sentences (terminal punctuation)."""
    spans: list[tuple[int, int]] = []
    start = 0
    for i, w in enumerate(words):
        if re.search(r"[.!?][\"')]*$", w["w"].strip()):
            spans.append((start, i))
            start = i + 1
    if start < len(words):
        spans.append((start, len(words) - 1))
    return spans


def _is_intro(text: str) -> bool:
    low = text.lower()
    return any(p in low for p in INTRO_PATTERNS)


def _review_opening(words: list[dict], start_idx: int, end_idx: int,
                    spans: list[tuple[int, int]]) -> int:
    """Move the start to the next sentence if it opens on filler, a
    conjunction, or a pronoun pointing at missing context — provided
    at least ~10 s of the window remains."""
    for si, (a, b) in enumerate(spans):
        if a <= start_idx <= b:
            tok = words[a]["w"].strip().lower().strip(".,!?")
            if tok in FILLER_STARTS or tok in PRONOUN_STARTS:
                if si + 1 < len(spans) and spans[si + 1][0] <= end_idx:
                    rest = words[spans[si + 1][0]]["s"]
                    if words[end_idx]["e"] - rest >= 10:
                        return spans[si + 1][0]
            return a
    return start_idx


def _build_window(words: list[dict], spans: list[tuple[int, int]],
                  first_span: int, lo: float, hi: float,
                  ideal_hi: float, energy: list[float] | None) -> dict | None:
    """Accumulate sentences from ``first_span`` until ~ideal_hi seconds.

    Stops at a sentence end inside the ideal band; extends to ``hi``
    only if the window is still under ``lo``. Returns a scored clip
    dict, or None if the window cannot reach ``lo``.
    """
    if first_span >= len(spans):
        return None
    a = spans[first_span][0]
    b = spans[first_span][1]
    si = first_span
    while True:
        dur = words[b]["e"] - words[a]["s"]
        if dur >= ideal_hi:
            break
        if si + 1 >= len(spans):
            break
        next_b = spans[si + 1][1]
        next_dur = words[next_b]["e"] - words[a]["s"]
        if next_dur > hi and dur >= lo:
            break
        si += 1
        b = next_b
        if dur >= lo and next_dur > ideal_hi:
            break
    end_t = words[b]["e"]
    start_t = words[a]["s"]
    if end_t - start_t < lo:
        return None
    a2 = _review_opening(words, a, b, spans)
    text = " ".join(w["w"] for w in words[a2:b + 1]).strip()
    if _is_intro(text) and words[a]["s"] < 30:
        return None
    first_text = " ".join(w["w"] for w in words[a2:min(a2 + 8, b + 1)])
    e = 0.0
    if energy:
        seg = energy[int(words[a2]["s"]):int(words[b]["e"]) + 1]
        e = sum(seg) / len(seg) if seg else 0.0
    v = _score_window_text(text, first_text, e, b - a2 + 1,
                           end_t - words[a2]["s"])
    return {"start": words[a2]["s"], "end": end_t, "predicted_score": v.total,
            "virality": v.as_dict(), "hook_title": first_text[:80],
            "text": text}


def heuristic_picks(transcript: dict, duration: float, n_clips: int = 3,
                    length: str = "auto",
                    energy: list[float] | None = None) -> list[dict]:
    words = transcript.get("words") or []
    if not words:
        return []
    lo, hi, _ideal_lo, ideal_hi = v2_bounds(length)
    spans = _sentence_spans(words)
    candidates = []
    for si in range(len(spans)):
        c = _build_window(words, spans, si, lo, hi, ideal_hi, energy)
        if c is None:
            continue
        s, e = cs.snap_clip_to_words(c["start"], c["end"], words,
                                     duration, lo, hi)
        if e - s < lo:
            continue
        c["start"], c["end"] = s, e
        c["text"] = " ".join(w["w"] for w in words
                             if s <= w["s"] < e).strip()
        candidates.append(c)
    candidates.sort(key=lambda c: -c["predicted_score"])
    kept = cs.dedupe_overlapping(candidates)
    kept.sort(key=lambda c: c["start"])
    return kept[:n_clips]


def pick_near(transcript: dict, duration: float, focus_times: list[float],
              length: str = "auto",
              energy: list[float] | None = None) -> list[dict]:
    """Best sentence window at/around each focus time (e.g. the
    interesting bits of a long video). One clip per focus time."""
    words = transcript.get("words") or []
    if not words:
        return []
    lo, hi, _ilo, ideal_hi = v2_bounds(length)
    spans = _sentence_spans(words)
    out = []
    for ft in focus_times:
        # Sentence span containing (or nearest before) the focus time.
        best_si = None
        for si, (a, b) in enumerate(spans):
            if words[a]["s"] <= ft <= words[b]["e"]:
                best_si = si
                break
        if best_si is None:
            best_si = min(range(len(spans)),
                          key=lambda si: abs(words[spans[si][0]]["s"] - ft))
        # Start a sentence or two before the focus so the clip has setup.
        start_si = max(0, best_si - 1)
        c = _build_window(words, spans, start_si, lo, hi, ideal_hi, energy)
        if c is None:
            continue
        s, e = cs.snap_clip_to_words(c["start"], c["end"], words,
                                     duration, lo, hi)
        c["start"], c["end"] = s, e
        c["text"] = " ".join(w["w"] for w in words
                             if s <= w["s"] < e).strip()
        out.append(c)
    # No dedupe here: each focus time asked for its own clip, even if
    # two foci are close enough that their windows overlap.
    out.sort(key=lambda c: c["start"])
    return out


_LLM_PROMPT = """You are a viral short-form editor. Analyze this timestamped transcript and find the 5-8 best self-contained moments for TikTok, Reels, Shorts, and X. Focus on: hot takes, funny reactions, emotional peaks, and quotable lines. Rules:
- Each clip 15-30 seconds (20-30 preferred; up to 60 only if the idea needs it)
- Start and end on natural sentence boundaries
- Must make sense with zero prior context
- Strong hook in the first 1-3 seconds
- Skip intros, filler, sponsors and tangents
Score each clip 0-25 on Hook (first 3s stops the scroll), Flow (logical, satisfying resolution), Value (takeaway / emotional resonance), Trend (topicality). Respond with valid JSON only: an array of objects with start, end, title, hook, flow, value, trend, hook_title, and reason.
TRANSCRIPT:
{windows}
"""


def _llm_picks_from_json(raw: str, words: list[dict], duration: float,
                         n_clips: int, lo: float, hi: float) -> list[dict]:
    data = json.loads(raw[raw.find("["):raw.rfind("]") + 1])
    out = []
    for item in data:
        s, e = cs.snap_clip_to_words(float(item["start"]), float(item["end"]),
                                     words, duration, lo, hi)
        # New rubric keys, with fallback to the v1 names.
        v = Virality(
            hook=_clamp25(item.get("hook", 0)),
            flow=_clamp25(item.get("flow", item.get("engagement", 0))),
            value=_clamp25(item.get("value", 0)),
            trend=_clamp25(item.get("trend", item.get("shareability", 0))),
            hook_type=str(item.get("hook_type", "none")),
            reasoning=str(item.get("reasoning", item.get("reason", "")))[:200],
        )
        text = " ".join(w["w"] for w in words
                         if s <= w["s"] < e).strip()
        out.append({"start": s, "end": e, "predicted_score": v.total,
                    "virality": v.as_dict(),
                    "hook_title": str(item.get("hook_title",
                                               item.get("title", "")))[:80],
                    "text": text})
    out.sort(key=lambda c: -c["predicted_score"])
    out = cs.dedupe_overlapping(out)
    out.sort(key=lambda c: c["start"])
    return out[:n_clips]


def gemini_picks(transcript: dict, duration: float, n_clips: int = 3,
                 length: str = "auto") -> list[dict]:
    """Gemini moment picking (OpenShorts approach, google-genai SDK)."""
    from google import genai  # type: ignore

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    lo, hi, _ilo, _ihi = v2_bounds(length)
    windows = cs.build_transcript_windows(transcript, duration)
    win_text = "\n".join(
        f"[{w['start']:.1f}-{w['end']:.1f}] {w['text'][:600]}" for w in windows)
    client = genai.Client(api_key=api_key)
    # Default verified 2026-11-15: gemini-2.5-flash / gemini-2.5-flash-lite
    # return 404 for new accounts (retired). gemini-3.5-flash works.
    model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
    resp = client.models.generate_content(
        model=model, contents=_LLM_PROMPT.format(windows=win_text))
    return _llm_picks_from_json(resp.text, transcript.get("words") or [],
                                duration, n_clips, lo, hi)


def ollama_picks(transcript: dict, duration: float, n_clips: int = 3,
                 length: str = "auto") -> list[dict]:
    """Local LLM moment picking via an OpenAI-compatible endpoint
    (Ollama / LM Studio / vLLM). Free, no key, runs on your own machine
    — the OpenShorts local-LLM option, same prompt as Gemini."""
    lo, hi, _ilo, _ihi = v2_bounds(length)
    windows = cs.build_transcript_windows(transcript, duration)
    win_text = "\n".join(
        f"[{w['start']:.1f}-{w['end']:.1f}] {w['text'][:600]}" for w in windows)
    base = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
    model = os.environ.get("LLM_MODEL", "llama3.1")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user",
                      "content": _LLM_PROMPT.format(windows=win_text)}],
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions",
                                 data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        payload = json.loads(resp.read())
    text = payload["choices"][0]["message"]["content"]
    return _llm_picks_from_json(text, transcript.get("words") or [],
                                duration, n_clips, lo, hi)


def pick_moments(transcript: dict, duration: float, n_clips: int = 3,
                 length: str = "auto", llm: str = "auto",
                 energy: list[float] | None = None,
                 focus_times: list[float] | None = None) -> tuple[list[dict], str]:
    """Return (clips, method_used). method is gemini / ollama / heuristic.

    With ``focus_times`` set, the best sentence window at each focus
    time is picked (heuristic scoring) instead of the global top-N.
    """
    if focus_times:
        return _focus_picks(transcript, duration, focus_times, length, energy)
    choice = (llm or "auto").lower()
    if choice in ("gemini", "auto"):
        try:
            return gemini_picks(transcript, duration, n_clips, length), "gemini"
        except Exception as exc:  # noqa: BLE001 - any LLM failure -> free fallback
            print(f"  [llm] Gemini unavailable ({exc}); using free heuristic.")
    if choice in ("ollama", "auto"):
        if os.environ.get("LLM_BASE_URL") or choice == "ollama":
            try:
                return ollama_picks(transcript, duration, n_clips, length), "ollama"
            except Exception as exc:  # noqa: BLE001
                print(f"  [llm] Ollama unavailable ({exc}); using free heuristic.")
    return heuristic_picks(transcript, duration, n_clips, length, energy), "heuristic"


def _focus_picks(transcript: dict, duration: float, focus_times: list[float],
                 length: str, energy: list[float] | None) -> tuple[list[dict], str]:
    clips = pick_near(transcript, duration, focus_times, length, energy)
    return clips, "heuristic-focus"
