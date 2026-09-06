"""Forward seek traces and scanner integration, without camera or motor access."""
import time
import unittest
from unittest.mock import Mock

import numpy as np

from frame_alignment import ForwardRecovery, HoleMeasurement, measure_hole
import test_frame_alignment as base
from test_frame_alignment import scanner_functions


def confirmed(recovery, offset):
    measurement = HoleMeasurement(offset, 1000)
    assert recovery.inspect(measurement) == 'confirm'
    return recovery.inspect(measurement)


def film_image(start_offset, steps, pitch_pixels=800):
    image = np.zeros((1000, 200, 3), np.uint8)
    for number in range(-1, 3):
        center = 500 + start_offset + pitch_pixels * number - steps * pitch_pixels / 280
        a, b = max(0, round(center - 115)), min(1000, round(center + 115))
        if a < b:
            image[a:b, 1:14] = 255
    return image


class RecoveryTraceTests(unittest.TestCase):
    def test_vcenter_shifts_target_without_shifting_physical_edge_transition(self):
        for shift in (-100, 100):
            recovery = ForwardRecovery(280)
            for _ in range(160):
                measurement = measure_hole(film_image(-200, recovery.total_steps), 'S8', shift)
                decision = recovery.inspect(measurement, target_shift=shift)
                if decision == 'move':
                    recovery.moved(recovery.next_steps)
                elif decision in ('accept', 'pause'):
                    break
            self.assertEqual(decision, 'accept', recovery.reason)
            self.assertLessEqual(abs(-200 + 800 - recovery.total_steps * 800 / 280 - shift), 31)

    def test_rendered_film_recovers_next_hole_from_several_overshoots(self):
        for initial in (-90, -160, -250, -330):
            for pitch_pixels in (750, 800, 950):
                with self.subTest(initial=initial, pitch_pixels=pitch_pixels):
                    recovery = ForwardRecovery(280, 8)
                    decisions = []
                    for _ in range(160):
                        measurement = measure_hole(film_image(initial, recovery.total_steps, pitch_pixels), 'S8')
                        decision = recovery.inspect(measurement)
                        decisions.append(decision)
                        if decision == 'move':
                            self.assertLessEqual(recovery.next_steps, 8)
                            recovery.moved(recovery.next_steps)
                        elif decision in ('accept', 'pause'):
                            break
                    self.assertEqual(decision, 'accept', recovery.reason)
                    self.assertEqual(recovery.phase, 'next')
                    # Check physical frame identity independently of the detector.
                    next_offset = initial + pitch_pixels - recovery.total_steps * pitch_pixels / 280
                    self.assertLessEqual(abs(next_offset), 31)
                    self.assertLessEqual(recovery.total_steps, 308)
                    self.assertEqual(decisions.count('accept'), 1)

    def test_unknown_or_centered_start_never_moves(self):
        for offset in (None, 0, 100):
            recovery = ForwardRecovery(280)
            self.assertEqual(confirmed(recovery, offset), 'pause')
            self.assertEqual(recovery.total_steps, 0)

    def test_stationary_disagreement_never_moves(self):
        recovery = ForwardRecovery(280)
        self.assertEqual(recovery.inspect(HoleMeasurement(-200, 1000)), 'confirm')
        self.assertEqual(recovery.inspect(HoleMeasurement(-150, 1000)), 'pause')

    def test_jam_or_wrong_direction_stops_after_one_command(self):
        for after in (-200, -180):
            recovery = ForwardRecovery(280)
            self.assertEqual(confirmed(recovery, -200), 'move')
            recovery.moved(8)
            self.assertEqual(confirmed(recovery, after), 'pause')
            self.assertEqual(recovery.total_steps, 8)

    def test_missing_detection_only_allows_bounded_edge_crossing(self):
        for initial in (-100, -350):
            recovery = ForwardRecovery(280)
            self.assertEqual(confirmed(recovery, initial), 'move')
            recovery.moved(8)
            while confirmed(recovery, None) == 'move':
                recovery.moved(recovery.next_steps)
            self.assertLessEqual(recovery.total_steps, 160 if initial == -350 else 8)
            self.assertEqual(recovery.phase, 'old')

    def test_unexpected_hole_switch_and_second_overshoot_stop(self):
        recovery = ForwardRecovery(280)
        confirmed(recovery, -100)
        recovery.moved(8)
        self.assertEqual(confirmed(recovery, 300), 'pause')
        recovery = ForwardRecovery(280)
        confirmed(recovery, -350)
        recovery.moved(8)
        self.assertEqual(confirmed(recovery, 350), 'move')
        while recovery.last_offset > 0.035:
            recovery.moved(recovery.next_steps)
            self.assertEqual(confirmed(recovery, recovery.last_offset * 1000 - 35), 'move')
        recovery.moved(recovery.next_steps)
        self.assertEqual(confirmed(recovery, -100), 'pause')
        self.assertNotEqual(recovery.phase, 'old')

    def test_wrong_ack_does_not_account_for_travel(self):
        recovery = ForwardRecovery(280)
        confirmed(recovery, -200)
        with self.assertRaises(ValueError):
            recovery.moved(7)
        self.assertEqual(recovery.total_steps, 0)


