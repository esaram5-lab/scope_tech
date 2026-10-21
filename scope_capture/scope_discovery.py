"""Authenticated IPv4 UDP discovery for Scope Capture. Python 3.9+."""
from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
import json
import logging
from pathlib import Path
import secrets
import select
import socket
import threading
import time

SERVICE = 'scope-capture'
VERSION = 1
DISCOVERY_PORT = 8766
MAX_PACKET = 2048


class DiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class NetworkInterface:
    name: str
    address: str
    netmask: str

    @property
    def broadcast(self):
        return str(ipaddress.IPv4Network(f'{self.address}/{self.netmask}', strict=False).broadcast_address)


@dataclass(frozen=True)
class ScopeDevice:
    device_id: str
    name: str
    host: str
    port: int
    interface: str
    local_address: str


def read_token(token=None, token_file=None):
    value = token if token is not None else Path(
        token_file or Path(__file__).with_name('capture_token.txt')
    ).read_text(encoding='utf-8').strip()
    if not value:
        raise ValueError('Empty capture token')
    return value


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def encode_packet(payload, token):
    payload = dict(payload)
    payload['signature'] = hmac.new(token.encode(), canonical(payload), hashlib.sha256).hexdigest()
    packet = canonical(payload)
    if len(packet) > MAX_PACKET:
        raise ValueError('Discovery packet too large')
    return packet


