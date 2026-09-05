# Frame alignment fixes

Branch: `frame-alignment-fixes`, based on the Pi's deployed `651f5d8`.

## Behavior

Fine Tune uses camera RGB measurements regardless of DNG output, `rawpy`, or
“Bad frames”. Feedback runs in capture order before the next transport command;
background saving only reports errors. Failed hole detection contributes no
numeric offset. Fine Tune stays in the Nano's supported 5–95 range, and DNG error
logs name the frame that was actually saved.

The expert controls add **Alignment guard**:

- **Off**: normal scanning and Fine Tune, without the pre-save protection.
- **Pause**: confirm a suspect position with another exposure of the stationary
  frame, then hold it without saving or advancing.
- **Correct** (default): on S8 with supporting Nano firmware, try bounded forward
  nudges and recheck before saving. Without firmware support, use Pause behavior.
  R8 also uses Pause behavior; automatic reversal is never attempted.

Guard tolerance defaults to **3% of image height**, independently of the existing
8% bad-frame reporting tolerance. Set Frame VCenter for the film before scanning.
This verifies sprocket position relative to that calibrated target, not the actual
picture borders: stock with a different picture-to-hole relationship can require
recalibration. The three detection strips must agree on a complete hole (S8) or
gap (R8). Clipped or ambiguous detections pause instead of driving the motor.

A pause dialog offers **Retry this frame** and **Stop scan**. Retry rechecks the
held physical frame without incrementing counters. Stop ends the session while
leaving the frame unsaved: retain it manually before starting a new scan, since
a normal scan start requests a new film frame. Guard events include its intended
filename in the scan error log.

Normal DNG/PNG captures reuse the checked camera request. JPEG uses its RGB image.
HDR and captures requesting exposure adaptation verify position first, then take
their required exposures while the film stays stationary.

## Nano firmware

`ALT-Scann8-Controller.ino` is now version **1.1.12**. The Pico variants are not
changed. The app probes support rather than relying on a version string.

Command 44 with parameter zero queries alignment support. For movement, the high
byte is a token (1–127), and the low byte is forward steps (1–40). Response 91
echoes the packed parameter and actual moved steps; zero moved steps means refusal.
Only a matching acknowledgement completes a pending move. A three-second timeout
pauses without resending an uncertain movement command.

The controller permits nudges only while holding a PT-detected frame, suspends
collection during that hold, and includes corrective travel in the next frame's
minimum-step calculation. Each frame is limited to one third of the minimum
frame travel. Python additionally limits correction to four attempts, confirms
improvement after each nudge, and waits for a fresh settled exposure. The configured
guard tolerance applies both before and after correction; a nudge does not tighten
the acceptance limit.

Build for this scanner's Nano old bootloader:

```sh
mkdir -p /tmp/alignment-nano/ALT-Scann8-Controller
cp ALT-Scann8-Controller.ino /tmp/alignment-nano/ALT-Scann8-Controller/
arduino-cli compile --fqbn arduino:avr:nano:cpu=atmega328old \
  --build-path /tmp/alignment-nano/build \
  --output-dir /tmp/alignment-nano/output \
  /tmp/alignment-nano/ALT-Scann8-Controller
```

This scanner's PCB cannot safely have the external PSU and Nano USB power connected
at the same time. For every Nano firmware update:

1. Stop scanning and disconnect the external PSU.
2. Connect the Nano by USB, then flash and verify the firmware.
3. Disconnect USB before restoring external PSU power.
4. Once the Pi is back online, restart the app and verify controller recognition.

The Pi being unreachable during this procedure is expected; do not require it to
be online for the USB flashing step.

Deploying Python alone enables the fixes and pause protection; automatic nudging
remains unavailable until the Nano update is installed. After restarting the app,
check that the status says automatic correction is available.

## Validation

Run `python -m unittest discover -s tests -v`. Tests cover unknown detections,
DNG reporting, ordered feedback, request ownership, stale exposures, unchanged
counters on rejection, correction bounds, overshoot, missing/stale acknowledgements,
and I2C failures. A C++ harness runs the actual firmware command handler against a
fake transport; the complete sketch is also compiled for the Nano.

A read-only replay of 12 saved `ven-1f` DNGs found valid hole measurements in all
samples. Frames 900, 950, 970 and 980 were within 3%; sampled shifted frames were
7.2–19.4% below the target and would trigger the guard. The Pi also passed a hidden
GUI smoke test with camera and motor access disabled.

Physical nudge behavior still needs a short live trial across a known troublesome
cut after flashing. Compare cropped frames, unnecessary pauses, correction count,
and throughput before using it unattended on a whole reel.
