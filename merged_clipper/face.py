"""Face tracking for the 9:16 crop (v2, pro style).

Research (report §3/§7): paid tools sample face position and pan a
smoothed virtual camera. Free replication: sample frames every 0.5 s,
smooth with an EMA + deadband so the crop pans instead of jumping.

Detectors, tried in order (first one that works wins):
  1. MediaPipe BlazeFace (opensource-clipping's approach) — needs the
     mediapipe package + its bundled model.
  2. OpenCV YuNet (``cv2.FaceDetectorYN``) — DNN face detector,
     model ``merged_clipper/data/face_detection_yunet_2023mar.onnx``
     (OpenCV model zoo, Apache-2.0). YuNet is the report's
     recommended OpenCV option and beats Haar on profile faces.
  3. OpenCV Haar cascade (``merged_clipper/data/`` XML) — only on
     OpenCV 4, where ``CascadeClassifier`` still exists; OpenCV 5
     removed it, so this path is legacy-only.

Returns a list of horizontal face centres (0..1) sampled every 0.5 s
— index ``i`` covers time ``i * 0.5`` s. If no detector is available
(or no face is ever found) returns None and the renderer falls back
to a centred crop over a blurred background. The pipeline never fails
because of this module.

Smoothing: EMA with alpha 0.35 over the 0.5 s samples, then a
deadband of 0.02 — moves smaller than 2% of the frame width are
ignored, which kills the jitter Haar produces on a static speaker.
``quantize_centres`` reduces the smoothed track to one centre per
2 s (median) for the renderer's crop expression, so the ffmpeg
expression stays short.
"""

from __future__ import annotations

import os
import statistics

SAMPLE_STEP = 0.5
EMA_ALPHA = 0.35
DEADBAND = 0.02


def _ema_smooth(values: list[float], alpha: float = EMA_ALPHA,
                deadband: float = DEADBAND) -> list[float]:
    out: list[float] = []
    prev: float | None = None
    for v in values:
        if prev is None:
            prev = v
        else:
            s = alpha * v + (1 - alpha) * prev
            # Deadband: ignore small moves — they are detector jitter.
            if abs(s - prev) < deadband:
                s = prev
            prev = s
        out.append(prev)
    return out


def quantize_centres(centres: list[float] | None, step: float = 2.0) -> list[float]:
    """One centre per ``step`` seconds (median of the 0.5 s samples)."""
    if not centres:
        return []
    per = max(1, int(round(step / SAMPLE_STEP)))
    out = []
    for i in range(0, len(centres), per):
        out.append(statistics.median(centres[i:i + per]))
    return out


def _data_path(name: str) -> str | None:
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "data", name)
    return p if os.path.exists(p) else None


def _haar_cascade_path() -> str | None:
    p = _data_path("haarcascade_frontalface_default.xml")
    if p:
        return p
    try:
        import cv2  # type: ignore
        q = os.path.join(cv2.data.haarcascades,
                         "haarcascade_frontalface_default.xml")
        return q if os.path.exists(q) else None
    except Exception:
        return None


def _yunet_centres(video_path: str, duration: float) -> list[float] | None:
    """OpenCV YuNet face detection (OpenCV 5's replacement for Haar)."""
    try:
        import cv2  # type: ignore
    except ImportError:
        return None
    model = _data_path("face_detection_yunet_2023mar.onnx")
    if not model or not hasattr(cv2, "FaceDetectorYN_create"):
        return None
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    raw: list[float] = []
    n = int(duration / SAMPLE_STEP) + 1
    detector = None
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i * SAMPLE_STEP * fps))
        ok, frame = cap.read()
        if not ok:
            raw.append(raw[-1] if raw else 0.5)
            continue
        small = cv2.resize(frame, (640, 360))
        if detector is None:
            try:
                detector = cv2.FaceDetectorYN_create(
                    model, "", (640, 360), score_threshold=0.7,
                    nms_threshold=0.3, top_k=5)
            except Exception:
                cap.release()
                return None
        _ret, faces = detector.detect(small)
        if faces is not None and len(faces):
            # Largest face = the speaker. faces rows: x, y, w, h, ...
            x, _y, w, _h = max(faces, key=lambda b: b[2] * b[3])[:4]
            raw.append(min(1.0, max(0.0, (x + w / 2) / small.shape[1])))
        else:
            raw.append(raw[-1] if raw else 0.5)
    cap.release()
    if not any(c != 0.5 for c in raw):
        return None  # never saw a face — caller uses the fallback
    return _ema_smooth(raw)


