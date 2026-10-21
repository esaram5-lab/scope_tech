import io
import unittest
from unittest.mock import patch

from scope_check import Checker
from scopetech.mso import MSO5B
from scopetech.sim import SimTransport
from scopetech.transport import ScopeTimeout


class AoeOrderingTest(unittest.TestCase):
    def transport(self):
        t = SimTransport()
        original = t.sim.handle

        def guarded(cmd):
            if cmd.upper() == 'ACTONEVENT:ENABLE 1':
                self.assertEqual(t.sim.acq_state, 0, 'AOE enabled while acquiring')
            return original(cmd)

        t.sim.handle = guarded
        return t

    def test_main_capture_from_running(self):
        t = self.transport()
        scope = MSO5B(t, settle_s=0)
        self.assertTrue(scope.screenshot().startswith(b'\x89PNG'))
        self.assertEqual(t.sim.aoe['ENABLE'], '0')

    def test_checker_capture_from_running_and_resumed_step(self):
        t = self.transport()
        ck = Checker(t, 'C:/', io.StringIO())
        self.assertTrue(ck.aoe_capture().endswith('.png'))
        self.assertTrue(ck.orig_cwd)
        self.assertEqual(t.sim.aoe['ENABLE'], '0')

    def test_stop_timeout_never_enables_aoe(self):
        t = self.transport()
        scope = MSO5B(t, settle_s=0)
        original = scope.query
        scope.query = lambda cmd: '1' if cmd == 'ACQuire:STATE?' else original(cmd)
        with patch('scopetech.mso.time.monotonic', side_effect=[0, 11]):
            with self.assertRaises(ScopeTimeout):
                scope.screenshot()
        self.assertNotIn('ACTONEVent:ENable 1', t.sim.log)

    def test_busy_timeout_never_enables_aoe(self):
        t = self.transport()
        t.sim.acq_state = 0
        scope = MSO5B(t, settle_s=0)
        original = scope.query
        scope.query = lambda cmd: '1' if cmd == 'BUSY?' else original(cmd)
        with patch('scopetech.mso.time.monotonic', side_effect=[0, 16]):
            with self.assertRaises(ScopeTimeout):
                scope.screenshot()
        self.assertNotIn('ACTONEVent:ENable 1', t.sim.log)
