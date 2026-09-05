#!/usr/bin/env python3
"""Benchmark offline saved frames without opening the camera or transport."""
import argparse
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np
from PIL import Image

from frame_alignment import measure_hole
from sprocket_yolo import SprocketYolo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images', type=Path, nargs='+')
    parser.add_argument('--film-type', choices=['S8', 'R8'], default='S8')
    parser.add_argument('--threads', type=int, choices=[1, 2, 3, 4], default=2)
    args = parser.parse_args()
    detector = SprocketYolo(threads=args.threads)
    elapsed = []
    for path in args.images:
        if path.suffix.lower() == '.dng':
            import rawpy
            with rawpy.imread(str(path)) as raw:
                rgb = raw.postprocess(use_camera_wb=True, half_size=True)
        else:
            with Image.open(path) as image:
                rgb = np.array(image.convert('RGB'))
        t = time.monotonic()
        classical = measure_hole(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), args.film_type)
        classical_ms = 1000 * (time.monotonic() - t)
        t = time.monotonic()
        neural = detector.measure(rgb, args.film_type)
        elapsed.append(1000 * (time.monotonic() - t))
        print(f'{path.name}: classical {classical_ms:.1f} ms {classical}; '
              f'YOLO {elapsed[-1]:.1f} ms {neural}', flush=True)
    if len(elapsed) > 1:
        print(f'Warm YOLO median {statistics.median(elapsed[1:]):.1f} ms; '
              f'range {min(elapsed[1:]):.1f}–{max(elapsed[1:]):.1f} ms')


if __name__ == '__main__':
    main()
