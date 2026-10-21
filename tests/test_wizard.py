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
        # 핀 abc(오류)->12, 항목 1, 출력 핀 5, 입력 채널 9(오류)->1, 출력 채널 1(입력과 같음)->2, 타입·이름·온도 유지,
        # 설정 화면에서 Enter 두 번(측정 2회) 후 q
        self.assertEqual(self.run_menu(['abc', '12', '1', '5', '9', '1', '1', '2', '', '', '', 'r', 'r', 'q']), 0)
        self.assertEqual(self.b.vih.call_count, 2)
        self.b.vih.assert_called_with(1, 2)
        self.assertEqual(self.b.save.call_count, 2)
        self.assertEqual(self.b.base_result('VIH')['dut_pin'], '12')
        self.assertTrue(self.b.new_record('VIH', [1, 2]).folder.name.startswith('PIN12_OUT5_'))
        self.assertEqual(self.b.base_result('VIH')['out_pin'], '5')
        self.assertEqual(json.loads((self.root/'session.json').read_text())['out'], 2)

    def test_voh_and_settings(self):
        # 첫 설정 -> Enter 측정 -> s 로 이름·온도만 바꾸고 나머지 Enter -> Enter 측정 -> q
        self.assertEqual(self.run_menu(['7', '2', '3', 'od', '', '', 'r', 's', '', '', '', '', 'TEST2', 'nan', '-40', 'r', 'q']), 0)
        self.assertEqual(self.b.voh.call_count, 2)
        self.b.voh.assert_called_with([3])
        self.assertEqual((self.b.name, self.b.temp, self.b.out_type), ('TEST2', '-40', 'OD'))

    def test_cancel_before_measurement(self):
        self.assertEqual(self.run_menu(['12', '1', '', '', 'q']), 0)
        self.b.vih.assert_not_called()

    def test_capture_failure_does_not_save_or_report_success(self):
        self.b.capture.side_effect = ScopeError('capture failed')
        self.assertEqual(self.run_menu(['12', '1', '3', '', '', '', '', '', 'r']), 1)
        self.b.save.assert_not_called()
        self.assertFalse(any('측정 완료' in s for s in self.messages))

    def test_saved_defaults_and_cli_precedence(self):
        (self.root/'session.json').write_text(json.dumps({'pin':'12','item':'2','voh':4,'name':'old','temp':'85','out_type':'OD'}))
        self.b.name = 'CLI'
        self.b.explicit_options = {'name'}
        # 저장된 설정이 완전하면 질문 없이 설정 화면 -> Enter 로 바로 측정
        self.assertEqual(self.run_menu(['r', 'q']), 0)
        self.assertTrue(any('현재 설정' in m and '출력 CH4' in m for m in self.messages))
        self.assertEqual((self.b.name, self.b.temp), ('CLI', '85'))
        self.b.voh.assert_called_once_with([4])

    def test_edit_from_summary_changes_only_pin(self):
        (self.root/'session.json').write_text(json.dumps(
            {'pin': '2', 'item': '1', 'out_pin': '12', 'in': 1, 'out': 2, 'name': 'INV', 'temp': '25', 'out_type': 'PP'}))
        # r 다시 측정 -> s -> 핀 3, 출력 핀 13 만 바꾸고 나머지 Enter -> r 측정 -> q
        self.assertEqual(self.run_menu(['r', 's', '3', '', '13', '', '', '', '', '', 'r', 'q']), 0)
        self.assertEqual(self.b.vih.call_count, 2)
        self.assertEqual((self.b.dut_pin, self.b.out_pin), ('3', '13'))
        state = json.loads((self.root/'session.json').read_text())
        self.assertEqual((state['pin'], state['out_pin'], state['in'], state['out']), ('3', '13', 1, 2))

    def test_pin_shortcut_changes_only_pins(self):
        (self.root/'session.json').write_text(json.dumps(
            {'pin': '2', 'item': '1', 'out_pin': '12', 'in': 1, 'out': 2, 'name': 'INV', 'temp': '25', 'out_type': 'PP'}))
        # Enter -> 입력 핀 3, 출력 핀 13 -> 바로 측정 -> Enter -> 입력 핀 x(오류)->4, 출력 핀 Enter 유지 -> 측정 -> q
        self.assertEqual(self.run_menu(['', '3', '13', '', 'x', '4', '', 'q']), 0)
        self.assertEqual(self.b.vih.call_count, 2)
        self.b.vih.assert_called_with(1, 2)
        self.assertEqual((self.b.dut_pin, self.b.out_pin), ('4', '13'))
        state = json.loads((self.root/'session.json').read_text())
        self.assertEqual((state['pin'], state['out_pin'], state['name']), ('4', '13', 'INV'))

    def test_pin_shortcut_single_pin_item(self):
        (self.root/'session.json').write_text(json.dumps({'pin': '12', 'item': '2', 'voh': 4, 'out_type': 'OD'}))
        self.assertEqual(self.run_menu(['', '20', 'q']), 0)
        self.b.voh.assert_called_once_with([4])
        self.assertEqual(self.b.dut_pin, '20')
