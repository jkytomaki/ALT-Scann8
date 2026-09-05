"""Transport safety and geometry checks independent of optional NCNN."""
from concurrent.futures import Future
import threading
import unittest
from unittest.mock import Mock

import numpy as np

from frame_alignment import AlignmentGuard, HoleMeasurement, measure_hole
from sprocket_yolo import YoloWorker, measure_corners
import test_frame_alignment as base
from test_frame_alignment import scanner_functions


class AdaptiveTests(unittest.TestCase):
    def test_clean_left_band_recovers_damaged_fixed_strips(self):
        image = np.zeros((1000, 1000, 3), np.uint8)
        image[375:625, 5:65] = 255
        for x, span in zip((30, 40, 50), ((390, 500), (450, 590), (580, 590))):
            image[span[0]:span[1], x:x + 3] = 0
        for film_type, pixels in [('S8', image), ('R8', 255 - image)]:
            m = measure_hole(pixels, film_type)
            self.assertEqual(m.source, 'adaptive')
            self.assertAlmostEqual(m.offset, -0.5)

    def test_competing_clean_bands_remain_unknown(self):
        image = np.zeros((1000, 1000, 3), np.uint8)
        image[300:500, 5:23] = 255
        image[600:800, 45:63] = 255
        self.assertIsNone(measure_hole(image, 'S8').offset)

    def test_similar_centers_but_disagreeing_edges_are_unknown(self):
        image = np.zeros((1000, 1000, 3), np.uint8)
        for x, start, end in [(5, 300, 700), (10, 350, 650), (15, 400, 600)]:
            image[start:end, x:x + 3] = 255
        self.assertIsNone(measure_hole(image, 'S8').offset)


class CornerTests(unittest.TestCase):
    pair = [(25, 375, 65, 420, .8, 0), (25, 580, 65, 625, .8, 1)]

    def test_unique_s8_pair_and_target_shift(self):
        m = measure_corners(self.pair, 1000, 1000, 'S8', 20)
        self.assertEqual(m.offset, -20)
        self.assertEqual(m.source, 'yolo')

    def test_r8_uses_gap_between_holes(self):
        boxes = [(25, 150, 65, 200, .8, 1), (25, 600, 65, 650, .8, 0)]
        self.assertEqual(measure_corners(boxes, 1000, 1000, 'R8').offset, -100)

    def test_rejects_missing_clipped_low_confidence_or_competing_corners(self):
        bad = [[], self.pair[:1], [(25, 0, 65, 420, .9, 0), self.pair[1]],
               [self.pair[0], (25, 580, 65, 625, .5, 1)],
               self.pair + [(25, 650, 65, 700, .9, 1)],
               [self.pair[0], (125, 580, 145, 625, .9, 1)],
               [(200, 375, 240, 420, .9, 0), (200, 580, 240, 625, .9, 1)]]
        for boxes in bad:
            with self.subTest(boxes=boxes):
                self.assertIsNone(measure_corners(boxes, 1000, 1000, 'S8').offset)

    def test_centered_yolo_requires_two_agreeing_exposures(self):
        guard = AlignmentGuard()
        self.assertEqual(guard.inspect(HoleMeasurement(0, 1000, source='yolo')), 'confirm')
        self.assertEqual(guard.inspect(HoleMeasurement(10, 1000, source='yolo')), 'accept')

    def test_disagreement_inside_tolerance_still_pauses(self):
        guard = AlignmentGuard(8)
        guard.inspect(HoleMeasurement(-30, 1000, source='yolo'))
        self.assertEqual(guard.inspect(HoleMeasurement(30, 1000, source='yolo')), 'pause')

    def test_yolo_reconfirms_after_correction(self):
        guard = AlignmentGuard(3, True, 280)
        self.assertEqual(guard.inspect(HoleMeasurement(200, 1000, source='yolo')), 'confirm')
        self.assertEqual(guard.inspect(HoleMeasurement(200, 1000, source='yolo')), 'nudge')
        self.assertEqual(guard.inspect(HoleMeasurement(10, 1000, source='yolo')), 'confirm')
        self.assertEqual(guard.inspect(HoleMeasurement(10, 1000, source='yolo')), 'accept')


