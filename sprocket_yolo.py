"""Optional NCNN corner detector. No camera, GUI, transport, or downloads.

Input is RGB (Picamera2 BGR888 arrays are byte-ordered RGB). The exported
YOLO11 model predicts corner boxes, not whole sprocket-hole rectangles.
"""
from concurrent.futures import Future
from pathlib import Path
import logging
import threading
import time

import cv2
import numpy as np

from frame_alignment import HoleMeasurement


def measure_corners(boxes, height, width, film_type, target_shift=0):
    """Accept a unique, complete, high-confidence pair in the left film margin."""
    corners = {0: [], 1: []}
    for x0, y0, x1, y1, score, label in boxes:
        if label not in corners or score < 0.65 or not np.isfinite([x0, y0, x1, y1, score]).all():
            continue
        if not (0 <= x0 < x1 < width * 0.15 and 0 < y0 < y1 < height):
            continue
        corners[label].append((x1, y0 if label == 0 else y1))
    candidates = []
    for top_x, top_y in corners[0]:
        for bottom_x, bottom_y in corners[1]:
            start, end = (top_y, bottom_y) if film_type == 'S8' else (bottom_y, top_y)
            if abs(top_x - bottom_x) <= width * 0.015 and height * 0.08 < end - start < height * 0.6:
                candidates.append((start + end) / 2)
    if len(candidates) != 1:
        return HoleMeasurement(None, height, 'YOLO did not find one complete, confident corner pair', 'yolo')
    return HoleMeasurement(candidates[0] - height / 2 - target_shift, height, source='yolo')


class SprocketYolo:
    def __init__(self, model_dir=None, threads=2):
        self.model_dir = Path(model_dir or Path(__file__).parent / 'models' / 'sprocket-ncnn')
        self.threads = threads
        self.net = None

    def detect(self, rgb):
        import ncnn  # Optional dependency; never prevent the scanner from starting.
        if self.net is None:
            net = ncnn.Net()
            net.opt.num_threads = self.threads
            net.opt.use_vulkan_compute = False
            if net.load_param(str(self.model_dir / 'model.ncnn.param')) != 0:
                raise RuntimeError('Cannot load YOLO parameters')
            if net.load_model(str(self.model_dir / 'model.ncnn.bin')) != 0:
                raise RuntimeError('Cannot load YOLO weights')
            self.net = net
        height, width = rgb.shape[:2]
        scale = min(640 / width, 640 / height)
        resized = cv2.resize(rgb, (round(width * scale), round(height * scale)))
        left = (640 - resized.shape[1]) // 2
        top = (640 - resized.shape[0]) // 2
        padded = np.full((640, 640, 3), 114, dtype=np.uint8)
        padded[top:top + resized.shape[0], left:left + resized.shape[1]] = resized
        chw = np.ascontiguousarray(padded.transpose(2, 0, 1), dtype=np.float32) / 255
        with self.net.create_extractor() as extractor:
            extractor.input('in0', ncnn.Mat(chw).clone())
            status, output = extractor.extract('out0')
            if status != 0:
                raise RuntimeError('YOLO inference failed')
            rows = np.array(output).copy().T
        if rows.ndim != 2 or rows.shape[1] != 7:
            raise RuntimeError(f'Unexpected YOLO output shape: {rows.shape}')
        boxes = []
        for label in (0, 1):  # Frame seams alone cannot establish sprocket position.
            selected = rows[rows[:, 4 + label] >= 0.65]
            xywh = selected[:, :4].copy()
            xywh[:, :2] -= xywh[:, 2:] / 2
            scores = selected[:, 4 + label]
            keep = cv2.dnn.NMSBoxes(xywh.tolist(), scores.tolist(), 0.65, 0.45)
            for i in np.asarray(keep, dtype=int).reshape(-1):
                x, y, w, h = xywh[i]
                boxes.append(((x - left) / scale, (y - top) / scale,
                              (x + w - left) / scale, (y + h - top) / scale,
                              float(scores[i]), label))
        return boxes

    def measure(self, rgb, film_type, target_shift=0):
        start = time.monotonic()
        measurement = measure_corners(self.detect(rgb), *rgb.shape[:2], film_type, target_shift)
        logging.info('YOLO alignment %.0f ms: %s', (time.monotonic() - start) * 1000, measurement)
        return measurement


class YoloWorker:
    """At most one daemon task; a timed-out inference cannot build a backlog.

    Workers own only an image copy. The Tk caller owns requests and discards
    stale futures on stop/retry, so late results cannot move film or save it.
    """
    def __init__(self, detector=None):
        self.detector = detector or SprocketYolo()
        self.future = None

    def submit(self, rgb, film_type, target_shift=0):
        if self.future is not None and not self.future.done():
            return None
        future = self.future = Future()
        owned = rgb.copy()
        def run():
            try:
                result = self.detector.measure(owned, film_type, target_shift)
            except Exception as error:
                logging.exception('YOLO alignment unavailable')
                result = HoleMeasurement(None, owned.shape[0], f'YOLO unavailable: {error}', 'yolo')
            future.set_result(result)
        threading.Thread(target=run, name='sprocket-yolo', daemon=True).start()
        return future
