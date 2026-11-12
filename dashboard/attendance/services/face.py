"""Registration: browser frames -> embedding service /v1/embed/batch -> embeddings. No images are kept."""
from collections import Counter

import httpx
from django.conf import settings

MAX_FRAMES = 30  # the embedding service's MAX_BATCH_IMAGES

REASONS = {"no_face": "no face", "multiple_faces": "more than one face", "face_too_small": "too far from camera",
           "blurry": "blurry", "low_confidence": "face unclear", "invalid_image": "unreadable image"}


class FaceError(Exception):
    pass


def embeddings_from_data_urls(frames):
    """Returns (model, embeddings). Raises FaceError with a message the admin can act on."""
    if not frames:
        raise FaceError("No frames received.")
    try:
        r = httpx.post(f"{settings.EMBED_URL.rstrip('/')}/v1/embed/batch",
                       json={"images_b64": frames[:MAX_FRAMES], "min_usable": settings.FACE_MIN_SAMPLES},
                       headers={"Authorization": f"Token {settings.EMBED_API_TOKEN}"}, timeout=60)
    except httpx.HTTPError as e:
        raise FaceError("The face service is not reachable. Is the embedding service running?") from e

    if r.status_code == 422:  # not enough usable frames; same body shape as 200
        body = r.json()
        counts = Counter(x["reason"] for x in body.get("rejected", []))
        why = ", ".join(f"{n} {REASONS.get(k, k)}" for k, n in counts.items())
        raise FaceError(f"Only {body['usable']} of {body['total']} frames were usable, "
                        f"at least {body.get('min_usable', settings.FACE_MIN_SAMPLES)} are needed"
                        f"{f' ({why})' if why else ''}. "
                        "Face the camera alone, in good light, a little closer, and try again.")
    if r.status_code == 401:
        raise FaceError("The face service rejected Django's EMBED_API_TOKEN.")
    if r.status_code == 413:
        raise FaceError("The scan was too large for the face service.")
    if r.status_code != 200:
        raise FaceError(f"Face service error (HTTP {r.status_code}).")

    body = r.json()
    if body["model"] != settings.FACE_MODEL:
        raise FaceError(f"Face service runs model {body['model']!r}, expected {settings.FACE_MODEL!r}.")
    return body["model"], body["embeddings"]
