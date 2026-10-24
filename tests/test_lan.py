import socket
import unittest
from unittest import mock

import scope
from scopetech.config import Config
from scopetech.transport import ScopeError, SocketTransport, lan_hint
from tests import test_screen_api


class LanHintTest(unittest.TestCase):
    def test_refused_port_names_socket_server(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()  # 아무도 듣지 않는 포트
        with self.assertRaises(ScopeError) as cm:
            SocketTransport("127.0.0.1", port, timeout=2)
        self.assertIn("연결 거부", str(cm.exception))
        self.assertIn("Socket Server", str(cm.exception))

    def test_hint_kinds(self):
        self.assertIn("응답 없음", lan_hint(TimeoutError("timed out"), 4000))
        self.assertIn("경로 없음", lan_hint(OSError(10065, "unreachable"), 4000))
        self.assertIn("Socket Server", lan_hint(OSError(1, "other"), 4000))


class LanVisaTest(unittest.TestCase):
    def test_resource_from_ip_and_protocol(self):
        cfg = Config()
        cfg.cp.set("connection", "ip", "192.168.0.10")
        self.assertEqual(scope.lan_visa_resource(cfg), "TCPIP0::192.168.0.10::hislip0::INSTR")
        cfg.cp.set("connection", "lan_visa_protocol", "vxi11")
        self.assertEqual(scope.lan_visa_resource(cfg), "TCPIP0::192.168.0.10::inst0::INSTR")
        cfg.cp.set("connection", "lan_resource", "TCPIP0::scope::inst0::INSTR")
        self.assertEqual(scope.lan_visa_resource(cfg), "TCPIP0::scope::inst0::INSTR")
        cfg.cp.set("connection", "lan_resource", "")
        cfg.cp.set("connection", "lan_visa_protocol", "raw")
        with self.assertRaises(ScopeError):
            scope.lan_visa_resource(cfg)

    def test_open_transport_uses_visa_with_lan_resource(self):
        cfg = Config()
        cfg.cp.set("connection", "interface", "lan_visa")
        cfg.cp.set("connection", "ip", "10.0.0.5")
        with mock.patch.object(scope, "VisaTransport") as visa:
            scope.open_transport(cfg, False, None)
        self.assertEqual(visa.call_args[0][0], "TCPIP0::10.0.0.5::hislip0::INSTR")
        self.assertTrue(visa.call_args[0][5])  # Device Clear (usb_clear) 그대로


class CaptureHostTest(unittest.TestCase):
    """[screen_api] host 가 비었고 스코프를 LAN 으로 제어하면 [connection] ip 를 쓴다."""

    def test_host_falls_back_to_connection_ip(self):
        h = test_screen_api.ScreenApiTest("test_stable_off_and_old_server")  # 가짜 캡처 클라이언트 도우미
        h.setUp()
        self.addCleanup(h.doCleanups)
        b, _ = h.bench(h.fake_dir(), host="")
        b.cfg.cp.set("connection", "interface", "lan")
        b.cfg.cp.set("connection", "ip", "127.0.0.9")
        self.assertEqual(b.capturer.client().host, "127.0.0.9")
        b.cfg.cp.set("connection", "interface", "usb")
        b.capturer._client = None
        b.cfg.cp.set("screen_api", "host", "127.0.0.8")
        self.assertEqual(b.capturer.client().host, "127.0.0.8")  # host 를 적으면 그 값


if __name__ == "__main__":
    unittest.main()
