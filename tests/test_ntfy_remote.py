from io import BytesIO
import json
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from PIL import Image

from ntfy_notifications import validate_settings
from ntfy_remote import PauseCommands
from ntfy_preview import preview_jpeg
import test_ntfy_notifications as notifications_tests
from test_frame_alignment import scanner_functions


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.settings = validate_settings(dict(enabled=True, topic='private-test'))
        self.commands = PauseCommands(lambda: self.settings, threading.Event())
        self.commands.worker = Mock()  # No network in capability tests.
        self.context = ('run-1', '/reel', 12, 'guard_pause')
        self.actions, self.token = self.commands.arm(self.context, ('retry', 'save', 'stop'))

    def send(self, command='retry', token=None):
        self.commands.receive(dict(event='message', message=json.dumps(dict(
            capability=self.token if token is None else token, command=command))))

    def test_valid_button_consumed_once(self):
        self.assertEqual(len(self.actions), 3)
        self.assertTrue(self.actions[0]['url'].endswith('/private-test-commands'))
        self.send()
        self.send('save')
        self.assertEqual(self.commands.take(self.context), 'retry')
        self.assertIsNone(self.commands.take(self.context))

    def test_wrong_capability_command_context_and_malformed_messages(self):
        for event in ({}, dict(event='message', message='[]'), dict(event='message', message='bad'),
                      dict(event='message', message='{"capability": 12}')):
            self.commands.receive(event)
        self.send(token='forged')
        self.send(command='shell')
        self.assertIsNone(self.commands.take(self.context))
        for context in (('run-2', '/reel', 12, 'guard_pause'), ('run-1', '/other', 12, 'guard_pause'),
                        ('run-1', '/reel', 13, 'guard_pause'), ('run-1', '/reel', 12, 'recovery_failed')):
            self.send()
            self.assertIsNone(self.commands.take(context))

    def test_local_action_and_same_frame_repause_revoke_old_buttons(self):
        self.send()
        self.commands.invalidate()
        self.assertIsNone(self.commands.take(self.context))
        _, new_token = self.commands.arm(self.context, ('retry', 'save', 'stop'))
        self.send()
        self.assertIsNone(self.commands.take(self.context))
        self.send(token=new_token)
        self.assertEqual(self.commands.take(self.context), 'retry')

    def test_expiry_checked_at_execution_and_receive(self):
        self.send()
        with patch('ntfy_remote.time.monotonic', return_value=self.commands.active[3] + 1):
            self.assertIsNone(self.commands.take(self.context))
            self.send()
            self.assertTrue(self.commands.pending.empty())

    def test_disable_invalidates_queued_command(self):
        self.send()
        self.settings['remote_commands'] = False
        self.assertIsNone(self.commands.take(self.context))
        self.assertEqual(self.commands.arm(self.context, ('stop',)), ([], None))

    def test_recovery_only_allows_stop_and_input_is_bounded(self):
        self.actions, self.token = self.commands.arm(self.context, ('stop',))
        self.send('save')
        self.assertIsNone(self.commands.take(self.context))
        for _ in range(50):
            self.send('stop')
        self.assertEqual(self.commands.pending.qsize(), 16)
        self.assertEqual(self.commands.take(self.context), 'stop')


class AttachmentTests(unittest.TestCase):
    setUp = notifications_tests.NotificationTests.setUp
    close = notifications_tests.NotificationTests.close
    enable = notifications_tests.NotificationTests.enable
    wait = notifications_tests.NotificationTests.wait

    def test_image_and_actions_uploaded_together_without_mutating_source(self):
        self.enable()
        original = Image.new('RGB', (2000, 1500), 'gray')
        actions = [dict(action='http', label='Stop scan', url='https://ntfy.example/cmd',
                        body='test')]
        self.notifier.notify('guard_pause', 12, 'Test', image=original,
                             overlay=(0.12, 8, 0, 'strips'), actions=actions)
        self.wait()
        request = self.transport.call_args.args[0]
        self.assertEqual(request.method, 'PUT')
        self.assertEqual(json.loads(request.get_header('Actions')), actions)
        self.assertEqual(parse_qs(urlsplit(request.full_url).query)['filename'], ['held-frame.jpg'])
        preview = Image.open(BytesIO(request.data))
        self.assertEqual(preview.size, (1000, 750))
        self.assertLess(len(request.data), 2_000_000)
        self.assertEqual(original.getpixel((0, 0)), (128, 128, 128))

    def test_image_failure_falls_back_to_text_with_buttons(self):
        self.enable()
        self.transport.side_effect = [HTTPError('https://ntfy.example', 413, 'Too large', {}, None), self.response]
        actions = [dict(action='http', label='Stop', url='https://ntfy.example/cmd')]
        with self.assertLogs(level='WARNING'):
            self.notifier.notify('guard_pause', 12, 'Test', image=Image.new('RGB', (10, 10)), actions=actions)
            self.wait()
        self.assertEqual(json.loads(self.transport.call_args.args[0].data)['actions'], actions)

    def test_images_can_be_disabled(self):
        self.enable(images=False)
        self.notifier.notify('guard_pause', 12, 'Test', image=Image.new('RGB', (10, 10)))
        self.wait()
        self.assertEqual(self.transport.call_args.args[0].method, 'POST')


class DispatchTests(unittest.TestCase):
    def state(self, **overrides):
        state = dict(ntfy_notifier=Mock(), win=Mock(), ScanOngoing=True, alignment_paused=True,
                     ScanStopRequested=False, CurrentFrame=11, CurrentDir='/reel',
                     retry_alignment_frame=Mock(), stop_alignment_scan=Mock(),
                     save_alignment_frame_and_continue=Mock())
        state.update(overrides)
        return scanner_functions('poll_phone_commands', **state)

    def test_paused_guard_dispatches_on_main_thread_and_acknowledges(self):
        for command, handler in [('retry', 'retry_alignment_frame'), ('save', 'save_alignment_frame_and_continue'),
                                 ('stop', 'stop_alignment_scan')]:
            ns = self.state()
            ns['ntfy_notifier'].commands.take.return_value = command
            ns['poll_phone_commands']()
            ns[handler].assert_called_once()
            ns['ntfy_notifier'].notify.assert_called_once()
            ns['win'].after.assert_called_once()

    def test_running_stopped_and_stop_requested_scans_do_not_dispatch(self):
        for overrides in (dict(alignment_paused=False), dict(ScanOngoing=False), dict(ScanStopRequested=True)):
            ns = self.state(**overrides)
            ns['poll_phone_commands']()
            ns['invalidate_phone_commands'].assert_called_once()
            ns['ntfy_notifier'].commands.take.assert_not_called()

    def test_recovery_does_not_dispatch_save_or_retry(self):
        ns = self.state(alignment_recovery=Mock())
        ns['ntfy_notifier'].commands.take.return_value = 'save'
        ns['poll_phone_commands']()
        ns['save_alignment_frame_and_continue'].assert_not_called()
