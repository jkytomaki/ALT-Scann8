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
from frame_alignment import AlignmentGuard, HoleMeasurement, measure_hole

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
                CaptureResolution='4056x3040', CMD_SET_FRAME_FINE_TUNE=54, ExpertMode=True,
                send_arduino_command=sender, frame_fine_tune_value=Mock())
            ns['adjust_auto_fine_tune']()
            sender.assert_called_once_with(54, expected)


    def test_basic_mode_does_not_require_expert_widgets(self):
        average = RollingAverage(5)
        average.add_value(300)
        ns = scanner_functions('adjust_auto_fine_tune', offset_image=average,
            FrameFineTuneValue=20, PreviousFrameFineTuneValue=20,
            auto_fine_tune_wait=0, auto_fine_tune_limit_warned=False,
            CaptureResolution='4056x3040', CMD_SET_FRAME_FINE_TUNE=54, ExpertMode=False,
            send_arduino_command=Mock(return_value=True))
        ns['adjust_auto_fine_tune']()
        self.assertEqual(ns['FrameFineTuneValue'], 30)

    def test_failed_setting_write_does_not_claim_success(self):
        average = RollingAverage(5)
        average.add_value(300)
        ns = scanner_functions('adjust_auto_fine_tune', offset_image=average,
            FrameFineTuneValue=20, PreviousFrameFineTuneValue=20,
            auto_fine_tune_wait=0, auto_fine_tune_limit_warned=False,
            CaptureResolution='4056x3040', CMD_SET_FRAME_FINE_TUNE=54, ExpertMode=True,
            send_arduino_command=Mock(return_value=False), frame_fine_tune_value=Mock())
        ns['adjust_auto_fine_tune']()
        self.assertEqual(ns['FrameFineTuneValue'], 20)
        self.assertEqual(ns['PreviousFrameFineTuneValue'], 20)
        ns['frame_fine_tune_value'].set.assert_not_called()


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

    def test_save_workers_never_drive_transport_feedback(self):
        for file_type in ['dng', 'png']:
            with self.subTest(file_type=file_type):
                request, average, adjust, _, _ = self.run_save(file_type=file_type, detect=True)
                request.make_array.assert_called_once_with('main')
                self.assertIsNone(average.get_average())
                adjust.assert_not_called()
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




class GuardTests(unittest.TestCase):
    def test_confirms_on_same_film_frame_then_pauses(self):
        guard = AlignmentGuard(3)
        self.assertEqual(guard.inspect(HoleMeasurement(200, 1000)), 'confirm')
        self.assertEqual(guard.inspect(HoleMeasurement(200, 1000)), 'pause')

    def test_transient_measurement_does_not_force_pause(self):
        guard = AlignmentGuard(3)
        self.assertEqual(guard.inspect(HoleMeasurement(None, 1000)), 'confirm')
        self.assertEqual(guard.inspect(HoleMeasurement(5, 1000)), 'accept')

    def test_rejects_clipped_or_ambiguous_holes(self):
        for spans in [[(0, 200)], [(200, 400), (600, 800)]]:
            img = np.zeros((1000, 1000, 3), np.uint8)
            for start, end in spans:
                img[start:end, 20:70] = 255
            self.assertIsNone(measure_hole(img, 'S8').offset)

    def test_strips_agree_despite_one_shadowed_strip(self):
        img = np.zeros((1000, 1000, 3), np.uint8)
        img[600:800, 40:70] = 255
        self.assertAlmostEqual(measure_hole(img, 'S8').offset, 199.5)

    def test_scan_does_not_count_save_or_advance_rejected_frame(self):
        ns = scanner_functions('capture_loop', ScanStopRequested=False, ScanOngoing=True,
            alignment_paused=False, FrameDetectMode='PFD', NewFrameAvailable=True, RetryingFrame=False,
            prepare_alignment_frame=Mock(return_value=False), win=Mock(), CurrentFrame=42,
            capture=Mock(), send_arduino_command=Mock())
        ns['capture_loop']()
        self.assertEqual(ns['CurrentFrame'], 42)
        ns['capture'].assert_not_called()
        ns['send_arduino_command'].assert_not_called()


