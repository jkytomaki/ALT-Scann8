"""Exercise scanner functions without importing camera hardware or starting Tk."""
import ast
import logging
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

import cv2
import numpy as np
from PIL import Image
from rolling_average import RollingAverage

SOURCE = Path(__file__).resolve().parents[1] / 'ALT-Scann8.py'


def scanner_functions(*names, **state):
    tree = ast.parse(SOURCE.read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(nodes) == len(names)
    ns = dict(cv2=cv2, np=np, logging=logging, time=time, Image=Image)
    ns.update(state)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns


class DetectionTests(unittest.TestCase):
    def test_missing_hole_is_not_a_near_zero_offset(self):
        ns = scanner_functions('is_frame_centered', PreviewHeight=500, FrameVCenterImageShift=0)
        self.assertEqual(ns['is_frame_centered'](np.zeros((1000, 1000, 3), np.uint8)), (False, None))

    def test_valid_minus_one_offset_is_preserved(self):
        ns = scanner_functions('is_frame_centered', PreviewHeight=500, FrameVCenterImageShift=0)
        img = np.zeros((1000, 1000, 3), np.uint8)
        img[399:600, 40:50] = 255
        self.assertEqual(ns['is_frame_centered'](img), (True, -1))


class FineTuneTests(unittest.TestCase):
    def test_never_sends_values_rejected_by_firmware(self):
        for start, offset, expected in [(90, 300, 95), (10, -300, 5)]:
            average = RollingAverage(5)
            average.add_value(offset)
            sender = Mock(return_value=True)
            ns = scanner_functions('adjust_auto_fine_tune', offset_image=average,
                FrameFineTuneValue=start, PreviousFrameFineTuneValue=start,
                auto_fine_tune_wait=0, auto_fine_tune_limit_warned=False,
                CaptureResolution='4056x3040', CMD_SET_FRAME_FINE_TUNE=54,
                send_arduino_command=sender, frame_fine_tune_value=Mock())
            ns['adjust_auto_fine_tune']()
            sender.assert_called_once_with(54, expected)


class SaveTests(unittest.TestCase):
    def run_save(self, *, detect=False, offset=300, file_type='dng', hdr_idx=0):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        log = Path(temp.name) / 'errors.log'
        request = Mock()
        request.make_array.return_value = np.zeros((100, 100, 3), np.uint8)
        work = queue.Queue()
        work.put(('request', request, 42, hdr_idx))
        work.put('end')
        average = RollingAverage(5)
        adjust = Mock()
        ns = scanner_functions('capture_save_thread', active_threads=1,
            ExitingApp=False, FileType=file_type, MaxQueueSize=10,
            END_TOKEN='end', REQUEST_TOKEN='request', IMAGE_TOKEN='image',
            HdrFrameFilenamePattern='picture-%05d.%i.%s', FrameFilenamePattern='picture-%05d.%s',
            DetectMisalignedFrames=detect, AutoFineTuneEnabled=True,
            FilmType='S8', MisalignedFrameTolerance=8, NegativeImage=False,
            is_frame_centered=Mock(return_value=(False, offset)), offset_image=average,
            adjust_auto_fine_tune=adjust, scan_error_counter=0, scan_error_total_frames_counter=10,
            scan_error_counter_value=Mock(), scan_error_log_fullpath=str(log),
            CurrentFrame=99, total_wait_time_save_image=0, time_save_image=RollingAverage(5))
        ns['capture_save_thread'](work, threading.Event(), 1)
        return request, average, adjust, log, ns

    def test_dng_and_png_auto_tune_without_error_reporting_or_rawpy(self):
        for file_type in ['dng', 'png']:
            with self.subTest(file_type=file_type):
                request, average, adjust, _, _ = self.run_save(file_type=file_type)
                request.make_array.assert_called_once_with('main')
                self.assertEqual(average.get_average(), 300)
                adjust.assert_called_once()
                request.release.assert_called_once()

    def test_missing_detection_never_enters_average(self):
        _, average, adjust, _, _ = self.run_save(offset=None)
        self.assertIsNone(average.get_average())
        adjust.assert_not_called()

    def test_dng_log_uses_captured_frame_number(self):
        _, _, _, log, _ = self.run_save(detect=True)
        self.assertEqual(log.read_text(), 'Misaligned frame, 42\n')

    def test_hdr_subexposure_does_not_repeat_feedback(self):
        request, _, adjust, _, _ = self.run_save(hdr_idx=2)
        request.make_array.assert_not_called()
        adjust.assert_not_called()


if __name__ == '__main__':
    unittest.main()
