"""Merged free clipper: OpenShorts (MIT) + opensource-clipping (MIT)
+ SupoClip (AGPL-3.0) — merged work is AGPL-3.0 overall (see LICENSE).

A working unified CLI pipeline: download -> transcribe (faster-whisper)
-> pick moments (Gemini if a key is set, else a free heuristic scorer
with a Hook/Flow/Value/Trend rubric — a ranking aid, not a prediction)
-> render 9:16 clips with Hormozi karaoke captions (Montserrat Black),
hook overlay, punch zooms, silence cuts and face-tracked crop.
No watermark, no monthly cap, no paid API required for the core path.
v2.0: pro style is the default; ``--style classic`` restores v1.
"""

__version__ = "0.2.0"
