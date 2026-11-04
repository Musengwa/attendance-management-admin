"""Turns webcam frames (from the browser) into SFace embeddings. No images are kept."""
import base64
import threading

from django.conf import settings

MIN_SAMPLES = 5
_lock = threading.Lock()
_models = {}


class FaceError(Exception):
    pass


def _load():
    import cv2
    if not _models:
        d = settings.FACE_MODEL_DIR
        det, rec = d / "face_detection_yunet_2023mar.onnx", d / "face_recognition_sface_2021dec.onnx"
        if not det.exists() or not rec.exists():
            raise FaceError("Face models not found. Run `python get_models.py` in the recognition folder.")
        _models["det"] = cv2.FaceDetectorYN.create(str(det), "", (320, 320), score_threshold=0.9)
        _models["rec"] = cv2.FaceRecognizerSF.create(str(rec), "")
    return cv2, _models["det"], _models["rec"]


def embeddings_from_data_urls(frames):
    import numpy as np
    out = []
    with _lock:
        cv2, det, rec = _load()
        for url in frames:
            try:
                raw = base64.b64decode(url.split(",", 1)[-1])
            except Exception:
                continue
            img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            h, w = img.shape[:2]
            det.setInputSize((w, h))
            _, faces = det.detect(img)
            if faces is None or len(faces) != 1:
                continue  # skip frames with no face or more than one person
            out.append(rec.feature(rec.alignCrop(img, faces[0]))[0].tolist())
    if len(out) < MIN_SAMPLES:
        raise FaceError(f"Only {len(out)} usable frames. Face the camera alone, in good light, and try again.")
    return out
