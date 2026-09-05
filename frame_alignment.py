"""Camera-only alignment checks; no transport or GUI dependencies."""
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class HoleMeasurement:
    offset: float | None
    height: int
    reason: str = ''


def measure_hole(image, film_type, target_shift=0):
    """Require two independent strips to agree on a complete sprocket/gap.

    target_shift is in pixels of this image. Unknown measurements never mean
    centered. S8 follows the bright hole, R8 the dark gap between holes.
    """
    height, width = image.shape[:2]
    centers = []
    for fraction in (0.03, 0.04, 0.05):
        x = int(width * fraction)
        strip = image[:, x:x + max(2, round(width * 0.0025))]
        if strip.size == 0:
            continue
        gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
        if int(gray.max()) - int(gray.min()) < 20:
            continue
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        profile = np.mean(binary > 0, axis=1)
        mask = profile >= 0.5 if film_type == 'S8' else profile < 0.5
        edges = np.diff(np.r_[False, mask, False].astype(np.int8))
        areas = [(start, end) for start, end in zip(np.where(edges == 1)[0], np.where(edges == -1)[0])
                 if start > 0 and end < height and 0.08 * height < end - start < 0.8 * height]
        if len(areas) != 1:
            continue
        start, end = areas[0]
        centers.append((start + end - 1) / 2)
    if len(centers) < 2 or max(centers) - min(centers) > height * 0.015:
        return HoleMeasurement(None, height, 'No unambiguous complete sprocket detected')
    return HoleMeasurement(float(np.median(centers)) - height / 2 - target_shift, height)


class AlignmentGuard:
    """Confirm suspect measurements on the same film frame before pausing."""
    def __init__(self, tolerance_percent=3):
        self.tolerance_percent = tolerance_percent
        self.confirming = False

    def inspect(self, measurement):
        if measurement.offset is not None and abs(measurement.offset) <= measurement.height * self.tolerance_percent / 100:
            return 'accept'
        if not self.confirming:
            self.confirming = True
            return 'confirm'
        return 'pause'
