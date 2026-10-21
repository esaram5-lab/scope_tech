from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import socket
import threading
import unittest
from unittest.mock import patch

from scope_discovery import (
    DiscoveryResponder, DiscoveryError, NetworkInterface,
    discover_scopes, decode_packet, encode_packet, list_interfaces, SERVICE, VERSION,
)
from scope_capture_client import ScopeCaptureClient
from scope_capture_server import CaptureServer
from test_capture_api import PNG

TOKEN = 'paired-device-token-123456789'
NIC = NetworkInterface('test-loopback', '127.0.0.1', '255.0.0.0')


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.http = CaptureServer(('127.0.0.1', 0), TOKEN, lambda: PNG)
        self.worker = threading.Thread(target=self.http.serve_forever, kwargs={'poll_interval': 0.01})
        self.worker.start()
        self.udp = DiscoveryResponder(TOKEN, 'MSO58B_01', 'device-one', self.http.server_port,
                                     bind='127.0.0.1', port=0).start()
        self.options = dict(token=TOKEN, interfaces=[NIC], targets=['127.0.0.1'],
                            discovery_port=self.udp.port)

    def tearDown(self):
        self.udp.close()
        self.http.shutdown()
        self.http.server_close()
        self.worker.join(2)

    def test_discovery_deduplicates_replies_and_captures(self):
        found = discover_scopes(0.55, **self.options)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].host, '127.0.0.1')
        self.assertEqual(found[0].name, 'MSO58B_01')
        client = ScopeCaptureClient.auto_connect(discovery_timeout=0.1, **self.options)
        self.assertEqual(client.capture_png(), PNG)

    def test_wrong_token_no_device(self):
        options = dict(self.options, token='wrong-token')
        self.assertEqual(discover_scopes(0.1, **options), [])
        with self.assertRaisesRegex(DiscoveryError, 'No matching'):
            ScopeCaptureClient.auto_connect(discovery_timeout=0.1, **options)

    def test_filter_and_multiple_device_rejection(self):
        device = discover_scopes(0.1, **self.options)[0]
        second = replace(device, name='MSO58B_02', device_id='device-two')
        with patch('scope_discovery.discover_scopes', return_value=[device, second]):
            with self.assertRaisesRegex(DiscoveryError, 'Multiple scopes'):
                ScopeCaptureClient.auto_connect(token=TOKEN)
            client = ScopeCaptureClient.auto_connect(token=TOKEN, name='MSO58B_01')
            self.assertEqual(client.capture_png(), PNG)
            client = ScopeCaptureClient.auto_connect(token=TOKEN, device_id='device-one')
            self.assertEqual(client.host, '127.0.0.1')

    def test_multiple_routes_one_device_fallback(self):
        device = discover_scopes(0.1, **self.options)[0]
        with socket.socket() as closed:
            closed.bind(('127.0.0.1', 0))
            port = closed.getsockname()[1]
        bad = replace(device, port=port)
        with patch('scope_discovery.discover_scopes', return_value=[bad, device]):
            client = ScopeCaptureClient.auto_connect(token=TOKEN, timeout=0.1)
            self.assertEqual(client.capture_png(), PNG)

    def test_interfaces_and_apipa_broadcast(self):
        self.assertEqual(NetworkInterface('Ethernet', '169.254.25.3', '255.255.0.0').broadcast,
                         '169.254.255.255')
        self.assertEqual(NetworkInterface('Ethernet', '192.168.50.1', '255.255.255.0').broadcast,
                         '192.168.50.255')
        with self.assertRaisesRegex(DiscoveryError, 'No active'):
            discover_scopes(0.1, interface_names=['missing'], **self.options)

    def test_malformed_unauthenticated_packets_ignored(self):
        request = dict(service=SERVICE, version=VERSION, type='discover', nonce='a'*32)
        good = encode_packet(request, TOKEN)
        self.assertEqual(decode_packet(good, TOKEN, 'discover'), request)
        for raw in (b'[]', b'not json', b'x'*2049, b'{}', good.replace(b'scope-capture', b'scope-wrongxx')):
            self.assertIsNone(decode_packet(raw, TOKEN, 'discover'))
        self.assertIsNone(decode_packet(good, 'wrong', 'discover'))
        self.assertIsNone(decode_packet(encode_packet(dict(request, version=99), TOKEN), TOKEN, 'discover'))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(b'bad-json', ('127.0.0.1', self.udp.port))
        self.assertEqual(len(discover_scopes(0.1, **self.options)), 1)

    def test_stale_nonce_and_invalid_port_ignored(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as fake:
            fake.bind(('127.0.0.1', 0))
            fake.settimeout(1)
            def reply():
                data, peer = fake.recvfrom(2048)
                request = decode_packet(data, TOKEN, 'discover')
                base = dict(service=SERVICE, version=VERSION, type='reply',
                            nonce=request['nonce'], name='fake', device_id='fake', port=8765)
                fake.sendto(encode_packet(dict(base, nonce='0'*32), TOKEN), peer)
                fake.sendto(encode_packet(dict(base, port=70000), TOKEN), peer)
                fake.sendto(b'{}', peer)
            with ThreadPoolExecutor(max_workers=1) as pool:
                task = pool.submit(reply)
                self.assertEqual(discover_scopes(0.1, **dict(self.options, discovery_port=fake.getsockname()[1])), [])
                task.result()

    def test_responder_ip_filter(self):
        self.udp.allowed_ip = '192.0.2.1'
        self.assertEqual(discover_scopes(0.1, **self.options), [])

    def test_psutil_adapter_filtering(self):
        from types import SimpleNamespace as Obj
        import sys
        fake = Obj(net_if_stats=lambda: {'Ethernet': Obj(isup=True), 'Down': Obj(isup=False),
                                        'Loopback': Obj(isup=True), 'WiFi': Obj(isup=True)},
                   net_if_addrs=lambda: {
                       'Ethernet': [Obj(family=socket.AF_INET, address='169.254.1.2', netmask='255.255.0.0')],
                       'Down': [Obj(family=socket.AF_INET, address='192.168.0.1', netmask='255.255.255.0')],
                       'Loopback': [Obj(family=socket.AF_INET, address='127.0.0.1', netmask='255.0.0.0')],
                       'WiFi': [Obj(family=socket.AF_INET, address='192.168.1.2', netmask='255.255.255.0')],
                   })
        with patch.dict(sys.modules, psutil=fake):
            self.assertEqual([n.name for n in list_interfaces()], ['Ethernet', 'WiFi'])


if __name__ == '__main__':
    unittest.main()
