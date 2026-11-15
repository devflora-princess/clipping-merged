"""Merged free clipper: OpenShorts (MIT) + opensource-clipping (MIT)
+ SupoClip (AGPL-3.0) — merged work is AGPL-3.0 overall (see LICENSE).

A working unified CLI pipeline: download -> transcribe (faster-whisper)
-> pick moments (Gemini if a key is set, else a free heuristic scorer
with SupoClip's hook/engagement/value/shareability breakdown)
-> render 9:16 clips with karaoke captions (SupoClip templates),
a 3-second hook teaser and an auto-thumbnail. No watermark, no
monthly cap, no paid API required for the core path.
"""

__version__ = "0.1.0"
