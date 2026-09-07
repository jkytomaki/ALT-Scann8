"""Movement evidence and scan-boundary tests, with no camera or transport access."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import cv2
import numpy as np
from PIL import Image

from frame_alignment import HoleMeasurement
from stabilization import (ExposureSample, StabilizationTest, compare_exposures,
                           picture_displacement, save_evidence)
import test_frame_alignment as base
from test_frame_alignment import scanner_functions


def textured_image():
    rng = np.random.default_rng(42)
    noise = rng.integers(0, 256, (768, 1024), dtype=np.uint8)
    return cv2.GaussianBlur(noise, (0, 0), 2)


def sample(array, timestamp=1_000_000_000, offset=0, delay=250):
    return ExposureSample(Image.fromarray(array).convert('RGB'),
                          dict(SensorTimestamp=timestamp, ExposureTime=8981), offset,
                          'strips', {}, delay)


class ComparisonTests(unittest.TestCase):
    def test_known_translation_including_71_full_resolution_pixels(self):
        original = textured_image()
        for dx, dy in ((0, 0), (0, 1), (3, -18), (0, -71)):
            with self.subTest(dx=dx, dy=dy):
                # Test 71px at the real capture resolution too.
                a = cv2.resize(original, (4056, 3040)) if dy == -71 else original
                b = cv2.warpAffine(a, np.float32([[1, 0, dx], [0, 1, dy]]),
                                   (a.shape[1], a.shape[0]), borderMode=cv2.BORDER_REFLECT)
                r = compare_exposures(sample(a), sample(b, 1_341_000_000, dy))
                self.assertTrue(r['confident'], r)
                self.assertAlmostEqual(r['dy_px'], dy, delta=1)
                self.assertAlmostEqual(r['dx_px'], dx, delta=1)
                self.assertEqual(r['status'], 'stable' if abs(dy) <= 1 else 'movement')
                self.assertEqual(r['interval_ms'], 341)

    def test_hole_only_change_is_not_film_movement(self):
        a = textured_image()
        b = a.copy()
        a[200:400, :80] = 255
        b[271:471, :80] = 255
        r = compare_exposures(sample(a), sample(b, 1_341_000_000, 71))
        self.assertEqual(r['status'], 'detection_disagreement')
        self.assertLess(r['displacement_px'], .1)

    def test_blank_unrelated_and_conflicting_regions_are_inconclusive(self):
        a = textured_image()
        shifted = cv2.warpAffine(a, np.float32([[1, 0, 0], [0, 1, 18]]),
                                (1024, 768), borderMode=cv2.BORDER_REFLECT)
        conflict = shifted.copy()
        conflict[:384] = a[:384]
        for first, second in ((np.zeros_like(a), np.zeros_like(a)), (a, np.flipud(a)), (a, conflict)):
            with self.subTest():
                r = compare_exposures(sample(first), sample(second, 1_341_000_000))
                self.assertEqual(r['status'], 'inconclusive')

    def test_gain_change_does_not_look_like_motion_and_stale_timestamps_fail(self):
        a = textured_image()
        b = np.clip(a.astype(float) * 1.2 + 7, 0, 255).astype(np.uint8)
        self.assertEqual(compare_exposures(sample(a), sample(b, 1_341_000_000))['status'], 'stable')
        self.assertEqual(compare_exposures(sample(a), sample(a))['status'], 'inconclusive')

    def test_lossless_evidence_survives_rechecks_with_metadata(self):
        first, second = sample(textured_image()), sample(textured_image(), 1_341_000_000)
        result = compare_exposures(first, second)
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(save_evidence(directory, 'run', 236, first, second, result)) for _ in range(2)]
            self.assertNotEqual(*paths)
            for path in paths:
                np.testing.assert_array_equal(np.asarray(Image.open(path / 'first.png')), np.asarray(first.image))
                np.testing.assert_array_equal(np.asarray(Image.open(path / 'second.png')), np.asarray(second.image))
                self.assertEqual(json.loads((path / 'measurement.json').read_text())['interval_ms'], 341)

    def test_test_counts_original_arrivals_and_reports_mixed_delays(self):
        test = StabilizationTest()
        a = textured_image()
        result = compare_exposures(sample(a), sample(a, 1_341_000_000))
        test.record(1, dict(result, status='movement'))
        test.record(1, result)  # Stable held retry must not erase the movement.
        for frame in range(2, 51):
            test.record(frame, dict(result, first=dict(settle_ms=350)))
        self.assertTrue(test.complete)
        self.assertEqual(test.summary()['checked'], 50)
        self.assertEqual(test.summary()['movement'], 1)
        self.assertEqual(test.summary()['delays_ms'], [250, 350])


class CaptureIntegrationTests(unittest.TestCase):
    def preflight(self, move=0, diagnostic=True):
        measurements = [HoleMeasurement(0 if diagnostic else -100, 768)] * 2
        ns, requests = base.PreflightTests().preflight(measurements, mode='Correct')
        ns.update(stabilization_test=None, stabilization_recheck=False, alignment_comparison=None,
                  CurrentFrame=4 if diagnostic else 3, CurrentDir='/unused',
                  max_inactivity_delay=12, last_frame_time=0,
                  ExposureSample=ExposureSample, compare_exposures=compare_exposures,
                  save_evidence=Mock(return_value='/evidence'), alignment_settings=Mock(return_value={}))
        names = ('stabilization_sample_due', 'stabilization_sample', 'finish_stabilization_pair')
        real = scanner_functions(*names, **{k: v for k, v in ns.items() if k not in names})
        # Functions must share a single globals dictionary, including preflight.
        all_names = names + ('prepare_alignment_frame', 'check_arrival_feedback', 'record_alignment_arrival',
                             'finish_alignment_diagnostic', 'take_alignment_request')
        ns = scanner_functions(*all_names, **{k: v for k, v in real.items() if k not in all_names})
        a = textured_image()
        b = cv2.warpAffine(a, np.float32([[1, 0, 0], [0, 1, move]]),
                           (1024, 768), borderMode=cv2.BORDER_REFLECT)
        for index, (req, image) in enumerate(zip(requests, (a, b))):
            req.make_array.return_value = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            req.make_image.return_value = Image.fromarray(image).convert('RGB')
            req.get_metadata.return_value = dict(SensorTimestamp=10**20 + index * 341_000_000,
                                                 ExposureTime=8981)
        return ns, requests

    def test_every_fifth_even_without_auto_fine_tune_and_guard(self):
        ns = scanner_functions('stabilization_sample_due', stabilization_test=None,
                               stabilization_recheck=False, CurrentFrame=0)
        for number in range(1, 16):
            ns['CurrentFrame'] = number - 1
            self.assertEqual(ns['stabilization_sample_due'](), number % 5 == 0)
        ns['stabilization_test'] = StabilizationTest()
        ns['CurrentFrame'] = 0
        self.assertTrue(ns['stabilization_sample_due']())
        ns, requests = self.preflight()
        ns.update(AlignmentGuardMode='Off', fine_tune_availability=Mock(return_value='Disabled'))
        self.assertFalse(ns['prepare_alignment_frame']())
        self.assertTrue(ns['prepare_alignment_frame']())
        self.assertIs(ns['take_alignment_request'](), requests[0])
        requests[0].release.assert_not_called()
        requests[1].release.assert_called_once()

    def test_periodic_movement_pauses_before_original_capture_or_tuning(self):
        ns, requests = self.preflight(move=18)
        self.assertFalse(ns['prepare_alignment_frame']())
        ns['adjust_auto_fine_tune'].assert_not_called()
        self.assertFalse(ns['prepare_alignment_frame']())
        ns['pause_alignment_frame'].assert_called_once()
        ns['send_alignment_nudge'].assert_not_called()
        ns['adjust_auto_fine_tune'].assert_not_called()
        ns['save_evidence'].assert_called_once()
        requests[1].release.assert_called_once()
        self.assertEqual(ns['CurrentFrame'], 4)

    def test_confirmation_pair_is_preserved_on_non_sampled_frame(self):
        ns, requests = self.preflight(move=-18, diagnostic=False)
        self.assertFalse(ns['prepare_alignment_frame']())
        requests[0].release.assert_called_once()
        self.assertFalse(ns['prepare_alignment_frame']())
        ns['save_evidence'].assert_called_once()
        ns['pause_alignment_frame'].assert_called_once()
        requests[1].release.assert_called_once()
        self.assertEqual(ns['alignment_comparison'][2]['status'], 'movement')

    def test_evidence_write_failure_holds_film(self):
        ns, _ = self.preflight(move=18)
        ns['save_evidence'].side_effect = OSError('disk full')
        self.assertFalse(ns['prepare_alignment_frame']())
        self.assertFalse(ns['prepare_alignment_frame']())
        self.assertIn('NOT SAVED', ns['alignment_comparison'][3])
        ns['pause_alignment_frame'].assert_called_once()
        ns['send_alignment_nudge'].assert_not_called()

    def test_pause_off_keeps_comparison_and_evidence_without_pausing(self):
        ns, requests = self.preflight(move=18)
        ns['PauseOnCreep'] = False
        self.assertFalse(ns['prepare_alignment_frame']())
        self.assertTrue(ns['prepare_alignment_frame']())
        ns['save_evidence'].assert_called_once()
        ns['pause_alignment_frame'].assert_not_called()
        self.assertIs(ns['alignment_request'], requests[0])

    def test_pause_off_still_protects_overshoot_in_second_periodic_exposure(self):
        ns, _ = self.preflight(move=-18)
        ns['PauseOnCreep'] = False
        ns['measure_hole'].side_effect = [HoleMeasurement(0, 768), HoleMeasurement(-100, 768)]
        self.assertFalse(ns['prepare_alignment_frame']())
        self.assertFalse(ns['prepare_alignment_frame']())
        self.assertIn('outside alignment tolerance', ns['pause_alignment_frame'].call_args.args[0])

    def test_measurement_off_uses_one_exposure_on_fifth_frame(self):
        ns, requests = self.preflight(move=18)
        ns.update(MeasureCreep=False, PauseOnCreep=False)
        self.assertTrue(ns['prepare_alignment_frame']())
        ns['capture_settled_request'].assert_called_once()
        ns['save_evidence'].assert_not_called()
        self.assertIs(ns['alignment_request'], requests[0])

    def test_measurement_off_between_checks_reuses_first_without_second_capture(self):
        ns, requests = self.preflight(move=18)
        self.assertFalse(ns['prepare_alignment_frame']())
        ns.update(MeasureCreep=False, PauseOnCreep=False)
        self.assertTrue(ns['prepare_alignment_frame']())
        ns['capture_settled_request'].assert_called_once()
        self.assertIs(ns['alignment_request'], requests[0])
        requests[0].release.assert_not_called()

    def test_measurement_off_retains_sprocket_overshoot_confirmation(self):
        ns, _ = self.preflight(move=-18, diagnostic=False)
        ns.update(MeasureCreep=False, PauseOnCreep=False)
        self.assertFalse(ns['prepare_alignment_frame']())
        self.assertFalse(ns['prepare_alignment_frame']())
        ns['pause_alignment_frame'].assert_called_once()
        ns['save_evidence'].assert_not_called()
        self.assertIsNone(ns['alignment_guard'].stabilization_first)

    def test_measurement_toggle_disables_pause_and_stops_active_test_only(self):
        for active in (None, StabilizationTest()):
            ns = scanner_functions('cmd_measure_creep', measure_creep_var=Mock(get=Mock(return_value=False)),
                pause_on_creep_var=Mock(), MeasureCreep=True, PauseOnCreep=True,
                stabilization_test=active, ScanStopRequested=False, debug_menu=Mock(),
                NORMAL='normal', DISABLED='disabled')
            ns['cmd_measure_creep']()
            self.assertFalse(ns['MeasureCreep'])
            self.assertFalse(ns['PauseOnCreep'])
            self.assertFalse(ns['ConfigData']['PauseOnCreep'])
            self.assertFalse(ns['ConfigData']['MeasureCreep'])
            self.assertEqual(ns['ScanStopRequested'], active is not None)
            ns['pause_on_creep_var'].set.assert_called_once_with(False)

    def test_final_held_save_stops_without_advance(self):
        state = base.HeldFrameSaveTests().state()
        test = StabilizationTest(target=1)
        test.records[3] = {}
        state.update(stabilization_test=test)
        names = ('save_alignment_frame', 'stop_completed_stabilization_test')
        ns = scanner_functions(*names, **{k: v for k, v in state.items() if k not in names})
        ns['save_alignment_frame'](True)
        ns['capture'].assert_called_once()
        ns['stop_scan'].assert_called_once()
        ns['send_arduino_command'].assert_not_called()
        ns['win'].after.assert_not_called()

    def test_normal_fiftieth_capture_stops_before_next_frame_command(self):
        test = StabilizationTest(target=50)
        test.records = dict.fromkeys(range(1, 51), {})
        ns = scanner_functions('capture_loop', 'stop_completed_stabilization_test',
            stabilization_test=test, ScanStopRequested=False, ScanOngoing=True,
            alignment_paused=False, FrameDetectMode='PFD', NewFrameAvailable=True,
            RetryingFrame=False, prepare_alignment_frame=Mock(return_value=True),
            frames_to_go_str=Mock(get=Mock(return_value='')), CurrentFrame=49,
            session_frames=49, register_frame=Mock(), capture=Mock(),
            Scanned_Images_number=Mock(), stop_scan=Mock(), send_arduino_command=Mock(), win=Mock())
        ns['capture_loop']()
        self.assertEqual(ns['CurrentFrame'], 50)
        self.assertEqual(ns['session_frames'], 50)
        self.assertEqual(ns['ConfigData']['CurrentFrame'], '50')
        ns['capture'].assert_called_once_with('normal')
        ns['stop_scan'].assert_called_once()
        ns['send_arduino_command'].assert_not_called()
        ns['win'].after.assert_not_called()

    def test_movement_pause_releases_original_request(self):
        ns, requests = self.preflight(move=18)
        self.assertFalse(ns['prepare_alignment_frame']())
        names = ('prepare_alignment_frame', 'check_arrival_feedback', 'record_alignment_arrival',
                 'finish_alignment_diagnostic', 'take_alignment_request', 'stabilization_sample_due',
                 'stabilization_sample', 'finish_stabilization_pair', 'pause_alignment_frame',
                 'release_alignment_request')
        with tempfile.TemporaryDirectory() as directory:
            ns.update(tk=Mock(), StabilizationComparison=Mock(), win=Mock(),
                      FrameFilenamePattern='picture-%05d.%s', FileType='dng',
                      scan_error_log_fullpath=str(Path(directory) / 'errors.log'),
                      can_recover_alignment=Mock(return_value=False), retry_alignment_frame=Mock(),
                      save_alignment_frame_and_continue=Mock(), save_alignment_frame_and_stop=Mock(),
                      stop_alignment_scan=Mock(), LEFT='left', RIGHT='right')
            ns = scanner_functions(*names, **{k: v for k, v in ns.items() if k not in names})
            self.assertFalse(ns['prepare_alignment_frame']())
            self.assertTrue(ns['alignment_paused'])
            self.assertIsNone(ns['alignment_request'])
            for request in requests:
                request.release.assert_called_once()
            ns['StabilizationComparison'].assert_called_once()


if __name__ == '__main__':
    unittest.main()
