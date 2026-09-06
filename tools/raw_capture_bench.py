#!/usr/bin/env python3
"""Benchmark in-loop DNG capture vs deferred raw-buffer dump.

Runs two timed loops with identical camera settings (full-res lossless raw):
  A) capture_request + save_dng per frame (what ALT-Scann8 does today)
  B) capture_request + dump raw buffer + metadata JSON per frame
Frames are written under the output directory (default ~/raw-bench).
The raw dumps can be converted to DNG afterwards with raw_to_dng.py.
"""
import argparse
import json
import os
import time

from picamera2 import Picamera2
from libcamera import Transform

parser = argparse.ArgumentParser()
parser.add_argument("--outdir", default=os.path.expanduser("~/raw-bench"))
parser.add_argument("--frames", type=int, default=15)
parser.add_argument("--exposure", type=int, default=8000, help="us")
args = parser.parse_args()

os.makedirs(f"{args.outdir}/dng", exist_ok=True)
os.makedirs(f"{args.outdir}/raw", exist_ok=True)

cam = Picamera2()
cfg = cam.create_still_configuration(main={"size": (2028, 1520)},
                                     raw={"size": (4056, 3040), "format": "SRGGB12"},
                                     transform=Transform(hflip=True))
cam.configure(cfg)
cam.set_controls({"AeEnable": False, "AwbEnable": False,
                  "ExposureTime": args.exposure, "AnalogueGain": 1.0})
cam.start()
time.sleep(1.5)

raw_cfg = cam.camera_configuration()["raw"]
print(f"raw stream: {raw_cfg['format']} {raw_cfg['size']} framesize {raw_cfg['framesize']}")
with open(f"{args.outdir}/raw/config.json", "w") as f:
    json.dump({"format": str(raw_cfg["format"]), "size": raw_cfg["size"],
               "stride": raw_cfg["stride"], "framesize": raw_cfg["framesize"]}, f)

# A: in-loop DNG (current ALT-Scann8 behavior)
t0 = time.time()
for i in range(args.frames):
    r = cam.capture_request()
    r.save_dng(f"{args.outdir}/dng/frame-{i:05d}.dng")
    r.release()
dt_a = time.time() - t0

# B: raw buffer + metadata dump (deferred encode)
t0 = time.time()
for i in range(args.frames):
    r = cam.capture_request()
    buf = r.make_buffer("raw")
    meta = r.get_metadata()
    r.release()
    with open(f"{args.outdir}/raw/frame-{i:05d}.raw", "wb") as f:
        f.write(buf)
    with open(f"{args.outdir}/raw/frame-{i:05d}.json", "w") as f:
        json.dump({k: v for k, v in meta.items() if isinstance(v, (int, float, str, list, tuple))}, f)
dt_b = time.time() - t0

cam.stop()
cam.close()

n = args.frames
print(f"A in-loop DNG:  {dt_a:.2f}s for {n} frames = {dt_a/n*1000:.0f} ms/frame = {n/dt_a:.2f} fps")
print(f"B raw dump:     {dt_b:.2f}s for {n} frames = {dt_b/n*1000:.0f} ms/frame = {n/dt_b:.2f} fps")
