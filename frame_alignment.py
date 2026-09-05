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
    centers.sort()
    agreement = height * 0.015
    if len(centers) >= 2 and centers[-1] - centers[0] > agreement:
        # A damaged edge can produce one wrong center. Require a unique pair
        # of agreeing strips; overlapping pairs with different centers remain
        # ambiguous rather than choosing whichever pair happens to come first.
        pairs = [(a, b) for a, b in zip(centers, centers[1:]) if b - a <= agreement]
        centers = list(pairs[0]) if len(pairs) == 1 else []
    if len(centers) < 2:
        return HoleMeasurement(None, height, 'No unambiguous complete sprocket detected')
    return HoleMeasurement(float(np.median(centers)) - height / 2 - target_shift, height)


class AlignmentGuard:
    """Bounded forward correction, confirmed by another stationary exposure."""
    def __init__(self, tolerance_percent=3, correct=False, steps_per_frame=0):
        self.tolerance_percent = tolerance_percent
        self.correct = correct
        self.steps_per_frame = steps_per_frame
        self.confirming = False
        self.confirm_offset = None
        self.attempts = 0
        self.total_steps = 0
        self.next_steps = 0
        self.last_offset = None
        self.reason = ''

    def inspect(self, measurement):
        offset, height = measurement.offset, measurement.height
        # A nudge must not make an otherwise acceptable frame fail the guard.
        tolerance = height * self.tolerance_percent / 100
        if offset is not None and abs(offset) <= tolerance:
            return 'accept'
        if not self.confirming:
            self.confirming = True
            self.confirm_offset = offset
            return 'confirm'
        if offset is None:
            self.reason = measurement.reason or 'Sprocket position is uncertain'
            return 'pause'
        if self.attempts == 0 and (self.confirm_offset is None or abs(offset - self.confirm_offset) > height * 0.015):
            self.reason = 'Stationary exposures disagree on sprocket position'
            return 'pause'
        if not self.correct:
            return 'pause'
        if offset < 0:
            self.reason = 'Frame has passed the target; automatic reversal is disabled'
            return 'pause'
        if self.last_offset is not None and offset >= self.last_offset - height * 0.002:
            self.reason = 'Corrective movement did not improve alignment'
            return 'pause'
        remaining = int(self.steps_per_frame / 3) - self.total_steps
        if self.attempts >= 4 or remaining <= 0 or self.steps_per_frame <= 0:
            self.reason = 'Correction limit reached'
            return 'pause'
        # Sensor height may include more than one film pitch. Under-correct and
        # re-measure rather than assuming an exact pixels-per-step calibration.
        self.next_steps = min(40, remaining, max(1, int(offset / height * self.steps_per_frame * 0.75)))
        self.last_offset = offset
        self.attempts += 1
        self.total_steps += self.next_steps
        return 'nudge'
