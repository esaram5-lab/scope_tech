"""Windows desktop capture over LAN. No Tektronix API or SCPI calls."""
import argparse
import ctypes
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import socket
import urllib.parse
import uuid
import threading
import time

DWMWA_EXTENDED_FRAME_BOUNDS = 9
STABLE_MS_MAX = 10000
STABLE_DEFAULTS = {'interval_ms': 100, 'frames': 3}


def check_desktop():
    """Fail rather than return a misleading desktop while locked/disconnected."""
    if os.name != 'nt':
        raise RuntimeError('Desktop capture is supported only on Windows.')
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.OpenInputDesktop.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_uint]
    user32.OpenInputDesktop.restype = ctypes.c_void_p
    user32.SwitchDesktop.argtypes = [ctypes.c_void_p]
    user32.SwitchDesktop.restype = ctypes.c_bool
    user32.CloseDesktop.argtypes = [ctypes.c_void_p]
    desktop = user32.OpenInputDesktop(0, False, 0x0100)
    if not desktop:
        raise RuntimeError('Interactive desktop unavailable. Unlock the scope Windows session.')
    try:
        if not user32.SwitchDesktop(desktop):
            raise RuntimeError('Desktop locked or unavailable.')
    finally:
        user32.CloseDesktop(desktop)


def grab_desktop(bbox=None):
    """Return a PIL image of the primary screen (or bbox) after the desktop check."""
    check_desktop()
    from PIL import ImageGrab
    return ImageGrab.grab(bbox=bbox, all_screens=False)


def encode_png(image):
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


def capture_desktop(bbox=None):
    image = grab_desktop(bbox)
    try:
        return encode_png(image)
    finally:
        image.close()


