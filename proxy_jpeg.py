"""Optional JPEG companions made from a completed capture's processed image."""
from concurrent.futures import ThreadPoolExecutor
import logging
import os
from pathlib import Path
import tempfile
import threading
import time

from PIL import Image


def capture_proxy_image(request):
    """Picamera2 make_image returns an image independent of the camera buffer."""
    started = time.monotonic()
    try:
        image = request.make_image('main')
        logging.debug('Proxy JPEG copied RGB in %.1f ms', (time.monotonic() - started) * 1000)
        return image
    except Exception:
        logging.exception('Proxy JPEG image unavailable; keeping the saved DNG')
        return None


class ProxyJpegWriter:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='proxy-jpeg')
        # One running and two queued images; never build an unbounded RGB backlog.
        self.slots = threading.BoundedSemaphore(3)

    def submit(self, image, dng_filename, *, background=True):
        if image is None:
            return
        # Resolve the destination now, before the user can switch target folders.
        destination = Path(dng_filename).absolute()
        destination = destination.parent / 'proxies' / destination.with_suffix('.jpg').name
        if not background:
            self.write(image, destination)
            return
        self.slots.acquire()
        try:
            self.executor.submit(self._run, image, destination)
        except Exception:
            self.slots.release()
            image.close()
            logging.exception('Proxy JPEG could not be queued: %s', destination)

    def _run(self, image, destination):
        try:
            self.write(image, destination)
        finally:
            self.slots.release()

    @staticmethod
    def write(image, destination):
        started = time.monotonic()
        temporary = None
        try:
            # Resize only the processed image. No sensor/camera configuration changes.
            with image.resize((1366, 1024), Image.Resampling.LANCZOS, reducing_gap=3.0) as resized:
                with resized.convert('RGB') as rgb:
                    resized_at = time.monotonic()
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.proxy-',
                                                     suffix='.tmp', delete=False) as output:
                        temporary = Path(output.name)
                        rgb.save(output, format='JPEG', quality=90)
                    # Readers see the proxy only once the complete JPEG is available.
                    os.replace(temporary, destination)
                    temporary = None
            logging.info('Proxy JPEG saved %s size=%s quality=90 resize_ms=%.1f write_ms=%.1f bytes=%d',
                         destination, '1366x1024', (resized_at - started) * 1000,
                         (time.monotonic() - resized_at) * 1000, destination.stat().st_size)
        except Exception:
            logging.exception('Proxy JPEG failed; keeping the saved DNG: %s', destination)
        finally:
            image.close()
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    logging.exception('Could not remove incomplete proxy: %s', temporary)

    def close(self):
        self.executor.shutdown(wait=True)
