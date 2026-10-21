import io
from pathlib import Path
import struct
import tempfile
import threading
import unittest
import zlib

from scope_capture_server import CaptureServer, WindowTarget, capture_until_stable, ScreenNotStable
from scope_capture_client import ScopeCaptureClient, CaptureError, CaptureBusy, CaptureUnstable, validate_png, request


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)

PNG = (b'\x89PNG\r\n\x1a\n'
       + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
       + chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + chunk(b'IEND', b''))


class Tests(unittest.TestCase):
    def setUp(self):
        self.server = CaptureServer(('127.0.0.1', 0), 'test-token', lambda: PNG)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01})
        self.thread.start()
        self.client = ScopeCaptureClient('127.0.0.1', token='test-token', port=self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_health_bytes_save_preservation(self):
        self.assertEqual(self.client.health()['status'], 'ok')
        self.assertEqual(self.client.capture_png(), PNG)
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / '한글' / 'screen.png'
            self.assertEqual(self.client.capture_to_file(dest), dest)
            self.assertEqual(dest.read_bytes(), PNG)
            with self.assertRaises(FileExistsError):
                self.client.capture_to_file(dest)
            self.assertEqual(dest.read_bytes(), PNG)
            self.assertEqual(list(dest.parent.iterdir()), [dest])

    def test_auth_and_ip(self):
        self.client.token = 'bad'
        with self.assertRaisesRegex(CaptureError, '401'):
            self.client.capture_png()
        self.client.token = 'test-token'
        self.server.allowed_ip = '192.0.2.1'
        with self.assertRaisesRegex(CaptureError, '403'):
            self.client.health()

    def test_overlap_rejected_not_queued(self):
        entered, release = threading.Event(), threading.Event()
        calls, result = [], []
        def capture():
            calls.append(1)
            entered.set()
            release.wait(2)
            return PNG
        self.server.capture = capture
        first = threading.Thread(target=lambda: result.append(self.client.capture_png()))
        first.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(self.client.health()['busy'])
            with self.assertRaises(CaptureBusy):
                self.client.capture_png()
            self.assertEqual(len(calls), 1)
        finally:
            release.set()
            first.join(2)
        self.assertEqual(result, [PNG])

    def test_capture_failure_releases_lock(self):
        def fail():
            raise RuntimeError('simulated locked desktop')
        self.server.capture = fail
        with self.assertRaisesRegex(CaptureError, '503'):
            self.client.capture_png()
        self.server.capture = lambda: PNG
        self.assertEqual(self.client.capture_png(), PNG)

    def test_timeout_then_recovery(self):
        release = threading.Event()
        self.server.capture = lambda: (release.wait(2), PNG)[1]
        self.client.timeout = 0.05
        try:
            with self.assertRaises(CaptureError):
                self.client.capture_png()
        finally:
            release.set()
        # Wait for the in-progress handler to release its capture lock.
        self.assertTrue(self.server.capture_lock.acquire(timeout=2))
        self.server.capture_lock.release()
        self.server.capture = lambda: PNG
        self.client.timeout = 2
        self.assertEqual(self.client.capture_png(), PNG)

    def test_corrupt_png_never_saved(self):
        self.server.capture = lambda: PNG[:-4]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'bad.png'
            with self.assertRaises(CaptureError):
                self.client.capture_to_file(target)
            self.assertFalse(target.exists())
        broken = bytearray(PNG)
        broken[20] ^= 1
        with self.assertRaises(CaptureError):
            validate_png(bytes(broken))


class StableCaptureTests(unittest.TestCase):
    def setUp(self):
        self.server = CaptureServer(('127.0.0.1', 0), 'test-token', lambda: PNG)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01})
        self.thread.start()
        self.client = ScopeCaptureClient('127.0.0.1', token='test-token', port=self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_waits_for_identical_frames_and_returns_last(self):
        keys = iter(['redraw-1', 'redraw-2', 'final', 'final', 'final', 'never'])
        grabbed = []

        def grab():
            key = next(keys)
            grabbed.append(key)
            return key, lambda: PNG
        self.server.grab = grab
        self.assertEqual(self.client.capture_png(stable_ms=2000, interval_ms=20, frames=3), PNG)
        self.assertEqual(grabbed, ['redraw-1', 'redraw-2', 'final', 'final', 'final'])

    def test_never_stable_is_reported_not_saved(self):
        counter = iter(range(10**6))
        self.server.grab = lambda: (next(counter), lambda: PNG)
        with self.assertRaises(CaptureUnstable):
            self.client.capture_png(stable_ms=150, interval_ms=20)
        self.assertTrue(self.server.capture_lock.acquire(timeout=2))  # lock released
        self.server.capture_lock.release()
        self.assertEqual(self.client.capture_png(), PNG)  # plain capture still works

    def test_stable_without_grab_compares_png_bytes(self):
        self.assertEqual(self.client.capture_png(stable_ms=1000, interval_ms=20, frames=2), PNG)

    def test_bad_options_rejected_before_capture(self):
        calls = []
        self.server.capture = lambda: calls.append(1) or PNG
        for query in ({'stable_ms': 99999}, {'stable_ms': 500, 'frames': 1}, {'stable_ms': 'x'}, {'zoom': 2}):
            with self.assertRaisesRegex(CaptureError, '400'):
                request('127.0.0.1', self.server.server_port, 'test-token', 'capture', 2, query)
        self.assertEqual(calls, [])

    def test_health_reports_features_and_window(self):
        self.assertIn('stable', self.client.health()['features'])
        self.assertNotIn('window', self.client.health())
        self.server.status = lambda: {'title': 'TekScope', 'found': True}
        self.assertEqual(self.client.health()['window'], {'title': 'TekScope', 'found': True})


class FakeWindows:
    def __init__(self, windows, foreground=None, rects=None):
        self._windows, self._foreground, self.rects = windows, foreground, rects or {}

    def windows(self):
        return self._windows

    def rect(self, hwnd):
        return self.rects.get(hwnd, (0, 0, 1920, 1080))

    def foreground(self):
        return self._foreground


class WindowTargetTests(unittest.TestCase):
    def test_ignores_server_console_title(self):
        api = FakeWindows([(1, 'C:\\ScopeCapture\\python.exe scope_capture_server.py --window TekScope'), (2, 'TekScope')])
        self.assertEqual(WindowTarget('TekScope', api=api).find(), (2, 'TekScope'))

    def test_topmost_title_match_and_bbox(self):
        api = FakeWindows([(1, 'Notepad'), (2, 'tekscope - MSO58B'), (3, 'TekScope old')], foreground=2,
                          rects={2: (0, 0, 1900, 1040)})
        target = WindowTarget('TekScope', api=api)
        self.assertEqual(target.bbox(), (0, 0, 1900, 1040))
        self.assertEqual(target.status(), {'title': 'TekScope', 'found': True, 'matched': 'tekscope - MSO58B',
                                           'foreground': True, 'rect': [0, 0, 1900, 1040]})

    def test_missing_background_and_offscreen_windows_fail_clearly(self):
        with self.assertRaisesRegex(RuntimeError, 'not found'):
            WindowTarget('TekScope', api=FakeWindows([(1, 'Notepad')])).bbox()
        background = FakeWindows([(1, 'Notepad'), (2, 'TekScope')], foreground=1)
        self.assertEqual(WindowTarget('TekScope', api=background).bbox(), (0, 0, 1920, 1080))
        with self.assertRaisesRegex(RuntimeError, 'not in front'):
            WindowTarget('TekScope', require_foreground=True, api=background).bbox()
        offscreen = FakeWindows([(2, 'TekScope')], rects={2: (1920, 0, 1920, 1080)})
        with self.assertRaisesRegex(RuntimeError, 'off screen'):
            WindowTarget('TekScope', api=offscreen).bbox()

    def test_status_never_raises(self):
        class Broken(FakeWindows):
            def windows(self):
                raise OSError('EnumWindows failed')
        info = WindowTarget('TekScope', api=Broken([])).status()
        self.assertFalse(info['found'])
        self.assertIn('EnumWindows failed', info['error'])

    def test_window_failure_is_capture_error_503(self):
        target = WindowTarget('TekScope', api=FakeWindows([]))
        server = CaptureServer(('127.0.0.1', 0), 'test-token', lambda: (target.bbox(), PNG)[1], status=target.status)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01})
        thread.start()
        try:
            client = ScopeCaptureClient('127.0.0.1', token='test-token', port=server.server_port)
            with self.assertRaisesRegex(CaptureError, "503: Window not found: 'TekScope'"):
                client.capture_png()
            self.assertFalse(client.health()['window']['found'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class StableLoopTests(unittest.TestCase):
    def test_timeout_counts_frames(self):
        now = [0.0]
        frames = iter(range(100))
        with self.assertRaisesRegex(ScreenNotStable, '300 ms'):
            capture_until_stable(lambda: (next(frames), lambda: b''), 300, 100, 2,
                                 clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))


if __name__ == '__main__':
    unittest.main()
