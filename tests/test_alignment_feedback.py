import json
import unittest
from unittest.mock import Mock

from alignment_feedback import FineTuner, AlignmentStatistics, format_statistics
from frame_alignment import AlignmentGuard, HoleMeasurement
import test_frame_alignment as base


class TunerTests(unittest.TestCase):
    def test_persistent_bias_moves_one_point_then_waits_and_needs_new_samples(self):
        tuner = FineTuner()
        proposal = None
        for frame in range(1, 14):
            result = tuner.observe(frame, 3, 25, 'settings')
            if result:
                self.assertIsNone(proposal)
                proposal = result
        self.assertEqual(proposal['value'], 26)
        tuner.applied(proposal, True)
        for frame in range(14, 40):
            self.assertIsNone(tuner.observe(frame, 3, 26, 'settings'))
        self.assertEqual(tuner.observe(40, 3, 26, 'settings')['value'], 27)

    def test_alternating_noise_and_small_bias_do_not_change_trim(self):
        for values in ([3, -3] * 50, [.8] * 100, [20, .2, .1, -.1] * 25):
            tuner = FineTuner()
            for frame, offset in enumerate(values, 1):
                self.assertIsNone(tuner.observe(frame, offset, 25, 'settings'))

    def test_reversal_needs_more_evidence(self):
        tuner = FineTuner()
        tuner.context, tuner.value, tuner.last_direction = 'settings', 26, 1
        for frame in range(1, 16):
            self.assertIsNone(tuner.observe(frame, -3, 26, 'settings'))
        self.assertEqual(tuner.observe(16, -3, 26, 'settings')['value'], 25)

    def test_settings_change_and_long_pause_discard_old_evidence(self):
        tuner = FineTuner()
        for frame in range(1, 13):
            tuner.observe(frame, 3, 25, 'settings', now=frame)
        self.assertIsNone(tuner.observe(13, 3, 25, 'settings', now=100))
        self.assertEqual(len(tuner.samples), 1)
        self.assertIsNone(tuner.observe(14, 3, 25, 'new film', now=101))
        self.assertEqual(len(tuner.samples), 0)

    def test_extreme_invalid_and_duplicate_samples_do_not_tune(self):
        tuner = FineTuner()
        tuner.observe(1, 3, 25, 'settings')
        for frame, offset in enumerate((None, float('nan'), 40), 6):
            self.assertIsNone(tuner.observe(frame, offset, 25, 'settings'))
        tuner.observe(9, 3, 25, 'settings')
        for _ in range(20):
            self.assertIsNone(tuner.observe(9, 3, 25, 'settings'))
        self.assertEqual(len(tuner.samples), 1)


