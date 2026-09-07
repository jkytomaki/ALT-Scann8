# Capture beep diagnostic

Enable **Debug → Capture beep (10 ms)** after installing custom Nano firmware
1.1.17. It starts disabled each time the application opens. The app probes for
support silently; older firmware cannot enable the option.

A fixed 10 ms, approximately 2 kHz pip marks delivery of each exposure used for
scanning, alignment/stabilization checks, recovery checks, snapshots and HDR
brackets. Reusing an alignment-checked exposure for saving does not beep twice.
Live preview and discarded exposure-settling frames do not beep.

The sensor runs continuously. The beep marks software delivery, **not the
physical shutter opening**. `Capture beep` log entries record the request's
SensorTimestamp, ExposureTime, host command time and delivery lag. A matching
token in the firmware acknowledgement confirms the buzzer command was handled.
I2C and processing add latency; these are useful video markers, not a precise
hardware exposure synchronization signal. Failed commands are not retried.

The diagnostic drives buzzer pin A2 from the main loop without `tone()`, delays,
or changing the UV PWM timer. Existing capture hold and transport commands are
unchanged. Instrumentation still adds a little software/I2C work, so disable it
after recording the diagnostic video.
