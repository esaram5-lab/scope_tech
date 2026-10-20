"""현재 설정 한 화면 확인(Enter 측정 / s 변경) → 측정/캡처/저장의 안내형 실행."""
from __future__ import annotations

import json
from pathlib import Path

from .transport import ScopeError


class Wizard:
    def __init__(self, bench, state_path: Path, ask=input, echo=print):
        self.bench, self.state_path = bench, Path(state_path)
        self.ask, self.echo = ask, echo
        self.state = {}
        try:
            data = json.loads(self.state_path.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                self.state = data
        except (OSError, ValueError):
            pass

    def prompt(self, label, default='', valid=lambda x: True, allow_quit=True):
        while True:
            value = self.ask(f'{label}' + (f' [{default}]' if default else '') + ' > ').strip()
            if allow_quit and value.lower() == 'q':
                raise EOFError
            value = value or str(default)
            if valid(value):
                return value
            self.echo('입력값을 확인해주세요.')

    def channel(self, key, label, fallback):
        return int(self.prompt(label, self.state.get(key, fallback),
                               lambda s: s.isascii() and s.isdigit() and 1 <= int(s) <= self.bench.scope.num_channels))

    def remember(self):
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError as e:
            self.echo(f'[경고] 이전 설정을 저장하지 못했습니다: {e}')

    def run(self):
        status = self._run()
        b = self.bench
        n = b.pending_count() if hasattr(b, 'pending_count') else 0
        if n and status == 0:
            try:
                got, left = b.fetch_pending()
                self.echo(f'캡처 파일 {got}개 가져옴' + (f', {left}개 남음 (py scope.py --fetch 로 다시 시도)' if left else ''))
            except (ScopeError, OSError) as e:
                self.echo(f'[캡처 파일 가져오기 실패] {e}\nTekScope 상태 확인/재시작 후 py scope.py --fetch 로 다시 가져오세요.')
                return 1
        elif n:
            self.echo(f'스코프에 저장된 캡처 {n}개를 아직 가져오지 않았습니다. TekScope 상태 확인 후 py scope.py --fetch 로 가져오세요.')
        return status

    ITEM_TEXT = {'1': 'VIL/VIH + 출력 VOH/VOL', '2': 'VOH/VOL만(50회 통계)', '3': '데이터·화면 저장만', '4': '화면만 캡처(커서)'}
    HINT = {'1': '입력 삼각파 0~VCCIO, 트리거',
            '2': 'High/Low 반복 출력, 부하 조건, 트리거',
            '3': '원하는 측정·커서 (DC 신호는 Auto 트리거)',
            '4': '화면·커서 위치, 신호와 트리거'}

    def _items(self, cap):
        return ('1', '2', '3', '4') if cap else ('1', '2', '3')

    def _item_text(self, item, cap):
        return '데이터 저장만' if item == '3' and not cap else self.ITEM_TEXT[item]

    def _run(self):
        b = self.bench
        self.echo('\n측정 도우미 — 현재 설정 확인 후 Enter: 측정 / p: 핀만 변경 / s: 설정 변경 / q: 종료')
        # CLI에서 전달한 값이 저장된 기본값보다 우선한다.
        for key, attr in (('name', 'name'), ('temp', 'temp'), ('out_type', 'out_type')):
            if key in self.state and attr not in getattr(b, 'explicit_options', set()):
                value = self.state[key]
                if isinstance(value, str) and (key != 'out_type' or value in ('PP', 'OD')):
                    setattr(b, attr, value)
        cap = b.capture_enabled()
        self.note = ''
        try:
            editing = not self._complete(cap)
            while True:
                if editing:
                    self._edit(cap)
                    editing = False
                self._show(cap)
                action = self.prompt('Enter: 측정 시작 / p: 핀만 변경 / s: 설정 변경 / q: 종료', '',
                                     lambda s: s.lower() in ('', 'p', 's')).lower()
                if action == 's':
                    editing = True
                    continue
                if action == 'p':
                    self._edit_pin()
                    continue
                status = self._measure(cap)
                if status is not None:
                    return status
        except (EOFError, KeyboardInterrupt):
            self.echo('\n측정을 종료합니다.')
            return 0

    def _ch_ok(self, value):
        return str(value).isascii() and str(value).isdigit() and 1 <= int(value) <= self.bench.scope.num_channels

    def _complete(self, cap):
        """저장된 설정만으로 바로 측정할 수 있는지."""
        st = self.state
        item = st.get('item')
        if not (str(st.get('pin', '')).isascii() and str(st.get('pin', '')).isdigit()) or item not in self._items(cap):
            return False
        if item == '1':
            return (str(st.get('out_pin', '')).isdigit() and self._ch_ok(st.get('in', '')) and self._ch_ok(st.get('out', ''))
                    and int(st['in']) != int(st['out']))
        if item == '2':
            return self._ch_ok(st.get('voh', ''))
        if item == '3':
            return bool(st.get('manual_label')) and self.valid_channels(str(st.get('manual_channels', '')))
        return bool(st.get('snap_label'))

    def _edit(self, cap):
        """설정 변경: 지금 값이 기본값이라 바꿀 것만 입력하고 나머지는 Enter."""
        b, st = self.bench, self.state
        self.echo('\n[설정 변경] Enter: 지금 값 유지')
        digit = self.valid_pin
        st['pin'] = self.prompt('측정할 DUT 핀 번호 (VIL/VIH 는 입력 핀)', st.get('pin', ''), digit)
        items = self._items(cap)
        st['item'] = item = self.prompt('측정 항목: ' + '  '.join(f'{k} {self._item_text(k, cap)}' for k in items),
                                        st.get('item', '1') if st.get('item', '1') in items else '1', lambda s: s in items)
        if item == '1':
            st['out_pin'] = self.prompt('출력 DUT 핀 번호', st.get('out_pin', ''), digit)
            cin = self.channel('in', '입력 신호 스코프 채널', b.cfg.get('defaults', 'vih_in'))
            while True:
                cout = self.channel('out', '출력 신호 스코프 채널', b.cfg.get('defaults', 'vih_out'))
                if cout != cin:
                    break
                self.echo('입력과 출력은 서로 다른 채널을 선택해주세요.')
            st.update({'in': cin, 'out': cout})
        elif item == '2':
            st['voh'] = self.channel('voh', '출력 신호 스코프 채널', '2')
        elif item == '3':
            st['manual_label'] = self.prompt('시험 항목 이름 (R_pd / R_pu / IOH 등)', st.get('manual_label', 'R_pd'), lambda s: bool(s))
            st['manual_channels'] = self.prompt('저장할 채널 번호 (예: 1,2)', st.get('manual_channels', '1'), self.valid_channels)
        else:
            st['snap_label'] = self.prompt('캡처 이름 (폴더 이름에 들어갑니다)', st.get('snap_label', 'screen'), lambda s: bool(s))
        if item in ('1', '2'):
            b.out_type = self.prompt('출력 타입: PP / OD', b.out_type, lambda s: s.upper() in ('PP', 'OD')).upper()
        else:
            note = self.prompt('조건 메모 (생략 가능, - 입력 시 지움)', self.note, allow_quit=False)
            self.note = '' if note == '-' else note
        b.name = self.prompt('시험 이름', b.name, lambda s: bool(s))
        b.temp = self.prompt('온도(°C)', b.temp, self.valid_temp)
        st.update({'name': b.name, 'temp': b.temp, 'out_type': b.out_type})
        self.remember()

    def _edit_pin(self):
        """핀 번호만 변경(VIL/VIH 는 입력·출력 핀). 나머지 설정은 그대로."""
        st = self.state
        st['pin'] = self.prompt('DUT 핀 번호' + (' (입력 핀)' if st['item'] == '1' else ''), st['pin'], self.valid_pin)
        if st['item'] == '1':
            st['out_pin'] = self.prompt('출력 DUT 핀 번호', st.get('out_pin', ''), self.valid_pin)
        self.remember()

    @staticmethod
    def valid_pin(value):
        return value.isascii() and value.isdigit() and int(value) > 0

    def _chans(self):
        return list(dict.fromkeys(int(x) for x in str(self.state['manual_channels']).replace(',', ' ').split()))

    def _show(self, cap):
        b, st = self.bench, self.state
        item, pin = st['item'], st['pin']
        lines = ['', '──────────── 현재 설정 ────────────', f' 항목 : {item} {self._item_text(item, cap)}']
        if item == '1':
            lines += [f' 핀   : 입력 {pin} -> 출력 {st["out_pin"]}', f' 채널 : 입력 CH{st["in"]} / 출력 CH{st["out"]}',
                      f' 출력 : {b.out_type}']
        elif item == '2':
            lines += [f' 핀   : {pin}', f' 채널 : 출력 CH{st["voh"]}', f' 출력 : {b.out_type}']
        else:
            lines += [f' 핀   : {pin}']
            if item == '3':
                lines += [f' 이름 : {st["manual_label"]}', ' 채널 : ' + ', '.join(f'CH{c}' for c in self._chans())]
            else:
                lines += [f' 이름 : {st["snap_label"]}']
            lines += [f' 메모 : {self.note or "없음"}']
        lines += [f' 시험 : {b.name} / 온도 {b.temp}°C / 기준 {b.spec_name or "미지정"}',
                  ' 캡처 : ' + ('켬 (Single 1회 추가 획득)' if cap else '끔 (측정·파형 저장만)'),
                  f' 준비 : {self.HINT[item]}',
                  '──────────────────────────────────']
        self.echo('\n'.join(lines))

    def _measure(self, cap):
        """현재 설정으로 1회 측정. 계속하면 None, 프로그램을 끝내야 하면 종료 코드."""
        b, st = self.bench, self.state
        item, pin = st['item'], str(st['pin'])
        st.update({'name': b.name, 'temp': b.temp, 'out_type': b.out_type})
        self.remember()
        b.dut_pin = pin
        try:
            self.echo('\n[0/3] 스코프 상태 정리')
            b.reset()
        except (ScopeError, OSError) as e:
            self.echo(f'[중단] {e}')
            return 1
        b.out_pin = str(st.get('out_pin', '')) if item == '1' else ''
        try:
            if item == '4':
                rec = b.snap(st['snap_label'], self.note)
            elif item == '3':
                rec = b.save_manual(st['manual_label'], self._chans(), self.note)
            else:
                self.echo('\n[1/3] 측정')
                rec = b.vih(int(st['in']), int(st['out'])) if item == '1' else b.voh([int(st['voh'])])
                if cap:
                    self.echo('[2/3] 화면 캡처')
                    b.capture()
                else:
                    self.echo('[2/3] 화면 캡처 생략')
                self.echo('[3/3] 파형·설정 저장')
                b.save()
        except KeyboardInterrupt:
            self.echo('\n[중단] 측정을 취소했습니다. 일부 파일만 저장됐을 수 있습니다.')
            return 130
        except (ScopeError, OSError, ValueError, KeyError) as e:
            self.echo(f'[중단] {e}\n완료되지 않았습니다. TekScope 화면 상태와 logs 폴더를 확인해주세요.')
            return 1
        self.echo(f'\n{"캡처" if item == "4" else "측정"} 완료 — DUT 핀 {pin}\n저장 위치: {rec.folder}'
                  + ('\n(화면 이미지는 종료할 때 스코프에서 가져옵니다)' if b.pending_count() else ''))
        return None

    def valid_channels(self, value):
        parts = value.replace(',', ' ').split()
        return bool(parts) and all(p.isascii() and p.isdigit() and 1 <= int(p) <= self.bench.scope.num_channels for p in parts)

    @staticmethod
    def valid_temp(value):
        try:
            import math
            return math.isfinite(float(value))
        except ValueError:
            return False
