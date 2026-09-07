"""Camera-only alignment checks; no transport or GUI dependencies."""
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class HoleMeasurement:
    offset: float | None
    height: int
    reason: str = ''
    source: str = 'strips'


def measure_hole(image, film_type, target_shift=0, diagnostics=None):
    """Require two independent strips to agree on a complete sprocket/gap.

    target_shift is in pixels of this image. Unknown measurements never mean
    centered. S8 follows the bright hole, R8 the dark gap between holes.
    """
    height, width = image.shape[:2]
    centers = []
    if diagnostics is not None:
        diagnostics.update(target_shift=target_shift, strips=[])
    for fraction in (0.03, 0.04, 0.05):
        x = int(width * fraction)
        strip = image[:, x:x + max(2, round(width * 0.0025))]
        if strip.size == 0:
            continue
        gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
        if int(gray.max()) - int(gray.min()) < 20:
            continue
        threshold, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        profile = np.mean(binary > 0, axis=1)
        mask = profile >= 0.5 if film_type == 'S8' else profile < 0.5
        edges = np.diff(np.r_[False, mask, False].astype(np.int8))
        areas = [(start, end) for start, end in zip(np.where(edges == 1)[0], np.where(edges == -1)[0])
                 if start > 0 and end < height and 0.08 * height < end - start < 0.8 * height]
        if diagnostics is not None:
            diagnostics['strips'].append(dict(x=x, width=strip.shape[1], threshold=threshold,
                                              areas=[(int(a), int(b)) for a, b in areas]))
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
        return measure_adaptive_hole(image, film_type, target_shift, diagnostics)
    if diagnostics is not None:
        diagnostics['selected_centers'] = [float(c) for c in centers]
    return HoleMeasurement(float(np.median(centers)) - height / 2 - target_shift, height)


def measure_adaptive_hole(image, film_type, target_shift=0, diagnostics=None):
    """Find a clean interior band; require agreement on BOTH edges across it.

    Three non-overlapping strips spanning 1% of image width must support the
    same complete hole. Competing supported bands make the result unknown.
    Search stays inside the sprocket region, away from picture content.
    """
    height, width = image.shape[:2]
    samples = []
    if diagnostics is not None:
        diagnostics['adaptive_strips'] = []
    for fraction in np.arange(0.005, 0.061, 0.005):
        x = round(width * fraction)
        strip = image[:, x:x + max(1, round(width * 0.0025))]
        gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
        areas = []
        threshold = None
        if int(gray.max()) - int(gray.min()) >= 20:
            threshold, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            mask = np.mean(binary > 0, axis=1) >= 0.5
            if film_type == 'R8':
                mask = ~mask
            edges = np.diff(np.r_[False, mask, False].astype(np.int8))
            areas = [(a, b) for a, b in zip(np.where(edges == 1)[0], np.where(edges == -1)[0])
                     if 0 < a and b < height and 0.08 * height < b - a < 0.8 * height]
        if diagnostics is not None:
            diagnostics['adaptive_strips'].append(dict(x=x, width=strip.shape[1], threshold=threshold,
                                                       areas=[(int(a), int(b)) for a, b in areas]))
        samples.append(areas[0] if len(areas) == 1 else None)
    supported = []
    for i in range(len(samples) - 2):
        band = samples[i:i + 3]
        if any(v is None for v in band):
            continue
        if np.max(np.ptp(band, axis=0)) <= height * 0.015:
            supported.append(np.median(band, axis=0))
    if not supported or np.max(np.ptp(supported, axis=0)) > height * 0.015:
        return HoleMeasurement(None, height, 'No unambiguous complete sprocket detected', 'adaptive')
    start, end = np.median(supported, axis=0)
    if diagnostics is not None:
        diagnostics['adaptive_selected_edges'] = [float(start), float(end)]
    return HoleMeasurement(float((start + end - 1) / 2 - height / 2 - target_shift), height,
                           source='adaptive')


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
        self.confirm_source = None
        self.arrival_first = None
        self.arrival_recorded = False
        self.arrival_waiting = False
        self.diagnostic_first = None
        self.stabilization_first = None

    def inspect(self, measurement):
        offset, height = measurement.offset, measurement.height
        # A nudge must not make an otherwise acceptable frame fail the guard.
        tolerance = height * self.tolerance_percent / 100
        # A neural estimate must agree on two stationary exposures even when
        # it already appears centered. Never feed an unconfirmed guess to motion.
        if measurement.source == 'yolo' or self.confirm_source == 'yolo':
            if not self.confirming:
                self.confirming = True
                self.confirm_offset = offset
                self.confirm_source = measurement.source
                return 'confirm'
            if offset is None or self.confirm_offset is None or abs(offset - self.confirm_offset) > height * 0.015:
                self.reason = measurement.reason or 'Stationary exposures disagree on YOLO sprocket position'
                return 'pause'
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
        if measurement.source == 'yolo':
            self.confirming = False
            self.confirm_source = None
        return 'nudge'


