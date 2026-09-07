"""Evidence for movement between stationary exposures, independent of hole detection.

Displacements are in original main-stream pixels, second image minus first.
Matching picture detail establishes movement relative to the camera, not shaft motion.
"""
from dataclasses import dataclass, field
from pathlib import Path
import json
import uuid

import cv2
import numpy as np


# Initial conservative threshold: 0.25% of image height (7.6 px at 3040).
# This is separate from framing tolerance; report it with every result.
MOVEMENT_FRACTION = 0.0025


def picture_displacement(first, second):
    """Require a common translation in several textured picture regions.

Ignore the sprocket and borders. Independent phase correlation estimates must
agree and pass an aligned-image correlation check; blank/repetitive/changed
images are inconclusive, never silently called stable.
    """
    if first.size != second.size:
        return dict(confident=False, reason='Image dimensions changed', tiles=[])
    width, height = first.size
    scale = min(1., 1000 / width)
    size = (round(width * scale), round(height * scale))
    images = [cv2.resize(np.asarray(im.convert('L')), size, interpolation=cv2.INTER_AREA)
              .astype(np.float32) for im in (first, second)]
    h, w = images[0].shape
    tiles = []
    for row, (ya, yb) in enumerate(((.12, .48), (.52, .88))):
        for col, (xa, xb) in enumerate(((.16, .39), (.41, .64), (.66, .89))):
            a, b = [im[round(h * ya):round(h * yb), round(w * xa):round(w * xb)]
                    for im in images]
            if min(a.shape) < 24 or min(float(a.std()), float(b.std())) < 4:
                continue
            # High-pass removes illumination gradients without changing geometry.
            aa, bb = [v - cv2.GaussianBlur(v, (0, 0), 3) for v in (a, b)]
            window = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
            (dx, dy), response = cv2.phaseCorrelate(aa.copy(), bb.copy(), window)
            if not np.isfinite([dx, dy, response]).all():
                continue
            if response < .3 or abs(dx) > a.shape[1] * .2 or abs(dy) > a.shape[0] * .2:
                continue
            aligned = cv2.warpAffine(bb, np.float32([[1, 0, -dx], [0, 1, -dy]]),
                                     (a.shape[1], a.shape[0]))
            margin = int(np.ceil(max(abs(dx), abs(dy)))) + 3
            av, bv = aa[margin:-margin, margin:-margin], aligned[margin:-margin, margin:-margin]
            if av.size < 100 or min(float(av.std()), float(bv.std())) < .5:
                continue
            correlation = float(np.corrcoef(av.ravel(), bv.ravel())[0, 1])
            if not np.isfinite(correlation) or correlation < .8:
                continue
            tiles.append(dict(row=row, col=col, dx_px=dx / scale, dy_px=dy / scale,
                              response=float(response), correlation=correlation))
    result = dict(confident=False, reason='Insufficient agreeing picture detail', tiles=tiles)
    if len(tiles) < 3:
        return result
    shifts = np.array([(t['dx_px'], t['dy_px']) for t in tiles])
    center = np.median(shifts, axis=0)
    # One downsampled pixel of agreement, with at least 3 regions spanning rows
    # and columns. Strong mutually conflicting matches remain inconclusive.
    supporting = np.linalg.norm(shifts - center, axis=1) <= 1. / scale
    selected = [t for t, keep in zip(tiles, supporting) if keep]
    if (len(selected) < 3 or len(selected) < len(tiles) * .8
            or len({t['row'] for t in selected}) < 2 or len({t['col'] for t in selected}) < 2):
        return result
    dx, dy = np.median(shifts[supporting], axis=0)
    return dict(confident=True, reason='', dx_px=float(dx), dy_px=float(dy),
                displacement_px=float(np.hypot(dx, dy)), tiles=tiles)


@dataclass
class ExposureSample:
    image: object  # Owned RGB PIL image; never a camera buffer view.
    metadata: dict
    offset_px: float | None
    source: str
    strips: dict
    settle_ms: int


def compare_exposures(first, second):
    result = picture_displacement(first.image, second.image)
    height = first.image.height
    threshold = max(2., height * MOVEMENT_FRACTION)
    delta = (second.offset_px - first.offset_px
             if first.offset_px is not None and second.offset_px is not None else None)
    start, end = (sample.metadata.get('SensorTimestamp') for sample in (first, second))
    if start is None or end is None or end <= start:
        result.update(confident=False, reason='Missing or non-increasing exposure timestamps')
    moving = result['confident'] and result['displacement_px'] > threshold
    disagreement = delta is not None and abs(delta) > threshold
    status = ('movement' if moving else 'inconclusive' if not result['confident'] else
              'detection_disagreement' if disagreement else 'stable')
    result.update(status=status, threshold_px=threshold, hole_delta_px=delta,
                  interval_ms=(end - start) / 1e6 if start is not None and end is not None else None,
                  first=sample_details(first), second=sample_details(second))
    return result


def sample_details(sample):
    return dict(metadata=sample.metadata, offset_px=sample.offset_px, source=sample.source,
                strips=sample.strips, settle_ms=sample.settle_ms,
                width=sample.image.width, height=sample.image.height)


def save_evidence(folder, run_id, frame, first, second, result):
    """Unique directories preserve the original pair across held-frame rechecks."""
    destination = Path(folder) / 'stabilization' / run_id / f'frame-{frame:05d}-{uuid.uuid4().hex[:12]}'
    destination.mkdir(parents=True, exist_ok=False)
    first.image.save(destination / 'first.png', compress_level=1)
    second.image.save(destination / 'second.png', compress_level=1)
    (destination / 'measurement.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    return str(destination)


@dataclass
class StabilizationTest:
    target: int = 50
    records: dict = field(default_factory=dict)

    def record(self, frame, result):
        # A held recheck has had extra settling time. Never replace the original
        # arrival result or let retries count as additional test frames.
        self.records.setdefault(frame, result)

    @property
    def complete(self):
        return len(self.records) >= self.target

    def summary(self):
        records = list(self.records.values())
        displacements = [r['displacement_px'] for r in records if r.get('confident')]
        return dict(target=self.target, checked=len(records),
                    movement=sum(r['status'] == 'movement' for r in records),
                    detection_disagreements=sum(r['status'] == 'detection_disagreement' for r in records),
                    inconclusive=sum(r['status'] == 'inconclusive' for r in records),
                    largest_displacement_px=max(displacements, default=None),
                    delays_ms=sorted({r['first']['settle_ms'] for r in records}),
                    frames=self.records)
