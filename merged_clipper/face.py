"""Optional face/speaker tracking for the 9:16 crop.

If OpenCV + MediaPipe are installed (they are in both parent
projects' requirements, but are heavy — torch-sized wheels on some
platforms), sample the video once per second, find the largest face,
and return a smoothed horizontal centre (0..1) per second so the
crop can pan with the speaker, like opensource-clipping's
MediaPipe BlazeFace tracking.

If either library is missing, returns None and the renderer falls
back to a centred crop over a blurred background (the OpenShorts
GENERAL layout). The pipeline never fails because of this module.
"""

from __future__ import annotations


def face_centres(video_path: str, duration: float) -> list[float] | None:
    try:
        import cv2  # type: ignore
        import mediapipe as mp  # type: ignore
    except ImportError:
        return None
    try:
        from mediapipe.tasks import python as mp_python  # type: ignore
        from mediapipe.tasks.python import vision as mp_vision  # type: ignore
    except Exception:
        return None

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    detector = None
    try:
        base = mp_python.BaseOptions(
            model_asset_path=_find_blazeface_model())
        options = mp_vision.FaceDetectorOptions(
            base_options=base, min_detection_confidence=0.5)
        detector = mp_vision.FaceDetector.create_from_options(options)
    except Exception:
        cap.release()
        return None

    centres: list[float] = []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    for sec in range(int(duration) + 1):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(sec * fps))
        ok, frame = cap.read()
        if not ok:
            centres.append(0.5)
            continue
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = detector.detect(image)
        if result.detections:
            box = result.detections[0].bounding_box
            centres.append(min(1.0, max(0.0,
                                         (box.origin_x + box.width / 2)
                                         / frame.shape[1])))
        else:
            centres.append(centres[-1] if centres else 0.5)
    cap.release()
    # Smooth: 3-point moving average, so the crop pans instead of jumping.
    smoothed = []
    for i, c in enumerate(centres):
        window = centres[max(0, i - 1):i + 2]
        smoothed.append(sum(window) / len(window))
    return smoothed


def _find_blazeface_model() -> str:
    """MediaPipe ships the BlazeFace model inside the pip package."""
    import os

    import mediapipe as mp  # type: ignore

    pkg = os.path.dirname(mp.__file__)
    for root, _dirs, files in os.walk(pkg):
        if "blaze_face_short_range.tflite" in files:
            return os.path.join(root, "blaze_face_short_range.tflite")
    raise FileNotFoundError("BlazeFace model not found in mediapipe package")