class ForwardRecovery:
    """Seek exactly the next S8 hole after explicitly saving an overshoot.

    Every position needs two stationary exposures. A complete hole must move
    towards the top, leave there, and the next one must enter from the bottom.
    Unknown measurements authorize only bounded travel through that edge gap.
    No frame is accepted merely because travel is approximately one pitch.
    """
    def __init__(self, steps_per_frame, tolerance_percent=3):
        self.pitch = float(steps_per_frame)
        self.tolerance = min(3, tolerance_percent) / 100
        self.phase = 'old'
        self.total_steps = 0
        self.moves = 0
        self.next_steps = 0
        self.last_offset = None
        self.last_seen_steps = 0
        self.confirmation = None
        self.confirming = False
        self.reason = ''
        self.target_shift = None

    def inspect(self, measurement, target_shift=0):
        if measurement.height <= 0 or self.pitch <= 0:
            return self.fail('Invalid recovery geometry')
        shift = target_shift / measurement.height
        if self.target_shift is not None and abs(shift - self.target_shift) > 1e-6:
            return self.fail('Recovery target changed while moving')
        self.target_shift = shift
        offset = None if measurement.offset is None else measurement.offset / measurement.height
        if not self.confirming:
            self.confirmation = offset
            self.confirming = True
            return 'confirm'
        self.confirming = False
        if (offset is None) != (self.confirmation is None) or (
                offset is not None and abs(offset - self.confirmation) > 0.015):
            return self.fail('Stationary recovery exposures disagree')
        if self.last_offset is None:
            if offset is None or offset >= -self.tolerance:
                return self.fail('Recovery needs a clearly overshot, complete starting sprocket')
        elif offset is None:
            if self.phase != 'old' or self.last_offset + shift > -0.20:
                return self.fail('Lost the sprocket away from the expected top-edge transition')
            if self.total_steps - self.last_seen_steps >= self.pitch * 0.55:
                return self.fail('Next sprocket did not appear within the edge-transition limit')
        else:
            travel = self.total_steps - self.last_seen_steps
            difference = offset - self.last_offset
            if self.phase == 'old' and self.last_offset + shift <= -0.20 and offset + shift >= 0.15 and difference >= 0.40:
                if travel <= 0 or travel > self.pitch * 0.55:
                    return self.fail('Sprocket transition travel is inconsistent')
                self.phase = 'next'
            elif difference >= -0.00025 * travel:
                return self.fail(
                    f'Recovery progress too small: {-difference * measurement.height:.1f} px '
                    f'(minimum {0.00025 * travel * measurement.height:.1f} px)')
            elif -difference > 0.02 + 0.007 * travel:
                return self.fail('Sprocket position jumped unexpectedly')
        if offset is not None:
            self.last_offset = offset
            self.last_seen_steps = self.total_steps
            if self.phase == 'next':
                if abs(offset) <= self.tolerance:
                    return 'accept'
                if offset < -self.tolerance:
                    return self.fail('Passed the next frame; recovery will not seek another')
        remaining = int(self.pitch * 1.1) - self.total_steps
        if remaining <= 0 or self.moves >= 80:
            return self.fail('Forward recovery travel limit reached')
        self.next_steps = min(8, remaining)
        if self.phase == 'next' and offset is not None:
            self.next_steps = min(self.next_steps,
                                  max(1, int((offset - self.tolerance / 2) * self.pitch * 0.5)))
        return 'move'

    def moved(self, steps):
        """Account only for acknowledged movement, never an attempted send."""
        if steps != self.next_steps or not 1 <= steps <= 8:
            raise ValueError('Recovery movement acknowledgement does not match')
        self.total_steps += steps
        self.moves += 1

    def fail(self, reason):
        self.reason = reason
        return 'pause'
