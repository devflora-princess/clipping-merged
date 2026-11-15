"""Moment picking: Gemini / Ollama LLM, else a free heuristic scorer.

Scoring model follows SupoClip's ViralityAnalysis breakdown
(backend/src/ai.py): every clip gets four 0-25 sub-scores — hook,
engagement, value, shareability — totalling 0-100, plus a hook type
and a short reasoning string. The LLM path asks the model for that
breakdown; the heuristic path computes the same four numbers from
the transcript text, word timing and audio energy, so the output
shape is identical with or without a key.

Clip-length presets come from SupoClip (vendored
``vendor/supoclip_clip_duration.py``); window building, word snapping
and dedup come from OpenShorts (vendored ``vendor/clip_selection.py``).
The opening review (don't start on filler / a fragment / a
conjunction pointing at missing context) re-implements the idea of
SupoClip's clip_openings.py in a few lines, without importing its
FastAPI-tied helpers.
"""

from __future__ import annotations

import json
import math
import os
import re
import urllib.request
from dataclasses import asdict, dataclass

from .vendor import clip_selection as cs
from .vendor.supoclip_clip_duration import clip_duration as supo_duration
from .vendor.supoclip_clip_duration import normalize_clip_length

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
FILLER_STARTS = {
    "um", "uh", "so", "and", "but", "well", "like", "yeah", "okay", "right",
}
CONTRAST_WORDS = {"but", "however", "instead", "actually", "though", "yet"}


@dataclass
class Virality:
    hook: int = 0
    engagement: int = 0
    value: int = 0
    shareability: int = 0
    hook_type: str = "none"
    reasoning: str = ""

    @property
    def total(self) -> int:
        return self.hook + self.engagement + self.value + self.shareability

    def as_dict(self) -> dict:
        d = asdict(self)
        d["total"] = self.total
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
    engagement = 5.0
    engagement += 2.0 * text.count("!")
    engagement += 2.0 * text.count("?")
    engagement += 10.0 * min(1.0, energy)
    wpm = (n_words / duration * 60) if duration else 0
    if 120 <= wpm <= 200:  # lively conversational pace
        engagement += 4
    value = 4.0
    value += 2.0 * len(word_set & PRACTICAL_WORDS)
    if re.search(r"\d", text):
        value += 4
    value += 1.5 * sum(1 for w in words if w[0:1].isupper() and len(w) > 2)
    shareability = 4.0
    shareability += 3.0 * len(word_set & EMOTIONAL_WORDS)
    shareability += 3.0 * len(word_set & CONTRAST_WORDS)
    if any(w in word_set for w in ("story", "happened", "then", "remember")):
        shareability += 3
    hook_type = "none"
    if "?" in first_text or any(w in first for w in QUESTION_WORDS):
        hook_type = "question"
    elif re.search(r"\d", first_text):
        hook_type = "statistic"
    elif any(w in first for w in HOOK_WORDS):
        hook_type = "statement"
    return Virality(
        hook=_clamp25(hook), engagement=_clamp25(engagement),
        value=_clamp25(value), shareability=_clamp25(shareability),
        hook_type=hook_type,
        reasoning="Heuristic score (no LLM key): hook/engagement/value/"
                  "shareability from wording, questions, numbers and audio energy.",
    )


def _review_opening(words: list[dict], start_idx: int, end_idx: int) -> int:
    """SupoClip clip_openings idea: don't open on filler or a fragment.

    If the window opens on a filler word / conjunction, move the start
    to the next word that begins a self-contained chunk, provided at
    least ~10 s of the window remains. Returns the (possibly new) index.
    """
    i = start_idx
    while i < end_idx - 3:
        tok = words[i]["w"].strip().lower().strip(".,!?")
        if tok in FILLER_STARTS:
            i += 1
            continue
        break
    return i


