"""
Register a member's face.

    python register.py 202400004

1. Checks the member exists in the dashboard (add them there first).
2. Opens the webcam and captures SAMPLES face embeddings (no photos are stored).
3. Uploads them to the dashboard (the database is the single source of truth).

Fallback for when the browser scan on the Members page isn't usable.

Keys:  SPACE = capture a sample (or let AUTO capture every 0.6s)   Q = quit
"""
import os
import sys
import time
from pathlib import Path

import cv2
import requests

HERE = Path(__file__).parent
DETECTOR = HERE / "models" / "face_detection_yunet_2023mar.onnx"
RECOGNIZER = HERE / "models" / "face_recognition_sface_2021dec.onnx"

DASHBOARD = os.environ.get("DASHBOARD_URL", "http://127.0.0.1:8000")
TOKEN = os.environ.get("ATTENDANCE_API_TOKEN", "dev-token-change-me")
HEADERS = {"Authorization": f"Token {TOKEN}"}
SAMPLES = 15
AUTO = True
PROMPTS = ["Look straight at the camera", "Turn slightly left", "Turn slightly right",
           "Tilt up a little", "Tilt down a little"]


def main(member_id):
    # 1. member must exist
    r = requests.get(f"{DASHBOARD}/api/members/{member_id}/face/", headers=HEADERS, timeout=5)
    if r.status_code == 404:
        sys.exit(f"Member {member_id} not found. Register them in the dashboard first.")
    r.raise_for_status()
    name = r.json()["full_name"]
    print(f"Registering face for {name} ({member_id})")

    if not DETECTOR.exists() or not RECOGNIZER.exists():
        sys.exit("Models missing. Run: python get_models.py")

    detector = cv2.FaceDetectorYN.create(str(DETECTOR), "", (320, 320), score_threshold=0.9)
    recognizer = cv2.FaceRecognizerSF.create(str(RECOGNIZER), "")
    cam = cv2.VideoCapture(0)
    if not cam.isOpened():
        sys.exit("Could not open webcam.")

    samples, last = [], 0.0
    while len(samples) < SAMPLES:
        ok, frame = cam.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        detector.setInputSize((w, h))
        _, faces = detector.detect(frame)
        faces = [] if faces is None else faces

        prompt = PROMPTS[min(len(samples) * len(PROMPTS) // SAMPLES, len(PROMPTS) - 1)]
        msg = prompt
        if len(faces) == 0:
            msg = "No face found"
        elif len(faces) > 1:
            msg = "Only one person in frame please"
        else:
            x, y, bw, bh = faces[0][:4].astype(int)
            cv2.rectangle(frame, (x, y), (x + bw, y + bh), (0, 255, 0), 2)

        key = cv2.waitKey(1) & 0xFF
        want = key == 32 or (AUTO and time.time() - last > 0.6)
        if len(faces) == 1 and want:
            aligned = recognizer.alignCrop(frame, faces[0])
            samples.append(recognizer.feature(aligned)[0])
            last = time.time()

        cv2.putText(frame, f"{msg}  [{len(samples)}/{SAMPLES}]", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.imshow("Register face (Q to quit)", frame)
        if key == ord("q"):
            break

    cam.release()
    cv2.destroyAllWindows()

    if len(samples) < SAMPLES:
        sys.exit("Cancelled - nothing saved.")

    r = requests.post(f"{DASHBOARD}/api/members/{member_id}/face/", headers=HEADERS, timeout=10,
                      json={"embeddings": [s.tolist() for s in samples]})
    r.raise_for_status()
    print(f"Uploaded {len(samples)} samples. Dashboard updated.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python register.py <member_id>")
    main(sys.argv[1])