class WinWindows:
    """Top-level window lookup via user32/dwmapi. Only used on Windows."""

    def __init__(self):
        import ctypes.wintypes as wt
        self.wt = wt
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        self.proc_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        u = self.user32
        u.EnumWindows.argtypes = [self.proc_type, wt.LPARAM]
        u.EnumWindows.restype = wt.BOOL
        u.IsWindowVisible.argtypes = [wt.HWND]
        u.IsIconic.argtypes = [wt.HWND]
        u.GetWindowTextLengthW.argtypes = [wt.HWND]
        u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        u.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        u.GetForegroundWindow.restype = wt.HWND
        u.GetSystemMetrics.argtypes = [ctypes.c_int]
        kernel32 = ctypes.WinDLL('kernel32')
        kernel32.GetConsoleWindow.restype = wt.HWND
        self.own_console = kernel32.GetConsoleWindow()  # 이 서버의 콘솔 창(제목에 명령줄이 보일 수 있음)
        try:
            self.dwmapi = ctypes.WinDLL('dwmapi')
            self.dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.POINTER(wt.RECT), wt.DWORD]
        except OSError:
            self.dwmapi = None

    def windows(self):
        """Visible, non-minimized titled top-level windows, topmost first: [(hwnd, title)]."""
        found = []

        def callback(hwnd, _):
            if hwnd != self.own_console and self.user32.IsWindowVisible(hwnd) and not self.user32.IsIconic(hwnd):
                n = self.user32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    self.user32.GetWindowTextW(hwnd, buf, n + 1)
                    found.append((hwnd, buf.value))
            return True
        self.user32.EnumWindows(self.proc_type(callback), 0)
        return found

    def rect(self, hwnd):
        """Visible window bounds clipped to the primary screen: (left, top, right, bottom)."""
        rect = self.wt.RECT()
        if self.dwmapi is None or self.dwmapi.DwmGetWindowAttribute(
                hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
            if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                raise RuntimeError('GetWindowRect failed')
        width, height = self.user32.GetSystemMetrics(0), self.user32.GetSystemMetrics(1)
        return (max(rect.left, 0), max(rect.top, 0), min(rect.right, width), min(rect.bottom, height))

    def foreground(self):
        return self.user32.GetForegroundWindow()


class WindowError(RuntimeError):
    """Target window missing/not in front; message is safe to return to the client."""


class WindowTarget:
    """Capture only one application window (e.g. TekScope), found by title on every capture."""

    def __init__(self, title, require_foreground=False, api=None):
        if not title.strip():
            raise ValueError('Window title must not be empty')
        self.title = title
        self.require_foreground = require_foreground
        self.api = api or WinWindows()

    def find(self):
        needle = self.title.casefold()
        for hwnd, title in self.api.windows():
            # 서버를 띄운 터미널 제목에 '--window TekScope' 가 보여 잘못 고르는 것을 막는다
            if needle in title.casefold() and 'scope_capture_server' not in title.casefold():
                return hwnd, title
        return None, None

    def bbox(self):
        hwnd, title = self.find()
        if hwnd is None:
            raise WindowError(f'Window not found: {self.title!r}. Is the scope application running and not minimized?')
        if self.require_foreground and self.api.foreground() != hwnd:
            raise WindowError(f'Window {title!r} is not in front')
        left, top, right, bottom = self.api.rect(hwnd)
        if right <= left or bottom <= top:
            raise WindowError(f'Window {title!r} is off screen')
        return left, top, right, bottom

    def status(self):
        """Health information; never raises."""
        try:
            hwnd, title = self.find()
            info = {'title': self.title, 'found': hwnd is not None}
            if hwnd is not None:
                info.update(matched=title, foreground=self.api.foreground() == hwnd, rect=list(self.api.rect(hwnd)))
            return info
        except Exception as error:  # health must answer even if Windows calls fail
            return {'title': self.title, 'found': False, 'error': str(error)}


def capture_until_stable(grab, max_ms, interval_ms=100, frames=3, clock=time.monotonic, sleep=time.sleep):
    """Grab repeatedly until `frames` consecutive frames are identical.

    grab() returns (key, make_png); key compares frame pixels, make_png() encodes only the chosen frame.
    Returns (png, grabbed_count, elapsed_ms) or raises ScreenNotStable after max_ms.
    """
    started = clock()
    key, make_png = grab()
    grabbed, same = 1, 1
    while same < frames:
        if (clock() - started) * 1000 >= max_ms:
            raise ScreenNotStable(f'Screen did not settle within {max_ms} ms ({grabbed} frames)')
        sleep(interval_ms / 1000)
        new_key, new_png = grab()
        grabbed += 1
        same = same + 1 if new_key == key else 1
        key, make_png = new_key, new_png
    return make_png(), grabbed, round((clock() - started) * 1000)


class ScreenNotStable(RuntimeError):
    pass


def parse_capture_options(query):
    """'/capture' query: stable_ms (0 = off), interval_ms, frames. Raises ValueError on bad input."""
    params = urllib.parse.parse_qs(query, strict_parsing=bool(query), max_num_fields=8)
    unknown = set(params) - {'stable_ms', 'interval_ms', 'frames'}
    if unknown:
        raise ValueError('Unknown capture option: ' + ', '.join(sorted(unknown)))

    def number(name, default, low, high):
        values = params.get(name)
        if not values:
            return default
        if len(values) != 1 or not values[0].isdigit():
            raise ValueError(f'{name} must be one non-negative integer')
        value = int(values[0])
        if not low <= value <= high:
            raise ValueError(f'{name} must be between {low} and {high}')
        return value
    return {
        'stable_ms': number('stable_ms', 0, 0, STABLE_MS_MAX),
        'interval_ms': number('interval_ms', STABLE_DEFAULTS['interval_ms'], 20, 1000),
        'frames': number('frames', STABLE_DEFAULTS['frames'], 2, 10),
    }


class CaptureServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def __init__(self, address, token, capture, allowed_ip=None, grab=None, status=None):
        """capture() -> PNG bytes. grab() -> (pixel key, make_png) for stable captures; when omitted the
        PNG bytes themselves are compared. status() -> extra health fields (e.g. window lookup)."""
        self.token = token
        self.capture = capture
        self.grab = grab
        self.status = status
        self.allowed_ip = allowed_ip
        self.capture_lock = threading.Lock()
        super().__init__(address, Handler)

    def grab_frame(self):
        if self.grab is not None:
            return self.grab()
        data = self.capture()
        return data, lambda: data


class Handler(BaseHTTPRequestHandler):
    server_version = 'ScopeCapture/1.0'

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, fmt, *args):
        # Do not log paths or headers, which may contain credentials.
        pass

    def respond(self, status, data, content_type='application/json', headers=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def message(self, status, message):
        self.respond(status, json.dumps({'message': message}).encode())

    def authorized(self):
        if self.server.allowed_ip and self.client_address[0] != self.server.allowed_ip:
            self.message(403, 'Client IP not allowed')
            return False
        supplied = self.headers.get('Authorization', '')
        expected = 'Bearer ' + self.server.token
        if not secrets.compare_digest(supplied.encode(), expected.encode()):
            self.message(401, 'Invalid token')
            return False
        return True

    def do_GET(self):
        try:
            if not self.authorized():
                return
            if self.path != '/health':
                self.message(404, 'Not found')
                return
            body = {'status': 'ok', 'busy': self.server.capture_lock.locked(), 'desktop_checked': False,
                    'features': ['stable', 'window']}
            if self.server.status is not None:
                body['window'] = self.server.status()
            self.respond(200, json.dumps(body).encode())
        except (OSError, TimeoutError):
            pass

    def do_POST(self):
        started = time.monotonic()
        try:
            if not self.authorized():
                return
            path, _, query = self.path.partition('?')
            if path != '/capture':
                self.message(404, 'Not found')
                return
            try:
                options = parse_capture_options(query)
            except ValueError as error:
                self.message(400, str(error))
                return
            if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Length', '0') != '0':
                self.message(400, 'Capture request must have an empty body')
                return
            if not self.server.capture_lock.acquire(blocking=False):
                self.message(409, 'Capture busy; request was not queued')
                return
            try:
                captured_at = datetime.now(timezone.utc).isoformat()
                headers = {'X-Capture-Started-UTC': captured_at}
                try:
                    if options['stable_ms']:
                        data, grabbed, waited = capture_until_stable(
                            self.server.grab_frame, options['stable_ms'], options['interval_ms'], options['frames'])
                        headers.update({'X-Capture-Frames': str(grabbed), 'X-Capture-Stable-Ms': str(waited)})
                    else:
                        data = self.server.capture()
                except ScreenNotStable as error:
                    logging.warning('Capture not stable: %s', error)
                    self.message(504, str(error))
                    return
                except WindowError as error:
                    logging.warning('Capture window: %s', error)
                    self.message(503, str(error))
                    return
                except Exception:
                    logging.exception('Capture failed')
                    self.message(503, 'Desktop capture failed; check scope server log, unlocked session and window')
                    return
                self.respond(200, data, 'image/png', headers)
                logging.info('capture OK client=%s bytes=%d seconds=%.3f %s', self.client_address[0], len(data),
                             time.monotonic() - started, headers.get('X-Capture-Frames', '') and
                             f"frames={headers['X-Capture-Frames']} stable_ms={headers['X-Capture-Stable-Ms']}")
            finally:
                self.server.capture_lock.release()
        except (OSError, TimeoutError):
            logging.warning('Client disconnected or timed out')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', help='Default: 0.0.0.0 with --discovery, otherwise 127.0.0.1')
    parser.add_argument('--discovery', action='store_true', help='Enable authenticated UDP discovery')
    parser.add_argument('--discovery-port', type=int, default=8766)
    parser.add_argument('--name', default=socket.gethostname())
    parser.add_argument('--device-id-file', type=Path, default=Path(__file__).with_name('capture_device_id.txt'))
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--allow-ip', help='Only allow this controller IPv4')
    parser.add_argument('--token-file', type=Path, default=Path(__file__).with_name('capture_token.txt'))
    area = parser.add_mutually_exclusive_group()
    area.add_argument('--bbox', nargs=4, type=int, metavar=('LEFT', 'TOP', 'RIGHT', 'BOTTOM'))
    area.add_argument('--window', metavar='TITLE',
                      help='Capture only the window whose title contains TITLE (e.g. TekScope), looked up each capture')
    parser.add_argument('--require-foreground', action='store_true',
                        help='With --window: refuse to capture unless that window is in front')
    parser.add_argument('--list-windows', action='store_true', help='Print visible window titles and exit')
    parser.add_argument('--local-test', type=Path, help='Save one local PNG and exit; no server')
    args = parser.parse_args()
    if args.require_foreground and not args.window:
        parser.error('--require-foreground needs --window')
    args.bind = args.bind or ('0.0.0.0' if args.discovery else '127.0.0.1')
    if not 1 <= args.port <= 65535 or not 1 <= args.discovery_port <= 65535:
        parser.error('Ports must be between 1 and 65535')
    if args.bbox and (args.bbox[2] <= args.bbox[0] or args.bbox[3] <= args.bbox[1]):
        parser.error('bbox requires RIGHT > LEFT and BOTTOM > TOP')
    if os.name != 'nt':
        parser.error('Run this server on the scope Windows desktop')
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()
    if args.list_windows:
        for _, title in WinWindows().windows():
            print(title)
        return
    target = WindowTarget(args.window, args.require_foreground) if args.window else None
    area = target.bbox if target else (lambda: tuple(args.bbox) if args.bbox else None)

    def capture():
        return capture_desktop(area())

    def grab():
        image = grab_desktop(area())
        return (image.size, image.tobytes()), lambda: encode_png(image)

    if args.local_test:
        data = capture()
        args.local_test.parent.mkdir(parents=True, exist_ok=True)
        with args.local_test.open('xb') as output:
            output.write(data)
        print('Saved:', args.local_test)
        return
    log = RotatingFileHandler(Path(__file__).with_name('capture_server.log'),
                              maxBytes=1_000_000, backupCount=3, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[log, logging.StreamHandler()],
                        format='%(asctime)s %(levelname)s %(message)s')
    if not args.token_file.exists():
        with args.token_file.open('x', encoding='utf-8') as output:
            output.write(secrets.token_urlsafe(32))
        print('Created token file:', args.token_file)
    token = args.token_file.read_text(encoding='utf-8').strip()
    if len(token) < 24 or not token.isascii() or any(c.isspace() for c in token):
        parser.error('Token must be at least 24 ASCII characters without whitespace')
    with CaptureServer((args.bind, args.port), token, capture, args.allow_ip, grab=grab,
                       status=target.status if target else None) as server:
        responder = None
        if args.discovery:
            from scope_discovery import DiscoveryResponder
            if not args.device_id_file.exists():
                with args.device_id_file.open('x', encoding='utf-8') as output:
                    output.write(str(uuid.uuid4()))
            device_id = args.device_id_file.read_text(encoding='utf-8').strip()
            responder = DiscoveryResponder(
                token, args.name, device_id, args.port, port=args.discovery_port,
                allowed_ip=args.allow_ip, api_bind=args.bind,
            ).start()
            logging.info('Discovery enabled UDP=%d name=%s id=%s', responder.port, args.name, device_id)
        if target:
            logging.info('Window capture: %r %s', args.window, target.status())
        logging.info('Listening on %s:%d; Ctrl+C stops server', args.bind, args.port)
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            logging.info('Stopped')
        finally:
            if responder:
                responder.close()


if __name__ == '__main__':
    main()
