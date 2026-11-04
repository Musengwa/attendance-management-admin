"""Run once: downloads the YuNet (detection) and SFace (recognition) models from OpenCV Zoo."""
import urllib.request
from pathlib import Path

BASE = "https://github.com/opencv/opencv_zoo/raw/main/models"
FILES = {
    "face_detection_yunet_2023mar.onnx": f"{BASE}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx": f"{BASE}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}
out = Path(__file__).parent / "models"
out.mkdir(exist_ok=True)
for name, url in FILES.items():
    dest = out / name
    if dest.exists():
        print("already have", name)
        continue
    print("downloading", name)
    urllib.request.urlretrieve(url, dest)
print("done")