class PreflightTests(unittest.TestCase):
    def preflight(self, measurements, mode='Pause', stale=False):
        requests = []
        for _ in measurements:
            request = Mock()
            request.get_metadata.return_value = dict(SensorTimestamp=0 if stale else 10**20, ExposureTime=10000)
            request.make_array.return_value = np.zeros((1000, 1000, 3), np.uint8)
            requests.append(request)
        average = RollingAverage(5)
        ns = scanner_functions('prepare_alignment_frame', 'take_alignment_request',
            alignment_guard=None, alignment_request=None, SimulatedRun=False, CameraDisabled=False,
            AlignmentGuardMode=mode, AlignmentGuardTolerance=3, AlignmentGuard=AlignmentGuard,
            AutoFineTuneEnabled=True, StabilizationDelayValue=0, CaptureSettleDeadline=0,
            capture_settled_request=Mock(side_effect=requests), FrameVCenterImageShift=0,
            PreviewHeight=500, FilmType='S8', FrameStepsS8=280, FrameStepsR8=240,
            alignment_firmware_supported=False, alignment_move_pending=None, alignment_move_result=None,
            auto_fine_tune_limit_warned=False,
            CurrentFrame=42, send_alignment_nudge=Mock(return_value=True), measure_hole=Mock(side_effect=measurements),
            offset_image=average, CaptureResolution='4056x3040', adjust_auto_fine_tune=Mock(),
            set_alignment_status=Mock(), pause_alignment_frame=Mock())
        return ns, requests

    def test_dng_feedback_runs_without_reporting_or_rawpy(self):
        ns, requests = self.preflight([HoleMeasurement(20, 1000)], mode='Off')
        self.assertTrue(ns['prepare_alignment_frame']())
        ns['adjust_auto_fine_tune'].assert_called_once()
        self.assertAlmostEqual(ns['offset_image'].get_average(), 60.8)
        self.assertIs(ns['take_alignment_request'](), requests[0])
        self.assertIsNone(ns['alignment_request'])
        requests[0].release.assert_not_called()

    def test_confirmation_exposures_released_without_counting_as_film_frames(self):
        ns, requests = self.preflight([HoleMeasurement(200, 1000), HoleMeasurement(200, 1000)])
        self.assertFalse(ns['prepare_alignment_frame']())
        self.assertFalse(ns['prepare_alignment_frame']())
        ns['pause_alignment_frame'].assert_called_once()
        ns['adjust_auto_fine_tune'].assert_called_once()
        self.assertEqual(len(ns['offset_image'].window), 1)
        for request in requests:
            request.release.assert_called_once()

    def test_unknown_never_feeds_trim_loop(self):
        ns, _ = self.preflight([HoleMeasurement(None, 1000)], mode='Off')
        self.assertTrue(ns['prepare_alignment_frame']())
        ns['adjust_auto_fine_tune'].assert_not_called()
        self.assertIsNone(ns['offset_image'].get_average())

    def test_stale_exposure_pauses_instead_of_failing_open(self):
        ns, requests = self.preflight([HoleMeasurement(0, 1000)], stale=True)
        self.assertFalse(ns['prepare_alignment_frame']())
        ns['pause_alignment_frame'].assert_called_once()
        requests[0].release.assert_called_once()
        ns['measure_hole'].assert_not_called()




