import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from scopetech.config import Config
from scopetech.wizard import Wizard
from scopetech.workflow import Bench
from scopetech.transport import ScopeError


class WizardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = Mock(num_channels=8, idn='SIM')
        self.b = Bench(self.scope, Config(), self.root, echo=lambda s: None)
        self.b.vih = Mock(return_value=Mock(folder=self.root))
        self.b.voh = Mock(return_value=Mock(folder=self.root))
        self.b.capture = Mock()
        self.b.save = Mock()
        self.messages = []

    def run_menu(self, answers):
        it = iter(answers)
        return Wizard(self.b, self.root/'session.json', ask=lambda _: next(it), echo=self.messages.append).run()

    def test_invalid_channels_repeat_and_pin_metadata(self):
        self.assertEqual(self.run_menu(['abc', '12', '1', '9', '1', '1', '2', '', '1', '', '3']), 0)
        self.assertEqual(self.b.vih.call_count, 2)
        self.b.vih.assert_called_with(1, 2)
        self.assertEqual(self.b.save.call_count, 2)
        self.assertEqual(self.b._base_result('VIH')['dut_pin'], '12')
        self.assertTrue(self.b._new_record('VIH', [1, 2]).folder.name.startswith('PIN12_'))
        self.assertEqual(json.loads((self.root/'session.json').read_text())['out'], 2)

    def test_voh_and_settings(self):
        self.assertEqual(self.run_menu(['7', '2', '3', 'od', 's', 'TEST2', 'nan', '-40', '', '3']), 0)
        self.b.voh.assert_called_once_with([3])
        self.assertEqual((self.b.name, self.b.temp, self.b.out_type), ('TEST2', '-40', 'OD'))

    def test_cancel_before_measurement(self):
        self.assertEqual(self.run_menu(['12', '1', '', '', 'q']), 0)
        self.b.vih.assert_not_called()

    def test_capture_failure_does_not_save_or_report_success(self):
        self.b.capture.side_effect = ScopeError('capture failed')
        self.assertEqual(self.run_menu(['12', '1', '', '', '']), 1)
        self.b.save.assert_not_called()
        self.assertFalse(any('측정 완료' in s for s in self.messages))

    def test_saved_defaults_and_cli_precedence(self):
        (self.root/'session.json').write_text(json.dumps({'pin':'12','item':'2','voh':4,'name':'old','temp':'85','out_type':'OD'}))
        self.b.name = 'CLI'
        self.b.explicit_options = {'name'}
        self.assertEqual(self.run_menu(['', '', '', '', '', '3']), 0)
        self.assertEqual((self.b.name, self.b.temp), ('CLI', '85'))
        self.b.voh.assert_called_once_with([4])
