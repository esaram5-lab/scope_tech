import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from scopetech.config import Config
from scopetech.mso import MSO5B
from scopetech.sim import SimTransport
from scopetech.workflow import Bench
from scopetech.wizard import Wizard


class ManualTest(unittest.TestCase):
    def test_preserves_overlay_and_saves_csv_screen_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            t = SimTransport()
            s = MSO5B(t, settle_s=0)
            s.connect()
            s.add_measurement('MEAS1', 'TOP', 1)
            s.cursor_hbars(1, 1.0, 0.0)
            t.sim.log.clear()
            b = Bench(s, Config(), Path(d), echo=lambda _: None)
            b.dut_pin = '12'
            rec = b.save_manual('R_pu', [1, 2], '1 Mohm')
            b.fetch_pending()
            self.assertTrue((rec.folder/'waveform.csv').exists())
            self.assertTrue((rec.folder/'screen.png').read_bytes().startswith(b'\x89PNG'))
            data = json.loads((rec.folder/'result.json').read_text())
            self.assertEqual((data['dut_pin'], data['manual_item'], data['status']), ('12', 'R_pu', 'complete'))
            self.assertIn('MEAS1', t.sim.meas)
            self.assertEqual(t.sim.cursor['STATE'], '1')
            self.assertFalse(any('DELETEALL' in cmd.upper() or 'RRBDISPLAYSTATE' in cmd.upper() for cmd in t.sim.log))
            self.assertFalse((Path(d)/'results.csv').exists())
            self.assertFalse(list(rec.folder.glob('*.wfm')))
            rec2 = b.save_manual('R_pu', [1], '')
            self.assertNotEqual(rec.folder, rec2.folder)

    def test_menu_manual_only_and_validation(self):
        with tempfile.TemporaryDirectory() as d:
            b = Bench(Mock(num_channels=8), Config(), Path(d), echo=lambda _: None)
            b.save_manual = Mock(return_value=Mock(folder=Path(d)))
            b.vih = Mock()
            b.voh = Mock()
            answers = iter(['12', '3', 'IOH', '9', '1,2,1', '11 mA', '', '', '', 'q'])
            code = Wizard(b, Path(d)/'session.json', ask=lambda _: next(answers), echo=lambda _: None).run()
            self.assertEqual(code, 0)
            b.save_manual.assert_called_once_with('IOH', [1, 2], '11 mA')
            b.vih.assert_not_called()
            b.voh.assert_not_called()


class SnapTest(unittest.TestCase):
    def test_snap_turns_cursor_on_and_saves_screen_only(self):
        with tempfile.TemporaryDirectory() as d:
            t = SimTransport()
            s = MSO5B(t, settle_s=0)
            s.connect()
            t.sim.log.clear()
            b = Bench(s, Config(), Path(d), echo=lambda _: None)
            b.dut_pin = '5'
            rec = b.snap('edge', 'note')
            self.assertEqual(t.sim.cursor['STATE'], '1')
            self.assertFalse(any(cmd.upper().startswith(('CURVE', 'DATA:SOU', 'SAVE:WAVE', 'SAVE:SETUP', 'MEASUREMENT:ADDNEW'))
                                 for cmd in t.sim.log))
            self.assertEqual(b.fetch_pending(), (1, 0))
            self.assertTrue((rec.folder/'screen.png').read_bytes().startswith(b'\x89PNG'))
            self.assertEqual(sorted(p.name for p in rec.folder.iterdir()), ['result.json', 'screen.png'])
            data = json.loads((rec.folder/'result.json').read_text())
            self.assertEqual((data['dut_pin'], data['snap_item'], data['status']), ('5', 'edge', 'complete'))
            self.assertIn('SNAP', rec.folder.parts)
            self.assertTrue(rec.folder.name.endswith('_edge'))

    def test_menu_snap(self):
        with tempfile.TemporaryDirectory() as d:
            b = Bench(Mock(num_channels=8), Config(), Path(d), echo=lambda _: None)
            b.snap = Mock(return_value=Mock(folder=Path(d)))
            b.save_manual = Mock()
            b.vih = Mock()
            answers = iter(['3', '4', 'pulse', 'room', '', '', '', 'q'])
            code = Wizard(b, Path(d)/'session.json', ask=lambda _: next(answers), echo=lambda _: None).run()
            self.assertEqual(code, 0)
            b.snap.assert_called_once_with('pulse', 'room')
            b.save_manual.assert_not_called()
            b.vih.assert_not_called()


class NoCaptureTest(unittest.TestCase):
    def test_capture_off_skips_all_screen_saving(self):
        with tempfile.TemporaryDirectory() as d:
            t = SimTransport()
            s = MSO5B(t, settle_s=0)
            s.connect()
            cfg = Config()
            cfg.cp.set('capture', 'enabled', 'no')
            b = Bench(s, cfg, Path(d), echo=lambda _: None)
            b.dut_pin = '2'
            rec = b.vih(1, 2)
            t.sim.log.clear()
            self.assertEqual(b.capture(), [])
            b.save([1, 2])
            self.assertFalse(any('ACTONEVENT' in cmd.upper() or 'SAVE:IMAGE' in cmd.upper() for cmd in t.sim.log))
            self.assertEqual(b.pending_count(), 0)
            self.assertEqual(json.loads((rec.folder/'result.json').read_text())['capture'], 'off')
            self.assertTrue((rec.folder/'waveform.csv').exists())
            self.assertFalse(list(rec.folder.glob('*.png')))
            m = b.save_manual('R_pu', [1], '')
            self.assertFalse((m.folder/'screen.png').exists())
            self.assertEqual(b.pending_count(), 0)
            with self.assertRaises(ValueError):
                b.snap('x')

    def test_menu_without_capture(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Config()
            cfg.cp.set('capture', 'enabled', 'no')
            b = Bench(Mock(num_channels=8), cfg, Path(d), echo=lambda _: None)
            b.vih = Mock(return_value=Mock(folder=Path(d)))
            b.capture = Mock()
            b.save = Mock()
            b.reset = Mock()
            prompts = []
            answers = iter(['2', '4', '1', '12', '1', '2', 'pp', '', '', '', 'q'])

            def ask(p):
                prompts.append(p)
                return next(answers)
            code = Wizard(b, Path(d)/'session.json', ask=ask, echo=lambda _: None).run()
            self.assertEqual(code, 0)
            self.assertNotIn('4 화면만', prompts[1])
            b.vih.assert_called_once_with(1, 2)
            b.capture.assert_not_called()
            b.save.assert_called_once()
