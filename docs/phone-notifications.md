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
and ordinary frames do not send alerts. Repeated pauses for the same frame in a
scan run are suppressed. A failed share can break several save workers; those
errors produce one alert per run and destination rather than one per worker.

The sender runs in a separate daemon thread. Event hooks only enqueue in memory;
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