class RecoveryIntegrationTests(unittest.TestCase):
    def state(self):
        clock = Mock()
        clock.monotonic.return_value = 10
        clock.clock_gettime.return_value = 10
        clock.time.return_value = 10
        clock.CLOCK_BOOTTIME = time.CLOCK_BOOTTIME
        requests = []
        def capture_request():
            request = Mock()
            request.get_metadata.return_value = {'SensorTimestamp': 11000000000, 'ExposureTime': 1000}
            request.make_array.return_value = np.zeros((1000, 100, 3), np.uint8)
            requests.append(request)
            return request
        ns = scanner_functions('prepare_recovery_frame', 'send_recovery_command', 'receive_recovery_response',
            alignment_recovery=ForwardRecovery(280), recovery_phase='enter', recovery_pending=(401, 13),
            recovery_result=None, recovery_deadline=100, alignment_request=None,
            ScanOngoing=True, ScanStopRequested=False,
            time=clock, CurrentFrame=42, StabilizationDelayValue=250, FrameVCenterImageShift=0,
            PreviewHeight=500, max_inactivity_delay=12, CaptureSettleDeadline=10,
            pause_alignment_recovery=Mock(), set_alignment_status=Mock(),
            send_arduino_command=Mock(return_value=True), CMD_ADVANCE_FRAME_FRACTION=42,
            capture_settled_request=Mock(side_effect=capture_request),
            measure_hole=Mock(return_value=HoleMeasurement(-200, 1000)))
        return ns, requests

    def test_waits_for_entry_ack_then_two_fresh_exposures_before_movement(self):
        ns, requests = self.state()
        self.assertFalse(ns['prepare_recovery_frame']())
        ns['capture_settled_request'].assert_not_called()
        ns['receive_recovery_response'](401, 1)
        self.assertFalse(ns['prepare_recovery_frame']())
        ns['send_arduino_command'].assert_not_called()
        self.assertFalse(ns['prepare_recovery_frame']())
        ns['send_arduino_command'].assert_called_once_with(42, 8)
        self.assertEqual(ns['alignment_recovery'].total_steps, 0)
        for request in requests:
            request.release.assert_called_once()
        ns['receive_recovery_response'](7, 1)
        self.assertIsNone(ns['recovery_result'])
        ns['receive_recovery_response'](8, 1)
        ns['measure_hole'].return_value = HoleMeasurement(-225, 1000)
        ns['prepare_recovery_frame']()
        self.assertEqual(ns['alignment_recovery'].total_steps, 8)

    def test_timeout_or_refusal_never_resends_or_captures(self):
        for result in (None, 0):
            ns, _ = self.state()
            ns['recovery_result'] = result
            ns['time'].monotonic.return_value = 14
            self.assertFalse(ns['prepare_recovery_frame']())
            ns['pause_alignment_recovery'].assert_called_once()
            ns['send_arduino_command'].assert_not_called()
            ns['capture_settled_request'].assert_not_called()

    def test_failed_send_is_not_retried(self):
        ns, _ = self.state()
        ns['send_arduino_command'].return_value = False
        self.assertFalse(ns['send_recovery_command'](8))
        self.assertIsNone(ns['recovery_pending'])
        ns['pause_alignment_recovery'].assert_called_once()
        ns['send_arduino_command'].assert_called_once_with(42, 8)

    def test_rejects_exposure_from_before_settling(self):
        ns, requests = self.state()
        ns['recovery_result'] = 1
        ns['time'].clock_gettime.return_value = 12
        self.assertFalse(ns['prepare_recovery_frame']())
        ns['pause_alignment_recovery'].assert_called_once()
        ns['measure_hole'].assert_not_called()
        requests[0].release.assert_called_once()

    def test_preserves_checked_request_until_exit_is_acknowledged(self):
        ns, requests = self.state()
        ns['recovery_result'] = 1
        ns['alignment_recovery'].inspect = Mock(return_value='accept')
        self.assertFalse(ns['prepare_recovery_frame']())
        ns['send_arduino_command'].assert_called_once_with(42, 402)
        self.assertIs(ns['alignment_request'], requests[0])
        requests[0].release.assert_not_called()
        self.assertFalse(ns['prepare_recovery_frame']())
        ns['receive_recovery_response'](402, 1)
        self.assertTrue(ns['prepare_recovery_frame']())
        self.assertIsNone(ns['alignment_recovery'])
        self.assertIs(ns['alignment_request'], requests[0])
        self.assertEqual(ns['CurrentFrame'], 42)
        self.assertEqual(ns['last_frame_time'], 20)

    def test_save_partial_once_before_starting_seek(self):
        ns = base.HeldFrameSaveTests().state()
        events = []
        ns['capture'].side_effect = lambda _: events.append(('capture', ns['CurrentFrame']))
        ns['reset_alignment_guard'].side_effect = lambda: ns.update(alignment_paused=False)
        ns['start_alignment_recovery'] = Mock(side_effect=lambda: events.append(('seek', ns['CurrentFrame'])))
        ns['save_alignment_frame'](True, recover=True)
        ns['save_alignment_frame'](True, recover=True)
        self.assertEqual(events, [('capture', 3), ('seek', 3)])
        self.assertEqual(ns['FramesToGo'], 7)
        self.assertFalse(ns['RetryingFrame'])
        ns['send_arduino_command'].assert_not_called()

    def test_capture_loop_waits_then_saves_recovered_frame_once_and_retries_only_advance(self):
        state = base.HeldFrameSaveTests().state()
        state.pop('capture_loop')
        state.update(alignment_paused=False, alignment_recovery=ForwardRecovery(280),
                     FrameDetectMode='PFD', SimulatedRun=False, CMD_GET_NEXT_FRAME=12,
                     prepare_recovery_frame=Mock(return_value=False), prepare_alignment_frame=Mock(),
                     FramesPerMinute=0, frames_to_go_key_press_time=0,
                     scan_error_counter=0, scan_error_total_frames_counter=10, scan_error_counter_value=Mock())
        ns = scanner_functions('capture_loop', **state)
        ns['capture_loop']()
        ns['capture'].assert_not_called()
        ns['send_arduino_command'].assert_not_called()
        self.assertEqual(ns['CurrentFrame'], 2)
        def ready():
            ns['alignment_recovery'] = None
            return True
        ns['prepare_recovery_frame'].side_effect = ready
        ns['send_arduino_command'].return_value = False
        ns['capture_loop']()
        ns['capture_loop']()
        self.assertEqual(ns['CurrentFrame'], 3)
        self.assertEqual(ns['session_frames'], 3)
        self.assertEqual(ns['FramesToGo'], 7)
        self.assertEqual(ns['scan_error_total_frames_counter'], 11)
        ns['capture'].assert_called_once_with('normal')
        ns['prepare_alignment_frame'].assert_not_called()
        self.assertEqual(ns['send_arduino_command'].call_count, 2)
        ns['send_arduino_command'].assert_called_with(12)

    def test_stop_requested_during_camera_check_prevents_next_move(self):
        ns, requests = self.state()
        ns['recovery_result'] = 1
        def stop_during_check(*_):
            ns['ScanStopRequested'] = True
            return HoleMeasurement(-200, 1000)
        ns['measure_hole'].side_effect = stop_during_check
        self.assertFalse(ns['prepare_recovery_frame']())
        ns['send_arduino_command'].assert_not_called()
        requests[0].release.assert_called_once()

    def test_stop_cleanup_discards_request_and_late_ack(self):
        state, _ = self.state()
        state.update(alignment_request=Mock(), alignment_yolo_pending=None,
                     alignment_pause_dialog=Mock())
        ns = scanner_functions('reset_alignment_guard', 'release_alignment_request', **state)
        request = ns['alignment_request']
        ns['reset_alignment_guard']()
        request.release.assert_called_once()
        self.assertIsNone(ns['alignment_recovery'])
        self.assertIsNone(ns['recovery_pending'])
        self.assertIsNone(ns['alignment_request'])

    def test_watchdog_does_not_fabricate_frame_during_recovery(self):
        ns = scanner_functions('arduino_listen_loop', SimulatedRun=True, ArduinoTrigger=0,
            ScanOngoing=True, FrameDetectMode='PFD', alignment_guard=None,
            alignment_recovery=ForwardRecovery(280), alignment_paused=False,
            last_frame_time=0, NewFrameAvailable=False, ExitingApp=False, win=Mock())
        ns['arduino_listen_loop']()
        self.assertFalse(ns['NewFrameAvailable'])
        self.assertEqual(ns['last_frame_time'], 0)

    def test_capability_gates_recovery_and_old_firmware_probe_stays_compatible(self):
        ns = scanner_functions('receive_alignment_response', 'can_recover_alignment', Controller_Id=1,
            send_arduino_command=Mock(return_value=True), CMD_ALIGN_FRAME=44,
            set_alignment_status=Mock(), FrameDetectMode='PFD', AlignmentGuardTolerance=8,
            alignment_last_measurement=HoleMeasurement(-160, 1000))
        ns['receive_alignment_response'](0, 1)
        self.assertTrue(ns['alignment_firmware_supported'])
        self.assertFalse(ns['can_recover_alignment']())
        ns['send_arduino_command'].assert_called_once_with(44, 32767)
        ns['receive_alignment_response'](32767, 0)  # Old firmware refuses the capability probe.
        self.assertFalse(ns['can_recover_alignment']())
        ns['receive_alignment_response'](32767, 2)
        self.assertTrue(ns['can_recover_alignment']())
        ns['FilmType'] = 'R8'
        self.assertFalse(ns['can_recover_alignment']())


if __name__ == '__main__':
    unittest.main()
