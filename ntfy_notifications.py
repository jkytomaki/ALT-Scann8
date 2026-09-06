"""Bounded, asynchronous ntfy alerts independent of Tk, camera and scan storage."""
from collections import OrderedDict
import json
import logging
import os
from pathlib import Path
import queue
import re
import tempfile
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlencode
from ntfy_remote import PauseCommands
from ntfy_preview import preview_jpeg
from urllib.request import Request, urlopen


DEFAULT_SETTINGS = dict(enabled=False, server='https://ntfy.sh', topic='', token='',
                        images=True, remote_commands=True, command_topic='')


def validate_settings(settings):
    result = dict(DEFAULT_SETTINGS, **settings)
    result = {key: result[key] for key in DEFAULT_SETTINGS}
    if any(not isinstance(result[key], bool) for key in ('enabled', 'images', 'remote_commands')):
        raise ValueError('Notification enabled setting must be true or false')
    for key in ('server', 'topic', 'token', 'command_topic'):
        if not isinstance(result[key], str):
            raise ValueError(f'Invalid notification {key}')
        result[key] = result[key].strip()
    result['server'] = result['server'].rstrip('/')
    url = urlsplit(result['server'])
    if (url.scheme not in ('http', 'https') or not url.hostname or url.username or
            url.password or url.query or url.fragment):
        raise ValueError('Use an http:// or https:// server URL without credentials or a topic')
    if result['topic'] and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', result['topic']):
        raise ValueError('Topic must be 1–64 letters, numbers, underscores or hyphens')
    if result['command_topic'] and not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', result['command_topic']):
        raise ValueError('Invalid command topic')
    if result['command_topic'] and result['command_topic'] == result['topic']:
        raise ValueError('Use separate notification and command topics')
    if result['enabled'] and not result['topic']:
        raise ValueError('Enter a topic to enable notifications')
    if '\n' in result['token'] or '\r' in result['token']:
        raise ValueError('Access token must be a single line')
    return result


def save_settings(path, settings):
    settings = validate_settings(settings)
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.ntfy-', delete=False) as output:
            temporary = Path(output.name)
            os.chmod(temporary, 0o600)
            json.dump(settings, output, indent=2)
            output.write('\n')
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class NtfyNotifier:
    TITLES = dict(guard_pause='Scanner paused', recovery_failed='Recovery stopped',
                  save_failed='Frame save failed', test='Test notification', command_result='Phone command')

    def __init__(self, settings_path):
        self.settings_path = Path(settings_path)
        self.lock = threading.Lock()
        self.settings = dict(DEFAULT_SETTINGS)
        self.status = 'Notifications disabled'
        try:
            self.settings = validate_settings(json.loads(self.settings_path.read_text()))
            self.status = 'Ready' if self.settings['enabled'] else self.status
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            self.status = 'Could not load notification settings'
            logging.warning('Could not load ntfy settings; notifications disabled')
        self.pending = queue.Queue(maxsize=16)
        self.seen = OrderedDict()
        self.stopping = threading.Event()
        self.commands = PauseCommands(self.get_settings, self.stopping)
        self.worker = threading.Thread(target=self._run, name='ntfy-notifications', daemon=True)
        self.worker.start()

    def get_settings(self):
        with self.lock:
            return dict(self.settings)

    def configure(self, settings):
        settings = validate_settings(settings)
        save_settings(self.settings_path, settings)
        self.commands.invalidate()
        with self.lock:
            self.settings = settings
            self.seen.clear()
            self.status = 'Ready' if settings['enabled'] else 'Notifications disabled'

    def notify(self, kind, frame, reason, *, folder='', run_id=None, test_settings=None, image=None, overlay=None, actions=None, incident=None):
        """Queue once per incident; no network or filesystem access in this path."""
        settings = self.get_settings() if test_settings is None else validate_settings(
            dict(test_settings, enabled=True))
        if not settings['enabled'] or self.stopping.is_set():
            return False
        title = self.TITLES[kind]
        reel = os.path.basename(os.path.normpath(folder)) if folder else 'Scanner'
        message = (f'{reel} — frame {frame}\n{reason}' if frame is not None else str(reason))
        # A broken destination can kill multiple writers: one alert per run/folder.
        key = (run_id, folder, kind, None if kind == 'save_failed' else frame, incident)
        with self.lock:
            if kind not in ('test', 'command_result') and key in self.seen:
                return False
            try:
                self.pending.put_nowait((settings, kind, title, message[:2000],
                                        image.copy() if image is not None and settings['images'] else None,
                                        overlay, actions))
            except queue.Full:
                self.status = 'Notification queue full; alert dropped'
                logging.warning('ntfy queue full; alert dropped')
                return False
            if kind not in ('test', 'command_result'):
                self.seen[key] = None
                if len(self.seen) > 256:
                    self.seen.popitem(last=False)
            self.status = 'Notification queued'
        return True

    def _run(self):
        while not self.stopping.is_set():
            try:
                job = self.pending.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                settings, kind, title, message, image, overlay, actions = job
                if kind == 'test' or self.get_settings()['enabled']:
                    self._deliver(settings, kind, title, message, image, overlay, actions)
            except Exception:
                # Notification failures must never escape into scanner exception hooks.
                self.status = 'Notification delivery failed'
                logging.warning('ntfy notification delivery failed')
            finally:
                self.pending.task_done()

    def _deliver(self, settings, kind, title, message, image=None, overlay=None, actions=None):
        payload = dict(topic=settings['topic'], title=f'ALT-Scann8: {title}', message=message,
                       priority=3 if kind in ('test', 'command_result') else 4, tags=['test_tube' if kind == 'test' else 'warning'])
        if actions:
            payload['actions'] = actions
        headers = {'Content-Type': 'application/json'}
        if settings['token']:
            headers['Authorization'] = 'Bearer ' + settings['token']
        request = Request(settings['server'] + '/', json.dumps(payload).encode('utf-8'), headers,
                          method='POST')
        if image is not None:
            try:
                data = preview_jpeg(image, overlay)
                parameters = dict(title=payload['title'], message=message, priority=payload['priority'],
                                  tags='warning', filename='held-frame.jpg')
                image_headers = dict(headers, **{'Content-Type': 'image/jpeg'})
                if actions:
                    image_headers['Actions'] = json.dumps(actions)
                request = Request(settings['server'] + '/' + settings['topic'] + '?' + urlencode(parameters),
                                  data, image_headers, method='PUT')
            except Exception:
                logging.warning('Could not prepare ntfy image; sending text alert')
                image = None
        for attempt in range(2):
            if self.stopping.is_set():
                return
            try:
                with urlopen(request, timeout=5) as response:
                    response.read(4096)
                self.status = 'Notification accepted by ntfy'
                logging.info('ntfy accepted %s notification', kind)
                return
            except HTTPError as error:
                self.status = f'ntfy rejected notification (HTTP {error.code})'
                retry = error.code == 429 or error.code >= 500
                error.close()
            except (URLError, OSError, TimeoutError):
                self.status = 'Cannot reach ntfy; notification delivery failed'
                retry = True
            if not retry or attempt == 1 or self.stopping.wait(2):
                break
        logging.warning('%s', self.status)
        if image is not None and not self.stopping.is_set():
            self._deliver(settings, kind, title, message, actions=actions)

    def close(self):
        # Never make application shutdown wait for a network operation.
        self.stopping.set()