def _haar_centres(video_path: str, duration: float) -> list[float] | None:
    """Legacy OpenCV 4 Haar path (CascadeClassifier was removed in
    OpenCV 5 — this only runs where the attribute still exists)."""
    try:
        import cv2  # type: ignore
    except ImportError:
        return None
    if not hasattr(cv2, "CascadeClassifier"):
        return None
    path = _haar_cascade_path()
    if not path:
        return None
    clf = cv2.CascadeClassifier(path)
    if clf.empty():
        return None
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    raw: list[float] = []
    n = int(duration / SAMPLE_STEP) + 1
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i * SAMPLE_STEP * fps))
        ok, frame = cap.read()
        if not ok:
            raw.append(raw[-1] if raw else 0.5)
            continue
        small = cv2.resize(frame, (640, 360))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        faces = clf.detectMultiScale(gray, scaleFactor=1.1,
                                     minNeighbors=5, minSize=(60, 60))
        if len(faces):
            # Largest face = the speaker.
            x, _y, w, _h = max(faces, key=lambda b: b[2] * b[3])
            raw.append(min(1.0, max(0.0, (x + w / 2) / small.shape[1])))
        else:
            raw.append(raw[-1] if raw else 0.5)
    cap.release()
    if not any(c != 0.5 for c in raw):
        return None  # never saw a face — caller uses the fallback
    return _ema_smooth(raw)


def _mediapipe_centres(video_path: str, duration: float) -> list[float] | None:
    try:
        import cv2  # type: ignore
        import mediapipe as mp  # type: ignore
        from mediapipe.tasks import python as mp_python  # type: ignore
        from mediapipe.tasks.python import vision as mp_vision  # type: ignore
    except Exception:
        return None
    pkg = os.path.dirname(mp.__file__)
    model = None
    for root, _dirs, files in os.walk(pkg):
        if "blaze_face_short_range.tflite" in files:
            model = os.path.join(root, "blaze_face_short_range.tflite")
            break
    if not model:
        return None
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    try:
        base = mp_python.BaseOptions(model_asset_path=model)
        options = mp_vision.FaceDetectorOptions(
            base_options=base, min_detection_confidence=0.5)
        detector = mp_vision.FaceDetector.create_from_options(options)
    except Exception:
        cap.release()
        return None
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    raw: list[float] = []
    n = int(duration / SAMPLE_STEP) + 1
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i * SAMPLE_STEP * fps))
        ok, frame = cap.read()
        if not ok:
            raw.append(raw[-1] if raw else 0.5)
            continue
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = detector.detect(image)
        if result.detections:
            box = result.detections[0].bounding_box
            raw.append(min(1.0, max(0.0,
                                     (box.origin_x + box.width / 2)
                                     / frame.shape[1])))
        else:
            raw.append(raw[-1] if raw else 0.5)
    cap.release()
    if not any(c != 0.5 for c in raw):
        return None
    return _ema_smooth(raw)


def face_centres(video_path: str, duration: float) -> list[float] | None:
    """Smoothed face centres sampled every 0.5 s, or None.

    MediaPipe first, OpenCV YuNet second, Haar third. ``face_centres``
    keeps the v1 name/signature (list per sample, or None) so old
    callers keep working — only the sampling rate changed (1 s -> 0.5 s).
    """
    centres = _mediapipe_centres(video_path, duration)
    if centres is not None:
        return centres
    centres = _yunet_centres(video_path, duration)
    if centres is not None:
        return centres
    return _haar_centres(video_path, duration)


def face_detector_name() -> str:
    """Which detector would run here (for logging / TEST_REPORT)."""
    try:
        import mediapipe  # type: ignore  # noqa: F401
        return "mediapipe-blazeface"
    except Exception:
        pass
    try:
        import cv2  # type: ignore  # noqa: F401
        if _data_path("face_detection_yunet_2023mar.onnx"):
            return "opencv-yunet"
        if _haar_cascade_path():
            return "opencv-haar"
    except Exception:
        pass
    return "none"
