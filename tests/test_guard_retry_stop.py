"""Regression tests for partial HDR retries and stopped capture callback chains."""
import unittest
from unittest.mock import Mock

import test_frame_alignment as base
from test_frame_alignment import scanner_functions


class HdrRetryTests(unittest.TestCase):
    def test_partial_bracket_retry_sets_and_settles_first_exposure_both_orders(self):
        for previous_session in (2, 3):
            with self.subTest(previous_session=previous_session):
                state = base.HeldFrameSaveTests().state()
                state.update(session_frames=previous_session, recalculate_hdr_exp_list=False,
                    HdrBracketAuto=False, hdr_exp_list=[10, 20, 40],
                    hdr_rev_exp_list=[40, 20, 10], hdr_num_exposures=3,
                    images_to_merge=[], HdrBracketShift=0, HDR_MAX_EXP=100,
                    FileType='dng', HdrMergeInPlace=False, dry_run_iterations=3,
                    StabilizationDelayValue=0, PreviewModuleValue=100,
                    FrameFilenamePattern='%s.%s', HdrFrameFilenamePattern='%s.%s.%s',
                    hdr_reinit=Mock(), camera=Mock())
                # Compile actual HDR and held-save functions into the same globals.
                for name in ('save_alignment_frame', 'capture_hdr'):
                    state.pop(name, None)
                ns = scanner_functions('save_alignment_frame', 'capture_hdr', **state)
                ns['capture'] = ns['capture_hdr']
                exposure = [10000 if previous_session == 2 else 40000]
                events = []
                fail = [True]
                def controls(values):
                    exposure[0] = values['ExposureTime']
                    events.append(('set', exposure[0]))
                def request():
                    captured = exposure[0]
                    req = Mock()
                    def save(filename):
                        events.append(('save', captured))
                        if captured == 20000 and fail[0]:
                            fail[0] = False
                            raise OSError('partial bracket write failed')
                    req.save_dng.side_effect = save
                    return req
                ns['camera'].set_controls.side_effect = controls
                ns['camera'].capture_image.side_effect = lambda *_: events.append(('settle', exposure[0]))
                ns['camera'].capture_request.side_effect = request
                ns['save_alignment_frame']()
                self.assertEqual(ns['session_frames'], previous_session)
                self.assertEqual(exposure[0], 20000)
                self.assertTrue(ns['recalculate_hdr_exp_list'])
                events.clear()
                ns['save_alignment_frame']()
                expected = [10000, 20000, 40000] if previous_session == 2 else [40000, 20000, 10000]
                self.assertEqual(events[:4], [('set', expected[0]), ('settle', expected[0]),
                                              ('settle', expected[0]), ('save', expected[0])])
                self.assertEqual([value for event, value in events if event == 'save'], expected)
                self.assertEqual(ns['session_frames'], previous_session + 1)
                ns['register_frame'].assert_called_once()
                ns['send_arduino_command'].assert_not_called()


class PausedStopTests(unittest.TestCase):
    def state(self, recovery):
        ns = scanner_functions('start_scan', 'capture_loop', 'arduino_listen_loop',
            SimulatedRun=True, ArduinoTrigger=0, ScanOngoing=True,
            ScanStopRequested=False, alignment_paused=True,
            alignment_recovery=object() if recovery else None,
            alignment_guard=None, FrameDetectMode='PFD', NewFrameAvailable=False,
            ExitingApp=False, win=Mock(), film_type=Mock(get=Mock(return_value='S8')),
            session_frames=0, disk_space_error_to_notify=False, stop_scan=Mock(),
            capture=Mock(), send_arduino_command=Mock())
        def stop():
            ns['ScanOngoing'] = False
            ns['alignment_paused'] = False
        ns['stop_scan'].side_effect = stop
        return ns

    def test_main_and_async_stop_are_serviced_while_guard_or_recovery_paused(self):
        for recovery in (False, True):
            for main_button in (False, True):
                with self.subTest(recovery=recovery, main_button=main_button):
                    ns = self.state(recovery)
                    ns['capture_loop']()  # A pause has ended this callback chain.
                    ns['win'].after.assert_not_called()
                    if main_button:
                        ns['start_scan']()
                    else:
                        ns['ScanStopRequested'] = True
                    ns['arduino_listen_loop']()
                    ns['arduino_listen_loop']()
                    ns['stop_scan'].assert_called_once()
                    self.assertFalse(ns['ScanOngoing'])
                    self.assertFalse(ns['ScanStopRequested'])
                    ns['capture'].assert_not_called()
                    ns['send_arduino_command'].assert_not_called()
                    self.assertTrue(all(call.args[1] is ns['arduino_listen_loop']
                                        for call in ns['win'].after.call_args_list))

    def test_listener_never_starts_capture_chain_on_pause_or_resume(self):
        ns = self.state(True)
        ns['capture_loop'] = Mock()
        ns['arduino_listen_loop']()
        ns['alignment_paused'] = False  # Resume owns scheduling the capture loop.
        ns['arduino_listen_loop']()
        ns['ScanStopRequested'] = True  # Active captures own normal stop handling.
        ns['arduino_listen_loop']()
        ns['capture_loop'].assert_not_called()
        ns['stop_scan'].assert_not_called()
