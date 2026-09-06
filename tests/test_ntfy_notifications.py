import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError, URLError

from ntfy_notifications import NtfyNotifier, save_settings, validate_settings
from test_frame_alignment import scanner_functions


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.response = MagicMock()
        self.response.__enter__.return_value.read.return_value = b'{}'
        self.transport = patch('ntfy_notifications.urlopen', return_value=self.response).start()
        self.addCleanup(patch.stopall)
        self.notifier = NtfyNotifier(Path(self.folder.name) / 'ntfy.json')
        self.addCleanup(self.close)

    def close(self):
        self.notifier.close()
        self.notifier.worker.join(2)

    def enable(self, **overrides):
        self.notifier.configure(dict(enabled=True, server='https://ntfy.example',
                                     topic='test-scanner', token='', **overrides))

    def wait(self):
        self.notifier.pending.join()

    def test_disabled_does_not_publish(self):
        self.assertFalse(self.notifier.notify('guard_pause', 206, 'Overshot'))
        self.transport.assert_not_called()

    def test_incidents_are_deduplicated_and_new_run_can_alert_again(self):
        self.enable()
        for _ in range(3):
            self.notifier.notify('guard_pause', 206, 'Overshot', folder='/mnt/scans/reel', run_id='one')
        self.notifier.notify('recovery_failed', 207, 'Progress too small', folder='/mnt/scans/reel', run_id='one')
        self.notifier.notify('guard_pause', 206, 'Overshot', folder='/mnt/scans/reel', run_id='two')
        self.wait()
        self.assertEqual(self.transport.call_count, 3)
        requests = [call.args[0] for call in self.transport.call_args_list]
        payload = json.loads(requests[0].data)
        self.assertEqual(payload['topic'], 'test-scanner')
        self.assertIn('reel — frame 206', payload['message'])
        self.assertNotIn('/mnt/scans', payload['message'])
        self.assertEqual(payload['priority'], 4)
        self.assertEqual(requests[0].full_url, 'https://ntfy.example/')
        self.assertEqual(self.transport.call_args.kwargs['timeout'], 5)

    def test_failed_share_alerts_once_across_save_workers(self):
        self.enable()
        for frame in (1, 2, 3):
            self.notifier.notify('save_failed', frame, 'Stale file handle', folder='/scans/reel', run_id='one')
        self.wait()
        self.assertEqual(self.transport.call_count, 1)

    def test_blocked_network_does_not_block_enqueue_and_queue_is_bounded(self):
        self.enable()
        entered, release = threading.Event(), threading.Event()
        def blocked(*_args, **_kwargs):
            entered.set()
            release.wait(3)
            return self.response
        self.transport.side_effect = blocked
        self.notifier.notify('guard_pause', 1, 'Overshot')
        self.assertTrue(entered.wait(2))
        try:
            results = [self.notifier.notify('guard_pause', frame, 'Overshot') for frame in range(2, 22)]
            self.assertEqual(sum(results), 16)
            self.assertEqual(self.notifier.pending.qsize(), 16)
        finally:
            release.set()
        self.wait()

    def test_transient_failure_retries_once_without_logging_secrets(self):
        self.notifier.configure(dict(enabled=True, server='https://ntfy.example',
                                     topic='private-topic', token='secret-token'))
        self.transport.side_effect = [URLError('unreachable'), self.response]
        with patch.object(self.notifier.stopping, 'wait', return_value=False), self.assertLogs(level='INFO') as logs:
            self.notifier.notify('guard_pause', 1, 'Overshot')
            self.wait()
        self.assertEqual(self.transport.call_count, 2)
        request = self.transport.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer secret-token')
        self.assertNotIn('secret-token', '\n'.join(logs.output))
        self.assertNotIn('private-topic', '\n'.join(logs.output))

    def test_rejected_auth_is_not_retried_and_worker_survives(self):
        self.enable()
        self.transport.side_effect = HTTPError('https://ntfy.example', 403, 'Forbidden', {}, None)
        with self.assertLogs(level='WARNING'):
            self.notifier.notify('guard_pause', 1, 'Overshot')
            self.wait()
        self.assertEqual(self.transport.call_count, 1)
        self.assertIn('403', self.notifier.status)
        self.transport.side_effect = None
        self.notifier.notify('guard_pause', 2, 'Overshot')
        self.wait()
        self.assertTrue(self.notifier.worker.is_alive())
        self.assertEqual(self.notifier.status, 'Notification accepted by ntfy')

    def test_settings_persist_locally_with_restricted_permissions(self):
        self.enable()
        path = self.notifier.settings_path
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        restored = NtfyNotifier(path)
        try:
            self.assertEqual(restored.get_settings(), self.notifier.get_settings())
        finally:
            restored.close()
            restored.worker.join(2)

    def test_draft_test_can_send_while_disabled_without_saving_settings(self):
        self.notifier.notify('test', None, 'Test', test_settings=dict(
            enabled=False, server='https://ntfy.example', topic='draft-topic', token=''))
        self.wait()
        self.assertEqual(json.loads(self.transport.call_args.args[0].data)['topic'], 'draft-topic')
        self.assertFalse(self.notifier.get_settings()['enabled'])
        self.assertFalse(self.notifier.settings_path.exists())

    def test_invalid_destinations_are_rejected_before_saving(self):
        for overrides in (dict(server='file:///etc/passwd'), dict(server='https://user:pass@ntfy.example'),
                          dict(topic=''), dict(topic='../invalid'), dict(token='abc\nxyz')):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                validate_settings(dict(enabled=True, server='https://ntfy.example',
                                       topic='valid', token='') | overrides)


class NotificationIntegrationTests(unittest.TestCase):
    def test_save_exception_reports_worker_frame_instead_of_live_counter(self):
        ns = scanner_functions('notify_save_exception', CurrentFrame=900, notify_scan_problem=Mock())
        def capture_save_thread():
            frame_idx = 206
            raise OSError('Stale file handle')
        try:
            capture_save_thread()
        except OSError as error:
            ns['notify_save_exception'](error, error.__traceback__)
        self.assertEqual(ns['notify_scan_problem'].call_args.args[:2], ('save_failed', 206))

    def test_other_worker_failures_do_not_claim_a_saved_frame_failed(self):
        ns = scanner_functions('notify_save_exception', CurrentFrame=900, notify_scan_problem=Mock())
        try:
            raise RuntimeError('Unrelated worker')
        except RuntimeError as error:
            ns['notify_save_exception'](error, error.__traceback__)
        ns['notify_scan_problem'].assert_not_called()

    def test_thread_hook_enqueues_before_logging(self):
        events = []
        ns = scanner_functions('log_thread_exception', notify_save_exception=Mock(
            side_effect=lambda *_args: events.append('notify')), logging=Mock())
        ns['logging'].exception.side_effect = lambda *_args, **_kwargs: events.append('log')
        ns['log_thread_exception'](SimpleNamespace(exc_type=OSError, exc_value=OSError('NFS'), exc_traceback=None))
        self.assertEqual(events, ['notify', 'log'])

    def test_sender_failure_cannot_interrupt_guard(self):
        ns = scanner_functions('notify_scan_problem', CurrentDir='/scans/reel',
                               ntfy_notifier=Mock(notify=Mock(side_effect=RuntimeError('Closed'))))
        with self.assertLogs(level='WARNING'):
            ns['notify_scan_problem']('guard_pause', 206, 'Overshot')


if __name__ == '__main__':
    unittest.main()