class AsyncTests(unittest.TestCase):
    def test_busy_worker_owns_copy_and_does_not_queue_more(self):
        entered, finish = threading.Event(), threading.Event()
        def measure(image, *args):
            entered.set()
            finish.wait(2)
            return HoleMeasurement(float(image[0, 0, 0]), image.shape[0])
        worker = YoloWorker(Mock(measure=measure))
        image = np.zeros((10, 10, 3), np.uint8)
        future = worker.submit(image, 'S8')
        try:
            self.assertTrue(entered.wait(1))
            image[:] = 255
            self.assertIsNone(worker.submit(image, 'S8'))
        finally:
            finish.set()
        self.assertEqual(future.result(timeout=2).offset, 0)

    def test_runtime_error_returns_unknown(self):
        worker = YoloWorker(Mock(measure=Mock(side_effect=ImportError('ncnn missing'))))
        with self.assertLogs(level='ERROR'):
            result = worker.submit(np.zeros((10, 10, 3), np.uint8), 'S8').result(timeout=2)
        self.assertIsNone(result.offset)
        self.assertIn('ncnn missing', result.reason)

    def test_preflight_retains_one_request_until_inference_finishes(self):
        ns, requests = base.PreflightTests().preflight([HoleMeasurement(None, 1000)])
        future = Future()
        ns['alignment_yolo_worker'].submit.return_value = future
        self.assertFalse(ns['prepare_alignment_frame']())
        self.assertFalse(ns['prepare_alignment_frame']())
        ns['capture_settled_request'].assert_called_once()
        requests[0].release.assert_not_called()
        future.set_result(HoleMeasurement(0, 1000, source='yolo'))
        self.assertFalse(ns['prepare_alignment_frame']())  # needs a second exposure
        requests[0].release.assert_called_once()
        ns['adjust_auto_fine_tune'].assert_not_called()
        ns['send_alignment_nudge'].assert_not_called()
        self.assertIsNone(ns['alignment_yolo_pending'])

    def test_stop_discards_late_result_and_releases_request_once(self):
        future, request = Future(), Mock()
        ns = scanner_functions('reset_alignment_guard', alignment_yolo_pending=(request, future, 0, 1000),
            release_alignment_request=Mock(), alignment_pause_dialog=None)
        ns['reset_alignment_guard']()
        future.set_result(HoleMeasurement(0, 1000, source='yolo'))
        ns['reset_alignment_guard']()
        request.release.assert_called_once()
        self.assertIsNone(ns['alignment_yolo_pending'])

    def test_timeout_does_not_move_or_retain_request(self):
        ns, requests = base.PreflightTests().preflight([HoleMeasurement(None, 1000)])
        ns['alignment_yolo_pending'] = (requests[0], Future(), 0, 1000)
        ns['alignment_guard'] = AlignmentGuard()
        ns['alignment_guard'].inspect(HoleMeasurement(0, 1000, source='yolo'))
        self.assertFalse(ns['prepare_alignment_frame']())
        ns['pause_alignment_frame'].assert_called_once()
        requests[0].release.assert_called_once()
        ns['send_alignment_nudge'].assert_not_called()
        ns['capture_settled_request'].assert_not_called()


class ContinueTests(unittest.TestCase):
    def test_capture_sees_correct_hdr_session_parity(self):
        ns = base.HeldFrameSaveTests().state()
        seen = []
        ns['capture'].side_effect = lambda _: seen.append(ns['session_frames'])
        ns['save_alignment_frame_and_continue']()
        self.assertEqual(seen, [3])

    def test_save_error_stop_request_is_not_cleared_by_continue(self):
        ns = base.HeldFrameSaveTests().state()
        def request_stop(_):
            ns['ScanStopRequested'] = True
        ns['capture'].side_effect = request_stop
        ns['save_alignment_frame_and_continue']()
        ns['stop_scan'].assert_called_once()
        ns['win'].after.assert_not_called()

    def test_continue_counts_once_and_retries_only_advance(self):
        ns = base.HeldFrameSaveTests().state()
        def reset():
            ns['alignment_paused'] = False
        ns['reset_alignment_guard'].side_effect = reset
        ns['save_alignment_frame_and_continue']()
        ns['save_alignment_frame_and_continue']()
        self.assertEqual(ns['CurrentFrame'], 3)
        self.assertEqual(ns['FramesToGo'], 7)
        self.assertEqual(ns['session_frames'], 3)
        ns['capture'].assert_called_once_with('normal')
        ns['stop_scan'].assert_not_called()
        self.assertTrue(ns['RetryingFrame'])
        loop = scanner_functions('capture_loop', **{k: v for k, v in ns.items() if k != 'capture_loop'},
            FrameDetectMode='PFD', SimulatedRun=False, CMD_GET_NEXT_FRAME=12)
        loop['send_arduino_command'].return_value = False
        loop['capture_loop']()
        loop['capture_loop']()
        self.assertEqual(loop['CurrentFrame'], 3)
        self.assertEqual(loop['session_frames'], 3)
        loop['capture'].assert_called_once()
        self.assertEqual(loop['send_arduino_command'].call_count, 2)

    def test_counter_autostop_saves_last_frame_without_advancing(self):
        ns = base.HeldFrameSaveTests().state()
        ns['frames_to_go_str'].get.return_value = '1'
        ns['AutoStopEnabled'] = True
        ns['autostop_type'].get.return_value = 'counter_to_zero'
        ns['save_alignment_frame_and_continue']()
        ns['capture'].assert_called_once_with('normal')
        ns['stop_scan'].assert_called_once()
        ns['win'].after.assert_not_called()
        self.assertFalse(ns['RetryingFrame'])


if __name__ == '__main__':
    unittest.main()