def decode_packet(packet, token, kind):
    try:
        if len(packet) > MAX_PACKET:
            return None
        data = json.loads(packet)
        if not isinstance(data, dict):
            return None
        signature = data.pop('signature', None)
        if not isinstance(signature, str) or len(signature) != 64:
            return None
        expected = hmac.new(token.encode(), canonical(data), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        if data.get('service') != SERVICE or data.get('version') != VERSION or data.get('type') != kind:
            return None
        nonce = data.get('nonce')
        if not isinstance(nonce, str) or len(nonce) != 32 or any(c not in '0123456789abcdef' for c in nonce):
            return None
        return data
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return None


def list_interfaces():
    """Up IPv4 adapters, including APIPA; psutil is required only here."""
    try:
        import psutil
    except ImportError as error:
        raise DiscoveryError('Automatic NIC detection needs psutil: py -m pip install psutil') from error
    stats = psutil.net_if_stats()
    result = []
    for name, addresses in psutil.net_if_addrs().items():
        if name not in stats or not stats[name].isup:
            continue
        for addr in addresses:
            if addr.family != socket.AF_INET or not addr.netmask:
                continue
            ip = ipaddress.IPv4Address(addr.address)
            if ip.is_loopback or ip.is_unspecified or ip.is_multicast:
                continue
            try:
                nic = NetworkInterface(name, addr.address, addr.netmask)
                network = ipaddress.IPv4Network(f'{nic.address}/{nic.netmask}', strict=False)
                if network.prefixlen < 31:
                    result.append(nic)
            except ValueError:
                continue
    return sorted(set(result), key=lambda n: (n.name, n.address))


def discover_scopes(timeout=2, *, token=None, token_file=None, discovery_port=DISCOVERY_PORT,
                    interface_names=None, interfaces=None, targets=None):
    """Return authenticated ScopeDevice routes. Never captures or changes IPs.

    interfaces: optional NetworkInterface list (bypasses psutil).
    targets: optional explicit IPv4 discovery targets, mainly for diagnostics/tests.
    interface_names: optional adapter-name allowlist. IPv4 interfaces otherwise all scanned.
    """
    if timeout <= 0 or not 1 <= discovery_port <= 65535:
        raise ValueError('Positive timeout and valid UDP port required')
    token = read_token(token, token_file)
    nics = list(list_interfaces() if interfaces is None else interfaces)
    if interface_names is not None:
        names = set(interface_names)
        nics = [nic for nic in nics if nic.name in names]
    if not nics:
        raise DiscoveryError('No active matching IPv4 interface. Check cable, adapter name and assigned IP; then retry.')
    nonce = secrets.token_hex(16)
    packet = encode_packet({'service': SERVICE, 'version': VERSION, 'type': 'discover', 'nonce': nonce}, token)
    sockets, routes, errors, found = [], {}, [], {}
    try:
        for nic in nics:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.bind((nic.address, 0))
                sock.setblocking(False)
                destinations = list(targets) if targets is not None else [nic.broadcast, '255.255.255.255']
                for address in destinations:
                    ipaddress.IPv4Address(address)
                routes[sock] = (nic, list(dict.fromkeys(destinations)))
                sockets.append(sock)
            except (OSError, ValueError) as error:
                errors.append(f'{nic.name}: {error}')
                sock.close()
        if not sockets:
            raise DiscoveryError('Cannot bind discovery sockets: ' + '; '.join(errors))
        deadline = time.monotonic() + timeout
        next_send, sent = time.monotonic(), False
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_send:
                for sock, (_, destinations) in routes.items():
                    for address in destinations:
                        try:
                            sock.sendto(packet, (address, discovery_port))
                            sent = True
                        except OSError as error:
                            errors.append(str(error))
                next_send = now + max(0.25, timeout / 2)
            wait = max(0, min(deadline, next_send) - time.monotonic())
            ready, _, _ = select.select(sockets, [], [], wait)
            for sock in ready:
                try:
                    raw, peer = sock.recvfrom(MAX_PACKET + 1)
                except OSError:
                    continue
                data = decode_packet(raw, token, 'reply')
                if not data or data['nonce'] != nonce:
                    continue
                port, device_id, name = data.get('port'), data.get('device_id'), data.get('name')
                if type(port) is not int or not 1 <= port <= 65535:
                    continue
                if not isinstance(device_id, str) or not 1 <= len(device_id) <= 128:
                    continue
                if not isinstance(name, str) or not 1 <= len(name) <= 128:
                    continue
                nic = routes[sock][0]
                device = ScopeDevice(device_id, name, peer[0], port, nic.name, nic.address)
                found[(device_id, peer[0], port)] = device
        if not sent:
            raise DiscoveryError('No discovery request could be sent: ' + '; '.join(errors[:4]))
        return sorted(found.values(), key=lambda d: (d.name, d.device_id, d.host, d.port))
    finally:
        for sock in sockets:
            sock.close()


class DiscoveryResponder:
    def __init__(self, token, name, device_id, api_port, *, bind='0.0.0.0', port=DISCOVERY_PORT,
                 allowed_ip=None, api_bind='0.0.0.0'):
        if not 1 <= len(name) <= 128 or not 1 <= len(device_id) <= 128:
            raise ValueError('Device name and ID must be 1..128 characters')
        self.token, self.name, self.device_id = token, name, device_id
        self.api_port, self.allowed_ip, self.api_bind = api_port, allowed_ip, api_bind
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind((bind, port))
            self.sock.settimeout(0.2)
        except BaseException:
            self.sock.close()
            raise
        self.port = self.sock.getsockname()[1]
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, name='ScopeDiscovery', daemon=True)

    def start(self):
        self.thread.start()
        return self

    def close(self):
        self.stop.set()
        self.thread.join(timeout=1)
        self.sock.close()

    def run(self):
        recent = {}
        window, replies = time.monotonic(), 0
        while not self.stop.is_set():
            try:
                raw, peer = self.sock.recvfrom(MAX_PACKET + 1)
            except socket.timeout:
                continue
            except OSError:
                return
            if self.allowed_ip and peer[0] != self.allowed_ip:
                continue
            data = decode_packet(raw, self.token, 'discover')
            if not data:
                continue
            now = time.monotonic()
            if now - window >= 1:
                window, replies = now, 0
                recent.clear()
            if replies >= 20 or now - recent.get(peer, -100) < 0.15:
                continue
            # A reply must advertise an HTTP endpoint reachable on this route.
            if self.api_bind != '0.0.0.0':
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
                        route.connect(peer)
                        if route.getsockname()[0] != self.api_bind:
                            continue
                except OSError:
                    continue
            reply = encode_packet({'service': SERVICE, 'version': VERSION, 'type': 'reply',
                                   'nonce': data['nonce'], 'name': self.name,
                                   'device_id': self.device_id, 'port': self.api_port}, self.token)
            try:
                self.sock.sendto(reply, peer)
                recent[peer] = now
                replies += 1
            except OSError:
                logging.debug('Discovery reply failed')
