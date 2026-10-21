"""Standard-library client. No automatic retries or Tektronix commands."""
import argparse
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

MAX_PNG_BYTES = 64 * 1024 * 1024


class CaptureError(RuntimeError):
    pass


class CaptureBusy(CaptureError):
    pass


class CaptureUnstable(CaptureError):
    """stable_ms 안에 화면이 멈추지 않음 (획득이 실행 중이거나 화면이 계속 바뀜)."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(host, port, token, endpoint, timeout, query=None):
    if timeout <= 0:
        raise ValueError('timeout must be positive')
    # Validate literal IPv4; avoid proxy routing or sending credentials to redirects.
    socket.inet_pton(socket.AF_INET, host)
    suffix = '?' + urllib.parse.urlencode(query) if query else ''
    req = urllib.request.Request(
        f'http://{host}:{port}/{endpoint}{suffix}',
        data=b'' if endpoint == 'capture' else None,
        headers={'Authorization': 'Bearer ' + token},
        method='POST' if endpoint == 'capture' else 'GET',
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    started = time.monotonic()
    try:
        with opener.open(req, timeout=timeout) as response:
            limit = MAX_PNG_BYTES if endpoint == 'capture' else 4096
            length = int(response.headers.get('Content-Length', '-1'))
            if not 0 <= length <= limit:
                raise CaptureError('Missing or invalid response length')
            content_type = response.headers.get_content_type()
            if endpoint == 'capture' and content_type != 'image/png':
                raise CaptureError('Server did not return PNG')
            chunks = []
            received = 0
            while received < length:
                if time.monotonic() - started > timeout:
                    raise CaptureError('Capture deadline exceeded; no retry performed')
                chunk = response.read1(min(65536, length - received))
                if not chunk:
                    raise CaptureError('Incomplete response')
                chunks.append(chunk)
                received += len(chunk)
            return b''.join(chunks)
    except urllib.error.HTTPError as error:
        if error.code == 409:
            raise CaptureBusy('Scope capture busy; request was not queued') from error
        detail = _error_message(error)
        if error.code == 504:
            raise CaptureUnstable(detail or 'Screen did not settle') from error
        if error.code == 400:
            raise CaptureError(f'HTTP 400: {detail}') from error
        if error.code == 404 and query:
            raise CaptureError('HTTP 404: server does not support capture options; update scope_capture_server.py') from error
        if error.code == 503:
            raise CaptureError(f'HTTP 503: {detail or "capture failed"} (see scope capture_server.log)') from error
        raise CaptureError(f'HTTP {error.code}: check token, allowed IP, or server log') from error
    except (OSError, ValueError) as error:
        raise CaptureError(f'Communication failed: {error}') from error


def _error_message(error):
    try:
        return str(json.loads(error.read(4096)).get('message', ''))
    except (OSError, ValueError, AttributeError):
        return ''


def capture_options(stable_ms=0, interval_ms=None, frames=None):
    """Query for /capture. stable_ms > 0: wait until the screen stops changing (server must support it)."""
    if not stable_ms:
        return None
    query = {'stable_ms': int(stable_ms)}
    if interval_ms is not None:
        query['interval_ms'] = int(interval_ms)
    if frames is not None:
        query['frames'] = int(frames)
    return query


def validate_png(data):
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise CaptureError('Invalid PNG signature')
    offset, count, has_data = 8, 0, False
    while offset + 12 <= len(data):
        size = struct.unpack('>I', data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + size
        if end > len(data):
            raise CaptureError('Truncated PNG chunk')
        payload = data[offset + 8:offset + 8 + size]
        crc = struct.unpack('>I', data[end - 4:end])[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            raise CaptureError('PNG checksum mismatch')
        if count == 0:
            if kind != b'IHDR' or size != 13:
                raise CaptureError('Invalid PNG header')
            width, height = struct.unpack('>II', payload[:8])
            if not width or not height:
                raise CaptureError('Empty PNG dimensions')
        has_data |= kind == b'IDAT'
        if kind == b'IEND':
            if size or end != len(data) or not has_data:
                raise CaptureError('Invalid PNG ending')
            return
        count += 1
        offset = end
    raise CaptureError('PNG ending missing')


def capture_scope(host, save_path, timeout=5, *, token=None, token_file=None, port=8765,
                  stable_ms=0, interval_ms=None, frames=None):
    """Capture once; return Path. Never overwrite or retry. Token required."""
    if token is None:
        token = Path(token_file or Path(__file__).with_name('capture_token.txt')).read_text(encoding='utf-8').strip()
    if Path(save_path).exists():
        raise FileExistsError(f'Output already exists: {save_path}')
    query = capture_options(stable_ms, interval_ms, frames)
    return save_png(request(host, port, token, 'capture', timeout + stable_ms / 1000, query), save_path)


def save_png(data, save_path):
    target = Path(save_path)
    if target.exists():
        raise FileExistsError(f'Output already exists: {target}')
    target.parent.mkdir(parents=True, exist_ok=True)
    validate_png(data)
    fd, temporary = tempfile.mkstemp(prefix='.capture-', suffix='.tmp', dir=target.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        # Atomic no-overwrite publication on NTFS (also supported on normal Linux filesystems).
        # Fail safely on filesystems without hard-link support; no partial final PNG.
        if os.name == 'nt':
            os.rename(temporary, target)  # Windows rename refuses existing destinations.
        else:
            os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


class ScopeCaptureClient:
    """Reusable HTTP API client; configuration is immutable by convention.

    All requests are synchronous, independent, and never automatically retried.
    capture_png() returns PNG bytes; capture_to_file() returns pathlib.Path.
    """
    def __init__(self, host, *, token=None, token_file=None, port=8765, timeout=5):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.token = token if token is not None else Path(
            token_file or Path(__file__).with_name('capture_token.txt')
        ).read_text(encoding='utf-8').strip()

    @classmethod
    def auto_connect(cls, *, token=None, token_file=None, name=None, device_id=None,
                     discovery_timeout=2, timeout=5, discovery_port=8766, interface_names=None,
                     interfaces=None, targets=None):
        """Discover exactly one device, then verify its HTTP health. No capture yet.

        Multiple routes to the same persistent device ID are tried in order.
        Discovery never changes IP configuration. Requires scope_discovery.py.
        """
        from scope_discovery import discover_scopes, read_token, DiscoveryError
        token = read_token(token, token_file)
        devices = discover_scopes(discovery_timeout, token=token, discovery_port=discovery_port,
                                  interface_names=interface_names, interfaces=interfaces, targets=targets)
        devices = [d for d in devices if (name is None or d.name == name)
                   and (device_id is None or d.device_id == device_id)]
        if not devices:
            raise DiscoveryError('No matching authenticated scope. Check cable/IP, token, server --discovery and UDP firewall.')
        if len({d.device_id for d in devices}) != 1:
            choices = ', '.join(f'{d.name} [{d.device_id}] at {d.host}' for d in devices)
            raise DiscoveryError('Multiple scopes found; select name or device_id: ' + choices)
        errors = []
        for device in devices:
            client = cls(device.host, token=token, port=device.port, timeout=timeout)
            try:
                health = client.health()
                if health.get('status') != 'ok':
                    raise CaptureError('Unexpected health response')
                return client
            except (CaptureError, OSError, ValueError) as error:
                errors.append(str(error))
        raise DiscoveryError('Scope discovered, but HTTP health failed. Check TCP firewall/allow-ip: ' + '; '.join(errors))

    def health(self):
        return json.loads(request(self.host, self.port, self.token, 'health', self.timeout))

    def capture_png(self, stable_ms=0, interval_ms=None, frames=None):
        """Capture once. stable_ms > 0: the server grabs every interval_ms (default 100) and returns the
        frame once `frames` (default 3) consecutive grabs are identical; CaptureUnstable after stable_ms."""
        query = capture_options(stable_ms, interval_ms, frames)
        data = request(self.host, self.port, self.token, 'capture', self.timeout + stable_ms / 1000, query)
        validate_png(data)
        return data

    def capture_to_file(self, save_path, **options):
        if Path(save_path).exists():
            raise FileExistsError(f'Output already exists: {save_path}')
        return save_png(self.capture_png(**options), save_path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True, help='Scope IPv4 address')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--token-file', type=Path, default=Path(__file__).with_name('capture_token.txt'))
    parser.add_argument('--timeout', type=float, default=5)
    parser.add_argument('--stable-ms', type=int, default=0, help='Wait until the screen stops changing (max ms)')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--output', type=Path)
    group.add_argument('--health', action='store_true')
    args = parser.parse_args()
    try:
        token = args.token_file.read_text(encoding='utf-8').strip()
        if args.health:
            print(json.loads(request(args.host, args.port, token, 'health', args.timeout)))
        else:
            print('Saved:', capture_scope(args.host, args.output, args.timeout, token=token, port=args.port,
                                          stable_ms=args.stable_ms))
        return 0
    except (CaptureError, OSError, ValueError) as error:
        print('ERROR:', error)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
