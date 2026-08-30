#!/usr/bin/env python3
"""Convert raw buffer dumps from raw_capture_bench.py into DNG files.

Uses picamera2's own DNG writer (Helpers.save_dng), so the output matches what
an in-loop save_dng would have produced from the same buffer and metadata.
Runs offline: the camera device is opened for its calibration data but no
capture is performed.
"""
import argparse
import glob
import json
import os
import time

import numpy as np
from picamera2 import Picamera2

parser = argparse.ArgumentParser()
parser.add_argument("rawdir", help="directory with frame-*.raw + frame-*.json + config.json")
parser.add_argument("--outdir", default=None, help="default: <rawdir>/../dng-deferred")
args = parser.parse_args()

outdir = args.outdir or os.path.join(args.rawdir, "..", "dng-deferred")
os.makedirs(outdir, exist_ok=True)

with open(os.path.join(args.rawdir, "config.json")) as f:
    stored = json.load(f)

cam = Picamera2()
cfg = cam.create_still_configuration(main={"size": (2028, 1520)},
                                     raw={"size": tuple(stored["size"]), "format": "SRGGB12"})
cam.configure(cfg)  # not started; supplies stream config + calibration to the DNG writer
raw_cfg = cam.camera_configuration()["raw"]
assert raw_cfg["framesize"] == stored["framesize"], "raw config mismatch with capture"

frames = sorted(glob.glob(os.path.join(args.rawdir, "frame-*.raw")))
t0 = time.time()
for path in frames:
    base = os.path.splitext(os.path.basename(path))[0]
    with open(path, "rb") as f:
        buf = np.frombuffer(f.read(), dtype=np.uint8)
    with open(os.path.join(args.rawdir, base + ".json")) as f:
        meta = json.load(f)
    cam.helpers.save_dng(buf, meta, raw_cfg, os.path.join(outdir, base + ".dng"))
dt = time.time() - t0
cam.close()

n = len(frames)
print(f"converted {n} frames in {dt:.2f}s = {dt/max(n,1)*1000:.0f} ms/frame")
