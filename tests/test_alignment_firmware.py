"""Run the actual Nano alignment command handler against a fake transport."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class FirmwareTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('g++'), 'g++ is required for the firmware harness')
    def test_recovery_reuses_manual_moves_and_holds_collection_until_exit(self):
        source = (Path(__file__).resolve().parents[1] / 'ALT-Scann8-Controller.ino').read_text()
        handler = source.split('                    case CMD_ADVANCE_FRAME_FRACTION:', 1)[1].split(
            '                    case CMD_RUN_FILM_COLLECTION:', 1)[0]
        stop = source.split('            case CMD_STOP_SCAN:', 1)[1].split('            case CMD_ALIGN_FRAME:', 1)[0]
        harness = '''
#include <cassert>
const int HIGH=1, LOW=0, MotorB_Direction=2, RSP_ADVANCE_FRAME_FRACTION=90, Sts_Idle=0;
int FrameStepsDone=40, RecoveryTravel=0, MinFrameStepsS8=280, moves=0;
int response_id=0, response_parameter=0, response_status=0, ScanState=0, LastFrameSteps=0;
bool IsS8=true, CaptureInProgress=true, scan_process_ongoing=true, VFD_mode_active=false;
bool RecoveryActive=false, FrameDetected=false, UVLedOn=true;
void SetReelsAsNeutral(int, int, int) {}
void digitalWrite(int, int) {}
void analogWrite(int, int) {}
void capstan_advance(int steps) { moves += steps; }
void SendToRPi(int id,int parameter,int status) { response_id=id; response_parameter=parameter; response_status=status; }
void DebugPrint(const char*,int) {}
void DebugPrintStr(const char*) {}
void command(int param) { switch(0) { case 0:
''' + handler + '''
} }
void stop() { switch(0) { case 0:
''' + stop + '''
} }
int main() {
    command(401); assert(RecoveryActive && CaptureInProgress && !VFD_mode_active && moves==0);
    assert(FrameStepsDone==0 && response_parameter==401 && response_status==1);
    command(401); assert(RecoveryActive && response_status==0); // Never restart a live seek.
    command(8); assert(moves==8 && RecoveryTravel==8 && CaptureInProgress && FrameStepsDone==0);
    assert(response_id==90 && response_parameter==8 && response_status==1);
    command(9); assert(moves==8 && response_status==0);
    for(int i=0;i<42;i++) command(8);
    assert(moves==344 && RecoveryTravel==344);
    command(8); assert(moves==344 && response_status==0);
    command(6); assert(moves==350 && response_status==1);
    command(1); assert(moves==350 && response_status==0);
    FrameStepsDone=99;
    command(402); assert(!RecoveryActive && CaptureInProgress && FrameStepsDone==0 && moves==350);
    assert(response_parameter==402 && response_status==1);
    command(402); assert(response_status==0);
    CaptureInProgress=false; command(401); assert(!RecoveryActive && response_status==0);
    CaptureInProgress=true; IsS8=false; command(401); assert(!RecoveryActive && response_status==0);
    IsS8=true; VFD_mode_active=true; command(401); assert(!RecoveryActive && response_status==0);
    VFD_mode_active=false; scan_process_ongoing=false; command(401); assert(!RecoveryActive && response_status==0);
    scan_process_ongoing=true; command(401); command(8);
    stop(); assert(!RecoveryActive && RecoveryTravel==0 && !scan_process_ongoing && !UVLedOn);
    command(400); assert(moves==758); // Legacy manual 1..400-step path still works after Stop.
}
'''
        with tempfile.TemporaryDirectory() as folder:
            cpp = Path(folder) / 'recovery.cpp'
            exe = Path(folder) / 'recovery-test'
            cpp.write_text(harness)
            subprocess.run(['g++', '-std=c++11', '-Wall', '-Wextra', '-Werror', str(cpp), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)

    @unittest.skipUnless(shutil.which('g++'), 'g++ is required for the firmware harness')
    def test_alignment_command_bounds_and_travel_accounting(self):
        source = (Path(__file__).resolve().parents[1] / 'ALT-Scann8-Controller.ino').read_text()
        handler = source.split('            case CMD_ALIGN_FRAME:', 1)[1].split('            case CMD_SET_AUTO_STOP:', 1)[0]
        harness = '''
#include <cassert>
const int Sts_Idle = 0, HIGH = 1, LOW = 0, MotorB_Direction = 2, RSP_ALIGN_FRAME = 91;
int ScanState = Sts_Idle, FrameStepsDone = 0, MinFrameSteps = 280, moves = 0;
int response_id = 0, response_token = 0, response_steps = 0;
bool CaptureInProgress = true, scan_process_ongoing = true, VFD_mode_active = false, RecoveryActive = false;
void SetReelsAsNeutral(int, int, int) {}
void digitalWrite(int, int) {}
void capstan_advance(int steps) { moves += steps; }
void SendToRPi(int id, int token, int steps) { response_id=id; response_token=token; response_steps=steps; }
void command(int param) { switch(0) { case 0:
''' + handler + '''
} }
int main() {
    command(0); assert(moves == 0 && response_steps == 1 && response_token == 0);
    command(256 + 40);
    assert(moves == 40 && FrameStepsDone == 40 && response_steps == 40 && response_token == 296);
    command(512 + 40);
    assert(moves == 80 && FrameStepsDone == 80 && response_steps == 40);
    command(768 + 40); // Cumulative travel would exceed a third of a frame.
    assert(moves == 80 && FrameStepsDone == 80 && response_steps == 0);
    FrameStepsDone = 0; CaptureInProgress = false;
    command(1024 + 20); assert(moves == 80 && response_steps == 0);
    CaptureInProgress = true; ScanState = 1;
    command(1280 + 20); assert(moves == 80 && response_steps == 0);
    ScanState = Sts_Idle; VFD_mode_active = true;
    command(1536 + 20); assert(moves == 80 && response_steps == 0);
    VFD_mode_active = false; scan_process_ongoing = false;
    command(1792 + 20); assert(moves == 80 && response_steps == 0);
    scan_process_ongoing = true;
    command(2048 + 41); assert(moves == 80 && response_steps == 0);
    command(20); assert(moves == 80 && response_steps == 0); // Missing token.
    command(-1); assert(moves == 80 && response_steps == 0);
    command(2304 + 13);
    assert(moves == 93 && FrameStepsDone == 13 && response_steps == 13 && response_token == 2317);
}
'''
        with tempfile.TemporaryDirectory() as folder:
            cpp = Path(folder) / 'alignment.cpp'
            exe = Path(folder) / 'alignment-test'
            cpp.write_text(harness)
            subprocess.run(['g++', '-std=c++11', '-Wall', '-Wextra', '-Werror', str(cpp), '-o', str(exe)], check=True)
            subprocess.run([str(exe)], check=True)


if __name__ == '__main__':
    unittest.main()
