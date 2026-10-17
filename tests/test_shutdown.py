import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import scope as app
from scopetech.mso import MSO5B
from scopetech.sim import SimTransport
from scopetech.transport import ScopeError


class ShutdownTest(unittest.TestCase):
    def test_interrupt_while_enabling_aoe_disables_it(self):
        t = SimTransport()
        s = MSO5B(t, settle_s=0)
        original = s.write

        def write(cmd):
            original(cmd)
            if cmd == 'ACTONEVent:ENable 1':
                raise KeyboardInterrupt

        s.write = write
        with self.assertRaises(KeyboardInterrupt):
            s.screenshot()
        self.assertEqual(t.sim.aoe['ENABLE'], '0')
        self.assertEqual(t.sim.acq_state, 0)
        self.assertFalse(s.aoe_active)

    def test_cleanup_continues_when_one_command_fails(self):
        t = SimTransport()
        s = MSO5B(t, settle_s=0)
        s.aoe_active = True
        original = s.write

        def write(cmd):
            if cmd == 'ACQuire:STATE STOP':
                raise ScopeError('stop failed')
            original(cmd)

        s.write = write
        errors = s.abort_capture()
        self.assertTrue(errors)
        self.assertIn('ACTONEVent:ENable 0', t.sim.log)
        self.assertEqual(t.timeout, 10)

    def test_connection_failure_closes_transport_and_log(self):
        t = Mock()
        logger = Mock()
        with patch.object(app, 'open_transport', return_value=t), patch.object(app, 'make_logger', return_value=logger), patch.object(app, 'MSO5B') as cls:
            cls.return_value.connect.side_effect = ScopeError('connect failed')
            cls.return_value.abort_capture.return_value = []
            self.assertEqual(app.main(['--sim']), 1)
        t.close.assert_called_once()
        logger.close.assert_called_once()

    def test_cancel_in_measurement_cleans_up_before_close(self):
        t = Mock()
        logger = Mock()
        with patch.object(app, 'open_transport', return_value=t), patch.object(app, 'make_logger', return_value=logger), patch.object(app, 'MSO5B') as cls, patch('scopetech.wizard.Wizard') as wizard:
            cls.return_value.connect.return_value = 'MSO58B'
            cls.return_value.abort_capture.return_value = []
            wizard.return_value.run.return_value = 130
            self.assertEqual(app.main(['--sim']), 130)
            cls.return_value.abort_capture.assert_called_once()
        t.close.assert_called_once()
