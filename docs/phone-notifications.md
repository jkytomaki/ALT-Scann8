# Phone notifications with ntfy

Install the ntfy phone app and subscribe to your server and topic. In ALT-Scann8,
open **Settings → Phone notifications…**, enter the same server/topic, enable
notifications, and click **Save**. The optional access token supports protected
topics. **Send test notification** uses the fields currently shown without saving
them; the status reports whether ntfy accepted the message.

The scanner sends alerts when:

- The alignment guard pauses an unsaved frame for an operator decision.
- Forward recovery fails and the next frame remains unsaved.
- A primary frame save fails, including an exception in a save worker.

Messages include the reel name, frame number and reason. Successful corrections
and ordinary frames do not send alerts. Repeated alerts for the same incident are suppressed; a new pause after a phone
retry has fresh buttons and sends a new alert. A failed share can break several save workers; those
errors produce one alert per run and destination rather than one per worker.

The sender runs in a separate daemon thread. Event hooks copy available images and enqueue in memory;
they do not access the scan share or wait for HTTP. The queue holds at most 16
pending messages. Each network attempt has a five-second socket timeout, with one
retry after two seconds for connection failures, HTTP 429 or server errors. Other
HTTP errors are not retried. Delivery failures appear in the application log and
notification settings status. Application exit does not wait for the network.

Configuration is stored in `ntfy.json` beside the application, separately from the
scan session. The file is excluded from Git and written with owner-only permissions.
No topic or access token is included in the application logs. Keep anonymous
ntfy.sh topics unguessable: knowing the topic allows publishing and subscribing.

This reports events the application detects. A blocked NFS operation or a completely
frozen app may produce no exception, so this is not an external watchdog. It also
does not repair the share or change the application's save-error handling.

Deploying while a scan is running does not activate the feature in that process.
Notifications start after the next normal application restart. Subscribe on the
phone first, then use the test button to verify delivery.

## Frame previews and phone controls

**Attach held-frame preview** and **Allow phone buttons** are enabled by default
when notifications are enabled. Both can be disabled independently in Settings.
For a measured guard pause, the alert uploads a JPEG copy of that exact exposure
(maximum 1000 × 750). Cyan marks the alignment target, green marks the tolerance
limits, and red marks the measured sprocket/gap center at the left edge. The
caption identifies the measurement source. Failed recovery includes the measured
image when available. Camera and movement failures without an available exposure,
and save errors, remain text-only; no extra exposure is taken for notifications.
Images are uploaded to the configured ntfy server, with its attachment retention
and access rules. Failed uploads fall back to text with the same buttons.

Guard pauses offer **Retry alignment**, **Save as-is and continue**, and **Stop
scan**. Save accepts the position despite the guard warning, using the existing
normal capture/save path. Recovery failures offer only **Stop scan**. Save errors
have no remote controls. The scanner sends a result notification after dispatch;
"normal saving queued" does not mean asynchronous disk writes have completed.
Retry can produce a new pause alert with fresh buttons, even on the same frame.

Buttons expire after 30 minutes. Each pause uses a random 256-bit, single-use
capability. Commands must match the active run, destination, frame and pause kind.
Local retry/save/stop, settings changes and application restart invalidate old
buttons. Duplicate commands cannot execute twice. A network worker reads commands
into a bounded queue; only the Tk thread invokes the existing scanner controls.

The command topic defaults to the first 55 characters of the alert topic plus
`-commands`; an explicit command topic can be entered in Settings. Only the Pi
subscribes to this topic; your phone stays subscribed to the existing alert topic.
Protected servers must grant the configured token read/write access to the command
topic and publish access to the alert topic. HTTP buttons include that token when
configured. Anyone who can read the alert can use its buttons: keep the alert
topic private and restrict protected-topic subscriptions to trusted operators.

The subscriber reconnects automatically, reading the last 30 seconds of commands.
Long outages may lose a command; an ntfy HTTP success only confirms publication.
Wait for the scanner's result message. Local controls continue to work offline.
Phone clients must support ntfy HTTP actions and attachments; presentation varies
by client. Protocol references: https://docs.ntfy.sh/publish/#action-buttons and
https://docs.ntfy.sh/subscribe/api/.