class CorrectionTests(unittest.TestCase):
    def test_corrects_19_percent_shift_without_advancing_to_another_film_frame(self):
        guard = AlignmentGuard(3, correct=True, steps_per_frame=280)
        offset = 588.0
        self.assertEqual(guard.inspect(HoleMeasurement(offset, 3040)), 'confirm')
        while True:
            decision = guard.inspect(HoleMeasurement(offset, 3040))
            if decision == 'accept':
                break
            self.assertEqual(decision, 'nudge', guard.reason)
            self.assertLessEqual(guard.next_steps, 40)
            offset -= guard.next_steps * 8.8  # Measured pitch can be smaller than sensor height.
        self.assertLessEqual(abs(offset), 3040 * 0.015)
        self.assertLessEqual(guard.attempts, 4)
        self.assertLessEqual(guard.total_steps, 280 // 3)

    def test_refuses_reverse_motion_and_overshoot(self):
        guard = AlignmentGuard(3, correct=True, steps_per_frame=280)
        guard.inspect(HoleMeasurement(500, 3040))
        self.assertEqual(guard.inspect(HoleMeasurement(500, 3040)), 'nudge')
        self.assertEqual(guard.inspect(HoleMeasurement(-200, 3040)), 'pause')
        self.assertIn('reversal', guard.reason)

    def test_no_progress_cannot_loop_forever(self):
        guard = AlignmentGuard(3, correct=True, steps_per_frame=280)
        guard.inspect(HoleMeasurement(500, 3040))
        guard.inspect(HoleMeasurement(500, 3040))
        self.assertEqual(guard.inspect(HoleMeasurement(500, 3040)), 'pause')
        self.assertEqual(guard.attempts, 1)

    def test_disagreeing_exposures_never_move_film(self):
        guard = AlignmentGuard(3, correct=True, steps_per_frame=280)
        guard.inspect(HoleMeasurement(500, 3040))
        self.assertEqual(guard.inspect(HoleMeasurement(300, 3040)), 'pause')
        self.assertEqual(guard.total_steps, 0)

    def test_total_movement_is_bounded_even_with_slow_progress(self):
        guard = AlignmentGuard(3, correct=True, steps_per_frame=280)
        guard.inspect(HoleMeasurement(1200, 3040))
        for offset in (1200, 1100, 1000, 900, 800):
            if guard.inspect(HoleMeasurement(offset, 3040)) == 'pause':
                break
        else:
            self.fail('Guard never paused')
        self.assertLessEqual(guard.total_steps, 280 // 3)
        self.assertLessEqual(guard.attempts, 4)


class MovementProtocolTests(unittest.TestCase):
    preflight = PreflightTests.preflight

    def test_old_firmware_pauses_without_sending_motion(self):
        ns, _ = self.preflight([HoleMeasurement(200, 1000), HoleMeasurement(200, 1000)], mode='Correct')
        ns['prepare_alignment_frame']()
        ns['prepare_alignment_frame']()
        ns['send_alignment_nudge'].assert_not_called()
        self.assertIn('firmware update', ns['pause_alignment_frame'].call_args.args[0])

    def test_waits_for_ack_before_acquiring_another_exposure(self):
        ns, _ = self.preflight([HoleMeasurement(0, 1000)])
        ns['alignment_guard'] = AlignmentGuard()
        ns['alignment_move_pending'] = (256 + 20, time.monotonic() + 3)
        self.assertFalse(ns['prepare_alignment_frame']())
        ns['capture_settled_request'].assert_not_called()
        ns['pause_alignment_frame'].assert_not_called()

    def test_timed_out_motion_is_not_repeated(self):
        ns, _ = self.preflight([HoleMeasurement(0, 1000)])
        ns['alignment_guard'] = AlignmentGuard()
        ns['alignment_move_pending'] = (256 + 20, time.monotonic() - 1)
        self.assertFalse(ns['prepare_alignment_frame']())
        ns['capture_settled_request'].assert_not_called()
        ns['send_alignment_nudge'].assert_not_called()
        ns['pause_alignment_frame'].assert_called_once()

    def test_refused_motion_never_saves_or_moves_again(self):
        ns, _ = self.preflight([HoleMeasurement(0, 1000)])
        ns['alignment_guard'] = AlignmentGuard()
        ns['alignment_move_pending'] = (256 + 20, time.monotonic() + 3)
        ns['alignment_move_result'] = 0
        self.assertFalse(ns['prepare_alignment_frame']())
        ns['capture_settled_request'].assert_not_called()
        ns['send_alignment_nudge'].assert_not_called()
        ns['pause_alignment_frame'].assert_called_once()

    def test_ack_resets_settle_deadline_before_rechecking(self):
        ns, _ = self.preflight([HoleMeasurement(0, 1000)])
        ns['alignment_guard'] = AlignmentGuard()
        ns['alignment_move_pending'] = (256 + 20, time.monotonic() + 3)
        ns['alignment_move_result'] = 20
        ns['StabilizationDelayValue'] = 250
        before = time.clock_gettime(time.CLOCK_BOOTTIME)
        self.assertTrue(ns['prepare_alignment_frame']())
        self.assertGreaterEqual(ns['CaptureSettleDeadline'], before + 0.25)

    def test_stale_ack_cannot_complete_a_different_move(self):
        ns = scanner_functions('receive_alignment_response', Controller_Id=1,
            alignment_move_pending=(532, time.monotonic() + 3), alignment_move_result=None,
            alignment_firmware_supported=True, set_alignment_status=Mock())
        ns['receive_alignment_response'](276, 20)
        self.assertIsNone(ns['alignment_move_result'])
        ns['receive_alignment_response'](532, 20)
        self.assertEqual(ns['alignment_move_result'], 20)

    def test_i2c_send_failure_clears_pending_without_retry(self):
        sender = Mock(return_value=False)
        ns = scanner_functions('send_alignment_nudge', alignment_move_token=0,
            alignment_firmware_supported=True, alignment_move_pending=None,
            alignment_move_result=None, CurrentFrame=42, set_alignment_status=Mock(),
            send_arduino_command=sender, CMD_ALIGN_FRAME=44)
        self.assertFalse(ns['send_alignment_nudge'](20))
        sender.assert_called_once_with(44, 276)
        self.assertIsNone(ns['alignment_move_pending'])


if __name__ == '__main__':
    unittest.main()
