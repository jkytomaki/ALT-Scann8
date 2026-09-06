from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from proxy_jpeg import ProxyJpegWriter, capture_proxy_image
from rolling_average import RollingAverage
from test_frame_alignment import scanner_functions


class ProxyWriterTests(unittest.TestCase):
    def test_matching_names_dimensions_quality_and_source_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Image.new('RGB', (4056, 3040), (25, 110, 220))
            writer = ProxyJpegWriter()
            for filename in ('picture-00001.dng', 'picture-10002.dng', 'picture-00001.2.dng'):
                writer.submit(source.copy(), Path(folder) / filename)
            writer.close()  # Flush every queued proxy before returning.
            self.assertEqual(source.size, (4056, 3040))
            self.assertEqual(source.getpixel((0, 0)), (25, 110, 220))
            reference = Path(folder) / 'q90.jpg'
            source.resize((1366, 1024)).save(reference, quality=90)
            with Image.open(reference) as expected:
                for filename in ('picture-00001.jpg', 'picture-10002.jpg', 'picture-00001.2.jpg'):
                    with Image.open(Path(folder) / 'proxies' / filename) as proxy:
                        self.assertEqual(proxy.size, (1366, 1024))
                        self.assertEqual(proxy.format, 'JPEG')
                        self.assertEqual(proxy.quantization, expected.quantization)
                        self.assertLess(abs(proxy.getpixel((0, 0))[2] - 220), 3)
            self.assertFalse(list(Path(folder).rglob('*.tmp')))
            source.close()

    def test_publish_is_atomic_and_failure_keeps_existing_proxy(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'proxies' / 'picture-00001.jpg'
            destination.parent.mkdir()
            destination.write_bytes(b'old proxy')
            def fail_replace(source, target):
                self.assertEqual(target.read_bytes(), b'old proxy')
                with Image.open(source) as candidate:
                    candidate.load()
                    self.assertEqual(candidate.size, (1366, 1024))
                raise OSError('test filesystem failure')
            with patch('proxy_jpeg.os.replace', side_effect=fail_replace), self.assertLogs(level='ERROR'):
                ProxyJpegWriter.write(Image.new('RGB', (2000, 1500)), destination)
            self.assertEqual(destination.read_bytes(), b'old proxy')
            self.assertFalse(list(destination.parent.glob('*.tmp')))


class CaptureProxyTests(unittest.TestCase):
    def worker(self, enabled=True, hdr=0, save_error=False):
        events = []
        request = Mock()
        request.save_dng.side_effect = (OSError('DNG failed') if save_error else
                                        lambda path: events.append(('save', path)))
        request.make_image.side_effect = lambda stream: events.append(('copy', stream)) or 'rgb'
        request.release.side_effect = lambda: events.append(('release',))
        writer = Mock()
        writer.submit.side_effect = lambda image, path: events.append(('proxy', image, path))
        work = queue.Queue()
        work.put(('REQUEST', request, 10002, hdr, enabled))
        work.put('END')
        ns = scanner_functions('capture_save_thread', active_threads=1, ExitingApp=False,
            END_TOKEN='END', REQUEST_TOKEN='REQUEST', IMAGE_TOKEN='IMAGE', FileType='dng',
            FrameFilenamePattern='/film/picture-%05d.%s',
            HdrFrameFilenamePattern='/film/picture-%05d.%d.%s',
            DetectMisalignedFrames=False, total_wait_time_save_image=0,
            time_save_image=RollingAverage(5), capture_proxy_image=capture_proxy_image,
            proxy_jpeg_writer=writer)
        return ns, work, request, writer, events

    def test_same_frame_and_hdr_number_release_before_proxy_work(self):
        for hdr, basename in ((0, 'picture-10002.dng'), (2, 'picture-10002.2.dng')):
            ns, work, request, writer, events = self.worker(hdr=hdr)
            ns['capture_save_thread'](work, threading.Event(), 1)
            self.assertEqual(events, [('save', '/film/' + basename), ('copy', 'main'),
                                     ('release',), ('proxy', 'rgb', '/film/' + basename)])
            request.make_image.assert_called_once_with('main')

    def test_disabled_does_not_copy_or_enqueue_image(self):
        ns, work, request, writer, _ = self.worker(enabled=False)
        ns['capture_save_thread'](work, threading.Event(), 1)
        request.make_image.assert_not_called()
        writer.submit.assert_not_called()

    def test_dng_failure_releases_request_without_creating_proxy(self):
        ns, work, request, writer, _ = self.worker(save_error=True)
        with self.assertRaises(OSError):
            ns['capture_save_thread'](work, threading.Event(), 1)
        request.release.assert_called_once()
        writer.submit.assert_not_called()

    def test_proxy_copy_failure_does_not_fail_saved_dng(self):
        ns, work, request, writer, _ = self.worker()
        request.make_image.side_effect = RuntimeError('RGB unavailable')
        with self.assertLogs(level='ERROR'):
            ns['capture_save_thread'](work, threading.Event(), 1)
        request.save_dng.assert_called_once()
        request.release.assert_called_once()
        writer.submit.assert_not_called()

    def single(self, threaded=True, enabled=True):
        request = Mock()
        ns = scanner_functions('capture_single', CurrentFrame=10002, FileType='dng',
            hw_panel_installed=False, DisableThreads=not threaded, PreviewModuleValue=7,
            take_alignment_request=Mock(return_value=request), capture_display_queue=queue.Queue(),
            capture_save_queue=queue.Queue(), time_preview_display=Mock(), time_save_image=Mock(),
            total_wait_time_save_image=0, FrameFilenamePattern='/film/picture-%05d.%s',
            REQUEST_TOKEN='REQUEST', IMAGE_TOKEN='IMAGE', ProxyJpegEnabled=enabled,
            capture_proxy_image=capture_proxy_image, proxy_jpeg_writer=Mock(), draw_preview_image=Mock())
        return ns, request

    def test_capture_snapshots_option_without_an_extra_request(self):
        for enabled in (True, False):
            ns, request = self.single(enabled=enabled)
            ns['capture_single']('normal')
            ns['take_alignment_request'].assert_called_once()
            self.assertEqual(ns['capture_save_queue'].get_nowait(),
                             ('REQUEST', request, 10002, 0, enabled))

    def test_synchronous_mode_releases_request_before_proxy_and_preview_saves_nothing(self):
        ns, request = self.single(threaded=False)
        ns['proxy_jpeg_writer'].submit.side_effect = lambda *args, **kwargs: request.release.assert_called_once()
        ns['capture_single']('normal')
        self.assertEqual(ns['proxy_jpeg_writer'].submit.call_args.args[1], '/film/picture-10002.dng')
        self.assertFalse(ns['proxy_jpeg_writer'].submit.call_args.kwargs['background'])
        ns, request = self.single(threaded=False)
        ns['capture_single']('preview')
        request.save_dng.assert_not_called()
        ns['proxy_jpeg_writer'].submit.assert_not_called()

    def test_hdr_each_exposure_gets_its_matching_proxy(self):
        requests = [Mock(), Mock()]
        ns = scanner_functions('capture_hdr', HdrBracketAuto=False, recalculate_hdr_exp_list=False,
            session_frames=2, hdr_num_exposures=2, hdr_rev_exp_list=[20, 10],
            FileType='dng', HdrBracketShift=0, HDR_MAX_EXP=100, StabilizationDelayValue=0,
            dry_run_iterations=1, HdrMergeInPlace=False, images_to_merge=[], CurrentFrame=10002, PreviewModuleValue=7,
            camera=Mock(capture_request=Mock(side_effect=requests)), ProxyJpegEnabled=True,
            DisableThreads=False, FrameFilenamePattern='/film/picture-%05d.%s',
            HdrFrameFilenamePattern='/film/picture-%05d.%d.%s',
            capture_proxy_image=capture_proxy_image, proxy_jpeg_writer=Mock())
        ns['capture_hdr']('normal')
        for request, name in zip(requests, ('picture-10002.2.dng', 'picture-10002.dng')):
            request.save_dng.assert_called_once_with('/film/' + name)
            request.make_image.assert_called_once_with('main')
            request.release.assert_called_once()
        self.assertEqual([c.args[1] for c in ns['proxy_jpeg_writer'].submit.call_args_list],
                         ['/film/picture-10002.2.dng', '/film/picture-10002.dng'])


if __name__ == '__main__':
    unittest.main()
