# Stabilization checks

Normal PT scanning compares two stationary exposures every fifth frame,
independent of Auto Fine Tune and alignment guard mode. Alignment confirmations
also retain and compare their two exposures, including frames that are not on
the five-frame interval. Each exposure must start after its settle deadline.

Two independent controls are available in the **Debug** menu:

- **Measure creep**: turn off to remove the periodic second exposure, picture
  comparison and creep evidence writing. This also turns off and disables
  **Pause on creep**. Normal captures and sprocket alignment/overshoot checks
  remain active. Turning measurement back on leaves pausing off until selected.
- **Pause on creep**: turn off to keep measurements and evidence without
  pausing for periodic creep. The alignment guard still protects positions
  outside its tolerance, including movement past the target during a pair.

Both default to on and are included in saved settings. A stabilization test
enables both; turning measurement off during a test requests a normal safe stop.

The picture comparison uses six regions away from the sprocket and image
borders. At least three textured regions spanning rows and columns must agree
on translation, with a strong correlation after alignment. Sprocket coordinates
are measured separately. Blank images, conflicting matches, and missing or
non-increasing timestamps are inconclusive, not evidence of stability.

The initial movement threshold is **0.25% of capture height**, with a minimum
of 2 pixels: 7.6 pixels at 3040 pixels high. This is independent of the framing
tolerance and is an initial diagnostic setting, not a hardware-validated limit.
The comparison reports movement relative to the camera; it does not measure
motor-shaft motion. Negative vertical displacement means upward in the image.

Confident movement above the threshold pauses **before normal capture or any
further movement**. The popup shows both images, matching crops, and optional
blink/overlay views. It reports picture displacement, sprocket-coordinate change,
exposure separation, delay, and the evidence path. Recheck checks the held film;
save actions use the normal held-frame capture flow. The existing alignment
guard may also pause independently. Inconclusive or detector-only disagreements
are saved/logged but do not themselves assert movement or trigger its pause.

## Evidence location

All image evidence is written directly below the selected scan output folder:

```text
<output>/stabilization/<run-id>/frame-00236-<unique-id>/
    first.png
    second.png
    measurement.json
```

These are full-resolution, lossless RGB main-stream images from the exact
requests checked, not raw DNGs or screenshots. JSON includes exposure timestamps,
exposure/gain metadata, per-strip thresholds and edges, picture matches, and scan
settings. Evidence is saved for movement, inconclusive comparisons, detector
disagreements, and alignment confirmation pairs. Routine stable periodic pairs
are logged without retaining images. No image spool is created on the Pi SD
card. Each recheck gets a unique directory and cannot overwrite the original.
An evidence-write failure holds the film and reports the error.

## Test stabilization

With scanning stopped, select **Test stabilization (50 frames)** next to the
delay setting. It starts a normal PT scan in the chosen output folder, checking
every frame and saving accepted frames normally. It stops after the fiftieth
checked frame is captured, without requesting a fifty-first advance. Film end,
the normal frame counter, Stop, or errors can end it earlier.
Frames reached through explicit forward recovery are counted as inconclusive
for this test because they have already had additional settling time.

The comparison popup allows a new delay to be applied for subsequent advances.
Rechecking already-held film does not validate that delay and never replaces
the original arrival result or increases the number of test frames.

The final report shows the number checked, significant movement events,
detector disagreements, inconclusive checks, largest confident displacement,
and delays tested. It is also saved as `<output>/stabilization/<run-id>/summary.json`.
A clean sample is not a guarantee that all frames have settled: this detects
movement between exposures and cannot prove the absence of blur within one
exposure. Normal five-frame sampling does not check the intervening frames.
