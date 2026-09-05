# Sprocket corner model

YOLO11n, three classes: top-right sprocket corner, bottom-right sprocket
corner, right frame seam. The scanner requires both sprocket corners;
frame seams and isolated corners do not authorize capture or movement.

Source: the user's AfterScan-X repository,
`Resources/yolo_sprocket_detector_3class.pt`.
Source SHA-256: `2843234a417037f178b9cae66aed342b2a68c4fa881a0a59d7753d50d0fb2a1d`.
See `metadata.yaml` for model provenance and the included `LICENSE` for
the Ultralytics AGPL-3.0 license.

Exported with Ultralytics 8.4.141, PyTorch 2.14.0+cpu, NCNN 1.0.20260526,
PNNX 20260526, Python 3.12:

```python
from ultralytics import YOLO
YOLO('sprocket.pt').export(format='ncnn', imgsz=640, device='cpu')
```

Only `model.ncnn.param`, `model.ncnn.bin`, and metadata are deployed;
the scanner does not need PyTorch, Ultralytics, or PNNX. Install the NCNN
wheel into its existing virtual environment without upgrading camera dependencies:

```sh
.venv/bin/python -m pip install --no-deps ncnn==1.0.20260526
```

The runtime uses RGB, a 640×640 letterbox with value 114, `[0,1]` float32
CHW input `in0`, and output `out0` of shape `[7,8400]`. It uses two CPU
threads, class-wise NMS at IoU 0.45, and corner confidence ≥0.65. No GPU,
Hailo runtime, network request, or automatic download is required.

The training resolution was 1024. Both 640 and 1024 exports were checked
against eight saved frames from this reel: the conservative two-corner
rule accepted one at 640 and none at 1024. This small sample is not an
accuracy benchmark; many damaged holes still require manual acceptance.