class ArrivalTests(unittest.TestCase):
    def preflight(self, measurements, mode='Off', frame=44):
        ns, requests = base.PreflightTests().preflight(measurements, mode)
        ns['CurrentFrame'] = frame
        ns['alignment_statistics'].start(0)
        ns['alignment_statistics'].frame(frame + 1, 1, origin='pt')
        return ns, requests

    def test_guard_off_collects_pair_without_pausing_or_nudging(self):
        ns, requests = self.preflight([HoleMeasurement(20, 1000), HoleMeasurement(22, 1000)])
        self.assertFalse(ns['prepare_alignment_frame']())
        ns['adjust_auto_fine_tune'].assert_called_once()
        self.assertTrue(ns['prepare_alignment_frame']())
        ns['pause_alignment_frame'].assert_not_called()
        ns['send_alignment_nudge'].assert_not_called()
        sample, confirmed, source = ns['adjust_auto_fine_tune'].call_args.args
        self.assertFalse(confirmed)
        self.assertEqual(sample.offset, 20)
        ns['adjust_auto_fine_tune'].assert_called_once()
        self.assertEqual(ns['alignment_statistics'].records[45].diagnostic['delta_px'], 2)
        self.assertTrue(ns['alignment_statistics'].records[45].diagnostic['agrees'])
        requests[1].release.assert_called_once()
        self.assertIs(ns['alignment_request'], requests[0])
        requests[0].release.assert_not_called()

    def test_diagnostic_disagreement_is_logged_without_changing_tuning_or_capture(self):
        ns, _ = self.preflight([HoleMeasurement(20, 1000), HoleMeasurement(200, 1000)])
        ns['prepare_alignment_frame']()
        self.assertTrue(ns['prepare_alignment_frame']())
        ns['pause_alignment_frame'].assert_not_called()
        sample, confirmed, _ = ns['adjust_auto_fine_tune'].call_args.args
        self.assertEqual(sample.offset, 20)
        self.assertFalse(confirmed)
        ns['adjust_auto_fine_tune'].assert_called_once()
        record = ns['alignment_statistics'].records[45]
        self.assertEqual(record.arrival, 'aligned')
        self.assertEqual(record.diagnostic['delta_pct'], 18)
        self.assertFalse(record.diagnostic['agrees'])

    def test_original_offset_is_recorded_once_before_correction_not_after(self):
        ns, _ = self.preflight([HoleMeasurement(200, 1000), HoleMeasurement(202, 1000), HoleMeasurement(10, 1000)], 'Correct')
        ns['alignment_firmware_supported'] = True
        ns['prepare_alignment_frame']()
        ns['prepare_alignment_frame']()
        self.assertEqual(ns['alignment_statistics'].records[45].offset_pct, 20.1)
        self.assertTrue(ns['prepare_alignment_frame']())
        ns['adjust_auto_fine_tune'].assert_called_once()
        self.assertEqual(ns['alignment_statistics'].records[45].offset_pct, 20.1)

    def test_clean_frames_between_samples_add_no_confirmation_capture(self):
        ns, _ = self.preflight([HoleMeasurement(20, 1000)], frame=45)
        self.assertTrue(ns['prepare_alignment_frame']())
        ns['capture_settled_request'].assert_called_once()
        ns['adjust_auto_fine_tune'].assert_called_once()

    def test_diagnostic_does_not_invoke_guard_or_yolo_on_second_exposure(self):
        ns, requests = self.preflight([HoleMeasurement(20, 1000), HoleMeasurement(None, 1000)], 'Correct')
        self.assertFalse(ns['prepare_alignment_frame']())
        self.assertTrue(ns['prepare_alignment_frame']())
        ns['pause_alignment_frame'].assert_not_called()
        ns['send_alignment_nudge'].assert_not_called()
        ns['alignment_yolo_worker'].submit.assert_not_called()
        self.assertIsNone(ns['alignment_statistics'].records[45].diagnostic['agrees'])
        self.assertIs(ns['alignment_request'], requests[0])
        requests[1].release.assert_called_once()

    def test_failed_or_stale_diagnostic_preserves_original_accepted_request(self):
        for failure in ('camera', 'stale'):
            ns, requests = self.preflight([HoleMeasurement(20, 1000)] * 2, 'Correct')
            ns['prepare_alignment_frame']()
            if failure == 'camera':
                ns['capture_settled_request'].side_effect = RuntimeError('Camera test failure')
            else:
                requests[1].get_metadata.return_value['SensorTimestamp'] = 0
            self.assertTrue(ns['prepare_alignment_frame']())
            ns['pause_alignment_frame'].assert_not_called()
            self.assertIs(ns['alignment_request'], requests[0])
            self.assertIsNone(ns['alignment_statistics'].records[45].diagnostic['agrees'])
            requests[0].release.assert_not_called()

    def test_stop_during_diagnostic_releases_held_original(self):
        ns, requests = self.preflight([HoleMeasurement(20, 1000)] * 2, 'Correct')
        ns['prepare_alignment_frame']()
        reset = base.scanner_functions('reset_alignment_guard', 'release_alignment_request',
            alignment_guard=ns['alignment_guard'], alignment_request=ns['alignment_request'],
            alignment_yolo_pending=None, alignment_pause_dialog=None)
        reset['reset_alignment_guard']()
        requests[0].release.assert_called_once()
        self.assertIsNone(reset['alignment_guard'])
        self.assertIsNone(reset['alignment_request'])

    def test_diagnostic_summary_separates_small_large_and_unknown_differences(self):
        stats = AlignmentStatistics()
        stats.start(0)
        for frame, delta in enumerate((.2, 2.5, None), 1):
            record = stats.frame(frame, frame, origin='pt')
            record.diagnostic = dict(delta_pct=delta, agrees=None if delta is None else abs(delta) <= 1.5)
        summary = stats.snapshot(4)['session']
        self.assertEqual(summary['diagnostic_pairs'], 3)
        self.assertEqual(summary['diagnostic_disagreements'], 1)
        self.assertEqual(summary['diagnostic_unknown'], 1)
        self.assertAlmostEqual(summary['diagnostic_median_delta_pct'], 1.35)

    def test_every_frame_feedback_reaches_tuner_without_double_exposure_requirement(self):
        ns = base.FineTuneTests().state()
        for frame in range(1, 14):
            ns['CurrentFrame'] = frame - 1
            ns['alignment_statistics'].frame(frame, frame, origin='pt', settings=dict(
                reported_steps=280, pt_valid=True, steps=250, extra_steps=0, film='S8',
                steps_auto=False, speed=5, vcenter=0, resolution='4056x3040', capstan=14.6))
            ns['adjust_auto_fine_tune'](HoleMeasurement(30, 1000), False, 'strips')
        ns['send_arduino_command'].assert_called_once_with(54, 26)

    def test_retry_does_not_feed_same_frame_again(self):
        ns, _ = self.preflight([HoleMeasurement(200, 1000)] * 4, 'Pause')
        ns['prepare_alignment_frame']()
        ns['prepare_alignment_frame']()
        ns['alignment_guard'] = None
        ns['prepare_alignment_frame']()
        ns['prepare_alignment_frame']()
        ns['adjust_auto_fine_tune'].assert_called_once()

    def test_tuner_eligibility_gate_recovery_and_yolo(self):
        for kind, steps, source in [('pt', 250, 'strips'), ('recovery', 280, 'strips'), ('pt', 280, 'yolo')]:
            ns = base.FineTuneTests().state()
            for frame in range(1, 30):
                ns['CurrentFrame'] = frame - 1
                ns['alignment_statistics'].frame(frame, frame, origin=kind,
                    settings=dict(reported_steps=steps, pt_valid=True, steps=250, extra_steps=0))
                ns['adjust_auto_fine_tune'](HoleMeasurement(30, 1000), True, source)
            ns['send_arduino_command'].assert_not_called()

    def test_availability_does_not_depend_on_guard(self):
        ns = base.scanner_functions('fine_tune_availability', AutoFineTuneEnabled=True,
            AutoPtLevelEnabled=True, FrameDetectMode='PFD', CameraDisabled=False, SimulatedRun=False,
            AlignmentGuardMode='Off')
        self.assertIsNone(ns['fine_tune_availability']())
        ns['AutoPtLevelEnabled'] = False
        self.assertIn('PT Level Auto', ns['fine_tune_availability']())
        ns['AutoPtLevelEnabled'] = True
        ns['FrameDetectMode'] = 'VFD'
        self.assertIn('Visual Detection', ns['fine_tune_availability']())


