#!/usr/bin/env python3
"""
column_probe.py - diagnostic for the sprocket hole detection used by
Frame VCenter / auto fine tune / bad frames.

Runs the same detection logic as is_frame_centered() in ALT-Scann8.py on a
capture, for a range of strip column offsets, at full resolution and at
preview size. Prints the Otsu threshold, the row areas found and the
resulting offset for each, so silent failure modes are visible:
  - "NO AREAS" = detection returns offset -1 (looks like 'centered')
  - a single area spanning ~full height = degenerate, center ~= middle,
    offset ~= 0 (also looks like 'centered')

Usage:
  python3 column_probe.py <image> [preview_height]

<image> can be .dng (needs rawpy), or anything PIL opens (.jpg/.png/.ppm).
preview_height defaults to 900 (approximate UI preview canvas height).
"""

import sys
import numpy as np
import cv2
from PIL import Image


def load_image(path):
    if path.lower().endswith('.dng'):
        import rawpy
        with rawpy.imread(path) as raw:
            rgb = raw.postprocess()
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    return cv2.cvtColor(np.array(Image.open(path).convert('RGB')), cv2.COLOR_RGB2BGR)


def detect(img, film_type='S8', slice_start=0, slice_width=10):
    """Same logic as is_frame_centered(), with a movable slice start."""
    height = img.shape[0]
    sliced = img[:, slice_start:slice_start + slice_width]
    gray = cv2.cvtColor(sliced, cv2.COLOR_BGR2GRAY)
    otsu_t, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    middle = height // 2
    profile = np.sum(binary, axis=1)
    if film_type == 'S8':
        rows = np.where(profile > 0)[0]
    else:
        rows = np.where(profile == 0)[0]
    areas = []
    start = None
    previous = None
    min_gap_size = int(height * 0.08)
    for i in rows:
        if start is None:
            start = i
        if previous is not None and i - previous > 1:
            if previous - start > min_gap_size:
                areas.append((start, previous - 1))
            start = i
        previous = i
    if start is not None and len(rows) and rows[-1] - start > min_gap_size:
        areas.append((start, rows[-1]))

    result = 0
    bigger = 0
    for n, (a, b) in enumerate(areas):
        if n >= 2:
            break
        if b - a > bigger:
            bigger = b - a
            result = (a + b) // 2
    offset = (result - middle) if result != 0 else None
    return otsu_t, areas, offset


def probe(img, label, film_type):
    height = img.shape[0]
    print(f"\n--- {label}: {img.shape[1]}x{height}, min area gate {int(height*0.08)} px ---")
    for x0 in range(0, 121, 10):
        otsu_t, areas, offset = detect(img, film_type, slice_start=x0)
        if offset is None:
            print(f"cols {x0:3d}-{x0+9:3d}: otsu {otsu_t:5.1f} -> NO AREAS (returns offset -1, reads as centered)")
            continue
        desc = ", ".join(f"[{a}-{b} h={b-a} c={(a+b)//2}]" for a, b in areas[:3])
        full = " <-- FULL-HEIGHT (degenerate, reads as centered)" if any(
            (b - a) > height * 0.9 for a, b in areas[:2]) else ""
        print(f"cols {x0:3d}-{x0+9:3d}: otsu {otsu_t:5.1f} -> offset {offset:+5d}  areas {desc}{full}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    path = sys.argv[1]
    preview_h = int(sys.argv[2]) if len(sys.argv) > 2 else 900
    film_type = 'S8'
    img = load_image(path)
    probe(img, f"FULL RESOLUTION {path}", film_type)
    scale = preview_h / img.shape[0]
    preview = cv2.resize(img, (int(img.shape[1] * scale), preview_h))
    probe(preview, f"PREVIEW SIZE (h={preview_h}) - what the VCenter click analyzes", film_type)


if __name__ == '__main__':
    main()
