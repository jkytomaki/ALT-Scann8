"""Optional capture markers must leave capture ownership and firmware timers alone."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from test_frame_alignment import scanner_functions


class CaptureBeepTests(unittest.TestCase):
    def state(self, *names):
        return scanner_functions(*names, capture_beep_enabled=True, capture_beep_supported=True,
            capture_beep_token=32767, SimulatedRun=False, CameraDisabled=False,
            CMD_CAPTURE_BEEP=45, CurrentFrame=1314, send_arduino_command=Mock(return_value=True))

    def test_disabled_or_unsupported_does_not_touch_camera_or_transport(self):
        for enabled, supported in ((False, True), (True, False)):
            ns = self.state('mark_camera_capture')
            ns.update(capture_beep_enabled=enabled, capture_beep_supported=supported)
            request = Mock()
            ns['mark_camera_capture'](request)
            request.get_metadata.assert_not_called()
            ns['send_arduino_command'].assert_not_called()

    def test_failed_send_is_not_retried_and_token_wraps(self):
        ns = self.state('mark_camera_capture')
        ns['send_arduino_command'].return_value = False
        request = Mock(get_metadata=Mock(return_value=dict(SensorTimestamp=1, ExposureTime=8981)))
        ns['mark_camera_capture'](request)
        ns['send_arduino_command'].assert_called_once_with(45, 1)
        request.release.assert_not_called()

    def test_only_selected_settled_request_beeps_and_reuse_does_not_repeat_it(self):
        ns = self.state('mark_camera_capture', 'capture_settled_request', 'take_alignment_request')
        first, second = Mock(), Mock()
        first.get_metadata.return_value = dict(SensorTimestamp=8_900_000_000, ExposureTime=8981, FrameDuration=100000)
        second.get_metadata.return_value = dict(SensorTimestamp=9_500_000_000, ExposureTime=8981, FrameDuration=100000)
        ns.update(camera=Mock(capture_request=Mock(side_effect=[first, second])), CaptureSettleDeadline=9,
                  time=Mock(clock_gettime=Mock(return_value=9.1), clock_gettime_ns=Mock(return_value=9_600_000_000)))
        self.assertIs(ns['capture_settled_request'](), second)
        first.release.assert_called_once()
        second.release.assert_not_called()
        ns['alignment_request'] = second
        self.assertIs(ns['take_alignment_request'](), second)
        ns['send_arduino_command'].assert_called_once_with(45, 1)

    def test_image_path_releases_request_even_when_conversion_fails(self):
        ns = self.state('capture_marked_image', 'mark_camera_capture')
        request = Mock(get_metadata=Mock(return_value={}))
        request.make_image.side_effect = RuntimeError('conversion failed')
        ns['camera'] = Mock(capture_request=Mock(return_value=request))
        with self.assertRaises(RuntimeError):
            ns['capture_marked_image']()
        request.release.assert_called_once()

    @unittest.skipUnless(shutil.which('g++'), 'g++ required for firmware harness')
    def test_firmware_fixed_pulse_silent_probe_and_clock_wrap(self):
        source = (Path(__file__).resolve().parents[1] / 'ALT-Scann8-Controller.ino').read_text()
        functions = source.split('void ServiceCaptureBeep()', 1)[1].split('void setup()', 1)[0]
        harness = '''
#include <cassert>
#include <climits>
const int A2=16, LOW=0, HIGH=1, RSP_CAPTURE_BEEP=92;
bool CaptureBeepActive=false, CaptureBeepLevel=false;
unsigned long CaptureBeepStarted=0, clock_value=0;
int writes=0, last_level=0, response_token=-1, response_value=-1;
unsigned long micros() { return clock_value; }
void digitalWrite(int pin, int level) { assert(pin==A2); ++writes; last_level=level; }
void SendToRPi(int response, int token, int value) {
    assert(response==92); response_token=token; response_value=value;
}
void ServiceCaptureBeep()''' + functions + '''
int main() {
    CaptureBeepCommand(0); assert(writes==0 && !CaptureBeepActive && response_value==1);
    CaptureBeepCommand(-1); assert(writes==0);
    CaptureBeepCommand(32767); assert(CaptureBeepActive && last_level==HIGH && response_value==10);
    clock_value=250; ServiceCaptureBeep(); assert(last_level==LOW && CaptureBeepActive);
    clock_value=500; ServiceCaptureBeep(); assert(last_level==HIGH);
    clock_value=9999; ServiceCaptureBeep(); assert(CaptureBeepActive);
    clock_value=10000; ServiceCaptureBeep(); assert(!CaptureBeepActive && last_level==LOW);
    clock_value=ULONG_MAX-1000; CaptureBeepCommand(1);
    clock_value=1200; ServiceCaptureBeep(); assert(CaptureBeepActive);
    clock_value=11000; ServiceCaptureBeep(); assert(!CaptureBeepActive && last_level==LOW);
}
'''
        with tempfile.TemporaryDirectory() as folder:
            cpp, exe = Path(folder) / 'beep.cpp', Path(folder) / 'beep-test'
            cpp.write_text(harness)
            subprocess.run(['g++', '-std=c++11', '-Wall', '-Wextra', '-Werror', str(cpp), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)