def heuristic_picks(transcript: dict, duration: float, n_clips: int = 3,
                    length: str = "auto",
                    energy: list[float] | None = None) -> list[dict]:
    words = transcript.get("words") or []
    if not words:
        return []
    preset = supo_duration(normalize_clip_length(length))
    lo, hi = preset.minimum, preset.maximum
    ideal_lo, ideal_hi = preset.ideal_minimum, preset.ideal_maximum
    min_d, max_d = cs.clip_duration_bounds()
    lo, hi = max(lo, min_d), min(hi, max_d)

    candidates = []
    step_words = 12
    i = 0
    while i < len(words):
        start_t = words[i]["s"]
        j = i
        while j + 1 < len(words) and words[j + 1]["e"] - start_t < ideal_hi:
            j += 1
        end_t = words[j]["e"]
        if end_t - start_t < lo:  # too short: extend to the next words
            while j + 1 < len(words) and words[j + 1]["e"] - start_t < lo:
                j += 1
            end_t = words[j]["e"]
        if end_t - start_t >= lo:
            i2 = _review_opening(words, i, j)
            text = " ".join(w["w"] for w in words[i2:j + 1]).strip()
            first_text = " ".join(w["w"] for w in words[i2:min(i2 + 8, j + 1)])
            e = 0.0
            if energy:
                seg = energy[int(words[i2]["s"]):int(words[j]["e"]) + 1]
                e = sum(seg) / len(seg) if seg else 0.0
            v = _score_window_text(text, first_text, e, j - i2 + 1,
                                   end_t - words[i2]["s"])
            s, e2 = cs.snap_clip_to_words(words[i2]["s"], end_t, words,
                                          duration, lo, hi)
            candidates.append({
                "start": s, "end": e2, "predicted_score": v.total,
                "virality": v.as_dict(), "hook_title": first_text[:80],
                "text": text,
            })
        i += step_words
    candidates.sort(key=lambda c: -c["predicted_score"])
    kept = cs.dedupe_overlapping(candidates)
    kept.sort(key=lambda c: c["start"])
    return kept[:n_clips]


_LLM_PROMPT = """You are picking viral short clips from a transcript.
Return ONLY a JSON array, no other text. Each item:
{{"start": <seconds>, "end": <seconds>, "hook": 0-25, "engagement": 0-25,
  "value": 0-25, "shareability": 0-25, "hook_type": "question|statement|statistic|story|contrast|none",
  "hook_title": "<short punchy headline>", "reasoning": "<one sentence>"}}
Rules: clips 25-50s, start on a complete sentence (never on filler like
"um/so/but"), one complete setup -> payoff, no overlap between clips.
Transcript windows (start-end seconds + text):
{windows}
"""


def _llm_picks_from_json(raw: str, words: list[dict], duration: float,
                         n_clips: int, lo: float, hi: float) -> list[dict]:
    data = json.loads(raw[raw.find("["):raw.rfind("]") + 1])
    out = []
    for item in data:
        s, e = cs.snap_clip_to_words(float(item["start"]), float(item["end"]),
                                     words, duration, lo, hi)
        v = Virality(
            hook=_clamp25(item.get("hook", 0)),
            engagement=_clamp25(item.get("engagement", 0)),
            value=_clamp25(item.get("value", 0)),
            shareability=_clamp25(item.get("shareability", 0)),
            hook_type=str(item.get("hook_type", "none")),
            reasoning=str(item.get("reasoning", ""))[:200],
        )
        text = " ".join(w["w"] for w in words
                         if s <= w["s"] < e).strip()
        out.append({"start": s, "end": e, "predicted_score": v.total,
                    "virality": v.as_dict(),
                    "hook_title": str(item.get("hook_title", ""))[:80],
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
    preset = supo_duration(normalize_clip_length(length))
    windows = cs.build_transcript_windows(transcript, duration)
    win_text = "\n".join(
        f"[{w['start']:.1f}-{w['end']:.1f}] {w['text'][:600]}" for w in windows)
    client = genai.Client(api_key=api_key)
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
    resp = client.models.generate_content(
        model=model, contents=_LLM_PROMPT.format(windows=win_text))
    return _llm_picks_from_json(resp.text, transcript.get("words") or [],
                                duration, n_clips, preset.minimum,
                                preset.maximum)


def ollama_picks(transcript: dict, duration: float, n_clips: int = 3,
                 length: str = "auto") -> list[dict]:
    """Local LLM moment picking via an OpenAI-compatible endpoint
    (Ollama / LM Studio / vLLM). Free, no key, runs on your own machine
    — the OpenShorts local-LLM option, same prompt as Gemini."""
    preset = supo_duration(normalize_clip_length(length))
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
                                duration, n_clips, preset.minimum,
                                preset.maximum)


def pick_moments(transcript: dict, duration: float, n_clips: int = 3,
                 length: str = "auto", llm: str = "auto",
                 energy: list[float] | None = None) -> tuple[list[dict], str]:
    """Return (clips, method_used). method is gemini / ollama / heuristic."""
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
