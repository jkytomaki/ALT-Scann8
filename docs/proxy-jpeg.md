# Optional DNG proxy JPEGs

In **Settings**, enable **Save proxy JPEGs with DNG** and accept the settings.
The option defaults to off and is saved with the session. It applies only when
the capture file type is DNG.

Each saved DNG gets a JPEG in a `proxies` subfolder beside the original:

```text
reel/picture-00001.dng
reel/proxies/picture-00001.jpg

reel/picture-00001.2.dng
reel/proxies/picture-00001.2.jpg
```

The complete basename and numbering come from the DNG filename, including HDR
exposure suffixes. Proxies are 1366 × 1024 pixels, JPEG quality 90. Existing frames
are not backfilled when enabling the option.

The image comes from the processed `main` stream of the same completed camera
request used for the DNG. There is no extra exposure or DNG decoding. Proxies
contain the camera's processed colour/exposure rendering, without UI overlays;
they are not a replacement for developing the RAW files.

Camera configuration, sensor mode, RAW format and capture resolution are unchanged.
With capture set to 4056 × 3040, RAW remains 4056 × 3040. Only the detached processed
image is resized, after the camera request has been released.

A single background worker encodes and writes proxies. Its backlog is bounded to
three images including the one being written; if it cannot keep up, saving waits
for space rather than dropping proxies or growing memory without limit. The
diagnostic `-t` (disable threads) mode writes proxies synchronously. Normal app exit
waits for all submitted proxies to finish.

Each JPEG is written to a temporary file and renamed into place when complete, so
preview tools do not see partial JPEGs. A proxy failure is logged and does not
invalidate or delete the saved DNG.

To compare overhead, scan comparable material with the option off and on and use
**Alignment statistics → Effective frames / second**. The application log includes
`Proxy JPEG saved` entries with resize time, write time, and file size; DEBUG logs
also include the RGB copy time. These timings do not by themselves establish the
effect on overall scan speed.
