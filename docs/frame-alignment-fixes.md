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
recalibration. At least two of three detection strips must agree on a complete
hole (S8) or gap (R8). One outlying strip is ignored only when the agreeing pair
is unique. Clipped or ambiguous detections pause instead of driving the motor.

A pause dialog offers **Retry this frame**, **Save and continue**, **Save this frame and stop**, and
**Stop without saving**. Retry rechecks the held physical frame without incrementing
counters. Save explicitly accepts its position, uses the normal scan format and
next frame number (including HDR when enabled), and updates counters once.
Save and continue advances after capture; an I2C send failure retries only the
advance, without saving or counting again. An armed frame-count stop still stops
after the last requested frame. Save this frame and stop keeps the film held.
Continuing also resets the controller watchdog interval, so time spent paused
cannot immediately trigger a synthetic frame event after the next advance.
Normal save workers finish writing queued exposures after stopping.
Stop without saving leaves this frame unsaved; a normal scan start requests a new
film frame. Guard events include its intended filename in the scan error log.

Normal DNG/PNG captures reuse the checked camera request. JPEG uses its RGB image.
HDR and captures requesting exposure adaptation verify position first, then take
their required exposures while the film stays stationary.

### Damaged sprocket fallback

If the usual three strips cannot agree, the detector searches 0.5–6% of the
image width for a clean interior band. Three adjacent, non-overlapping strips
must agree on both edges, within 1.5% of image height. Multiple supported bands
with different edges remain unknown. The damaged sprocket in saved frame 1996
is recovered this way: -36 pixels on a 1520-pixel-high DNG render (-2.37%).

With the guard enabled, an unknown classical result invokes the optional
NCNN YOLO11n fallback. It requires one complete corner pair, confidence at least
0.65 for each corner, plausible geometry, and agreement across two stationary
exposures. Neural measurements do not feed the automatic Fine Tune loop.
One corner, a frame seam, missing runtime/model, an inference error, or a ten-second
timeout cannot authorize movement or saving. Tk stays responsive during inference;
stop/retry discards the result and releases the held camera request.

The exported model and installation instructions are in
[`models/sprocket-ncnn`](../models/sprocket-ncnn/README.md). No accelerator is required.
On this 4 GB Pi 5 while scanning, eight saved-frame previews took 448 ms for the
first inference, then 102–298 ms (median 118 ms) at 640×640 with two CPU threads.
The strip detector took around 1 ms; the adaptive search on frame 1996 took 4.3 ms.
These exclude decoding and camera acquisition. Full live camera buffers cost
additional conversion/copy time, and a YOLO decision needs two exposures.

The model accepted a complete pair on frame 146, agreeing with the classical
center within one pixel. It declined the other seven sample frames, including
1996 where only the top corner was confidently detected. This is a conservative
fallback, not a guarantee that every damaged hole will be detected.

Reproduce offline measurements without opening the camera or moving film:

```sh
.venv/bin/python tools/sprocket_bench.py /path/to/saved/frames/*.png
```

### Live scan observations, 2026-09-05

On `ven-1f3`, frame 146 was rejected twice despite a nearly centered sprocket.
Reprocessing its saved DNG at full resolution reproduced the rejection: strip
centers were 1486.0, 1485.5 and 1378.5 pixels. A dark mark interrupted the third
strip. The unique agreeing-pair fix returns -34.25 pixels (-1.13%) instead of
unknown. These are measurements of the saved DNG render, not the discarded
preflight exposures.

The live log confirms forward corrections on frames 456, 457 and 460: 19, 21 and
18 steps respectively, with residuals 81.5, 95.0 and 64.0 pixels. Fine Tune then
reported its upper limit of 95 with an average offset of 81 pixels. Overshoot
pauses occurred at frame 63 (-6.2%), 389 (-7.5%) and 531 (-8.0%). Increasing guard
tolerance admits these offsets; it does not stabilize the transport.

The cause of that remaining position variation is not established. Candidate
contributors include the interaction of the dynamic PT threshold, learned
minimum-step gate, and delayed camera Fine Tune feedback (five-frame average,
up to ten ratio points per adjustment, two measured frames between adjustments),
along with transport tension/slip. A controlled comparison with fixed Fine Tune
and per-frame offset/threshold/step telemetry is needed to distinguish them.

After restarting at frame 3700 on September 6, 49 of frames 3701–3795 required
forward correction. Frame 3795 needed 28 steps and finished at -3 pixels;
3796 onward initially needed no correction. Fine Tune reached 95 with an average
offset of +402 pixels during the run. Settings were manual minimum 250 steps,
automatic PT level, automatic Fine Tune, and 8% guard tolerance. No YOLO calls
were logged during this run. A separate disagreement pause occurred at frame 3829.

The restart loaded Fine Tune 25 from the session file: automatic changes had
updated the live value but not the configuration. Successful automatic updates
now also update the global and film-specific saved trim values, which are written
by normal session saving. Failed I2C writes leave those values untouched. Trim
changes are logged at INFO to make later oscillations traceable. This fixes stale
trim restoration; it does not establish the cause of every undershoot episode.

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
