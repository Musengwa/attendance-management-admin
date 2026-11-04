"""Matching helper for the camera script: compare a live face to every registered embedding."""
import os

import numpy as np
import requests

DASHBOARD = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8000")
TOKEN = os.environ.get("ATTENDANCE_API_TOKEN", "dev-token-change-me")
COSINE_THRESHOLD = 0.363  # SFace's recommended cosine threshold; higher score = more similar


def load_registered():
    """Download all registered faces from the dashboard. Banned members are included on purpose,
    so the system can recognise them and raise an alert."""
    r = requests.get(f"{DASHBOARD}/api/embeddings/", headers={"Authorization": f"Token {TOKEN}"}, timeout=10)
    r.raise_for_status()
    return {mid: np.array(v, dtype=np.float32) for mid, v in r.json().items()}


def best_match(feature, registered):
    """Return (member_id, score), or (None, score) if the best score is below the threshold."""
    feature = np.asarray(feature, dtype=np.float32).ravel()
    feature = feature / np.linalg.norm(feature)
    best_id, best = None, -1.0
    for member_id, samples in registered.items():
        samples = samples / np.linalg.norm(samples, axis=1, keepdims=True)
        score = float(np.max(samples @ feature))
        if score > best:
            best_id, best = member_id, score
    return (best_id, best) if best >= COSINE_THRESHOLD else (None, best)
