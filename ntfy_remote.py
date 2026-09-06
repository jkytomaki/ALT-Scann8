"""Single-use pause capabilities and a bounded ntfy command subscriber."""
import hmac
import json
import queue
import secrets
import threading
import time
from urllib.request import Request, urlopen


class PauseCommands:
    LABELS = {'retry': 'Retry alignment', 'save': 'Save as-is and continue', 'stop': 'Stop scan'}

    def __init__(self, settings, stopping):
        self.settings = settings
        self.stopping = stopping
        self.lock = threading.Lock()
        self.active = None
        self.pending = queue.Queue(16)
        self.worker = None
        self.status = 'Remote commands idle'

    def arm(self, context, allowed):
        settings = self.settings()
        if not settings['enabled'] or not settings['remote_commands']:
            self.invalidate()
            return [], None
        capability = secrets.token_hex(32)
        with self.lock:
            self.active = (capability, context, frozenset(allowed), time.monotonic() + 1800)
        if self.worker is None:
            self.worker = threading.Thread(target=self._run, name='ntfy-commands', daemon=True)
            self.worker.start()
        headers = {'Content-Type': 'text/plain'}
        if settings['token']:
            headers['Authorization'] = 'Bearer ' + settings['token']
        actions = [dict(action='http', label=self.LABELS[command], method='POST',
                        url=settings['server'] + '/' + self.topic(settings), headers=headers,
                        body=json.dumps(dict(capability=capability, command=command)))
                   for command in allowed]
        return actions, capability

    @staticmethod
    def topic(settings):
        return settings['command_topic'] or settings['topic'][:55] + '-commands'

    def invalidate(self):
        with self.lock:
            self.active = None

    def receive(self, event):
        """Untrusted network input only enters a bounded queue after authentication."""
        try:
            if event.get('event') != 'message':
                return
            command = json.loads(event['message'])
            if not isinstance(command, dict):
                return
            capability = command.get('capability')
            if not isinstance(capability, str):
                return
            with self.lock:
                active = self.active
                if (active is None or not hmac.compare_digest(capability, active[0]) or
                        command.get('command') not in active[2] or time.monotonic() > active[3]):
                    return
                self.pending.put_nowait((capability, command['command']))
        except (ValueError, KeyError, TypeError, queue.Full):
            pass

    def take(self, context):
        """Called only by Tk; consume the capability before invoking any handler."""
        settings = self.settings()
        if not settings['enabled'] or not settings['remote_commands']:
            self.invalidate()
            return None
        while True:
            try:
                capability, command = self.pending.get_nowait()
            except queue.Empty:
                return None
            with self.lock:
                active = self.active
                if (active and active[0] == capability and active[1] == context and
                        time.monotonic() <= active[3] and command in active[2]):
                    self.active = None
                    return command

    def _run(self):
        while not self.stopping.is_set():
            settings = self.settings()
            with self.lock:
                active = self.active
            if not settings['enabled'] or not settings['remote_commands'] or active is None:
                self.stopping.wait(1)
                continue
            headers = {}
            if settings['token']:
                headers['Authorization'] = 'Bearer ' + settings['token']
            url = settings['server'] + '/' + self.topic(settings) + '/json?since=30s'
            try:
                with urlopen(Request(url, headers=headers), timeout=35) as response:
                    self.status = 'Listening for phone commands'
                    while not self.stopping.is_set() and self.settings() == settings:
                        line = response.readline(8193)
                        if not line or len(line) > 8192:
                            break
                        try:
                            self.receive(json.loads(line))
                        except (ValueError, AttributeError):
                            continue
            except Exception:
                self.status = 'Cannot receive phone commands; local controls remain available'
            self.stopping.wait(5)