class StatisticsTests(unittest.TestCase):
    def test_counts_original_arrivals_unknowns_and_overrides_without_retry_duplicates(self):
        stats = AlignmentStatistics()
        stats.start(0)
        for frame, offset in enumerate((1, 20, -10, None), 1):
            stats.frame(frame, frame, origin='pt')
            stats.arrival(frame, frame, offset, 3, frame != 1, 'strips')
            stats.captured(frame, frame + .5)
        corrected = stats.records[2]
        corrected.nudges, corrected.nudge_steps = 2, 59
        stats.records[3].paused = stats.records[3].accepted_as_is = True
        self.assertFalse(stats.arrival(2, 10, 0, 3, True, 'strips'))
        stats.captured(2, 10)
        summary = stats.snapshot(10)['session']
        self.assertEqual(summary['aligned_pct'], 25)
        self.assertEqual((summary['undershoots'], summary['overshoots'], summary['unknown']), (1, 1, 1))
        self.assertEqual(summary['median_offset_pct'], 1)
        self.assertEqual(summary['p95_absolute_pct'], 20)
        self.assertEqual(summary['nudge_steps'], 59)
        self.assertEqual(summary['captured'], 4)
        self.assertEqual(summary['effective_fps'], .4)
        self.assertEqual(summary['accepted_as_is'], 1)

    def test_recovery_is_not_counted_as_a_good_pt_stop_and_pause_time_counts(self):
        stats = AlignmentStatistics()
        stats.start(0)
        record = stats.frame(1, 1, origin='pt')
        stats.arrival(1, 1, -20, 3, True, 'strips')
        record.paused = record.accepted_as_is = True
        stats.captured(1, 2)
        recovered = stats.frame(2, 2, origin='recovery')
        recovered.recovery_started = recovered.recovered = True
        recovered.recovery_steps = 231
        stats.captured(2, 22)
        result = stats.snapshot(25)['session']
        self.assertEqual(result['pt_arrivals'], 1)
        self.assertEqual(result['aligned_pct'], 0)
        self.assertEqual(result['recovered'], 1)
        self.assertEqual(result['effective_fps'], 2 / 25)
        stats.stop(25)
        self.assertEqual(stats.snapshot(1000)['session'], result)

    def test_rolling_window_and_new_session_are_independent(self):
        stats = AlignmentStatistics()
        stats.start(0)
        for frame in range(1, 151):
            stats.frame(frame, frame - .5, origin='pt')
            stats.arrival(frame, frame, 20 if frame <= 50 else 1, 3, True, 'strips')
            stats.captured(frame, frame)
        snapshot = stats.snapshot(160)
        self.assertEqual(snapshot['recent']['aligned_pct'], 100)
        self.assertAlmostEqual(snapshot['session']['aligned_pct'], 100 * 100 / 150)
        self.assertAlmostEqual(snapshot['recent']['effective_fps'], 100 / 110)
        self.assertIn('Last 100', format_statistics(snapshot))
        stats.start(200)
        self.assertEqual(stats.snapshot(201)['session']['captured'], 0)

    def test_ui_and_periodic_log_share_snapshot_and_final_log(self):
        stats = AlignmentStatistics()
        stats.start(10)
        ns = base.scanner_functions('refresh_alignment_statistics', alignment_statistics=stats,
            alignment_stats_last_log=10, alignment_stats_logged_captures=0,
            alignment_stats_button_var=Mock(), alignment_stats_window=Mock(),
            AlignmentGuardMode='Correct', AlignmentGuardTolerance=3,
            AutoPtLevelEnabled=True, FrameFineTuneValue=25, StepsPerFrame=250, AutoFrameStepsEnabled=False,
            alignment_settings=Mock(return_value={'fine_tune': 25}),
            logging=Mock(), time=Mock(monotonic=Mock(return_value=39)))
        ns['refresh_alignment_statistics']()
        ns['logging'].info.assert_not_called()
        ns['time'].monotonic.return_value = 40
        ns['refresh_alignment_statistics']()
        logged = json.loads(ns['logging'].info.call_args.args[1])
        self.assertEqual(logged['session']['elapsed_s'], 30)
        shown = ns['alignment_stats_window'].update_snapshot.call_args.args[0]
        self.assertEqual(logged['recent'], shown['recent'])
        self.assertEqual(logged['session'], shown['session'])
        stats.stop(42)
        ns['refresh_alignment_statistics'](True, 'stop')
        self.assertEqual(json.loads(ns['logging'].info.call_args.args[1])['reason'], 'stop')


if __name__ == '__main__':
    unittest.main()
