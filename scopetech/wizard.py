"""핀 선택 → 준비 확인 → 측정/캡처/저장의 안내형 실행."""
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
        b = self.bench
        self.echo('\n측정 도우미 — Enter: 기본값 유지 / q: 종료')
        # CLI에서 전달한 값이 저장된 기본값보다 우선한다.
        for key, attr in (('name', 'name'), ('temp', 'temp'), ('out_type', 'out_type')):
            if key in self.state and attr not in getattr(b, 'explicit_options', set()):
                value = self.state[key]
                if isinstance(value, str) and (key != 'out_type' or value in ('PP', 'OD')):
                    setattr(b, attr, value)
        try:
            while True:
                pin = self.prompt('측정할 DUT 핀 번호', self.state.get('pin', ''),
                                  lambda s: s.isascii() and s.isdigit() and int(s) > 0)
                item = self.prompt('측정 항목: 1 VIL/VIH  2 VOH/VOL  3 데이터·화면 저장만', self.state.get('item', '1'),
                                   lambda s: s in ('1', '2', '3'))
                if item == '1':
                    cin = self.channel('in', '입력 신호 스코프 채널', b.cfg.get('defaults', 'vih_in'))
                    while True:
                        cout = self.channel('out', '출력 신호 스코프 채널', b.cfg.get('defaults', 'vih_out'))
                        if cout != cin:
                            break
                        self.echo('입력과 출력은 서로 다른 채널을 선택해주세요.')
                    self.state.update({'in': cin, 'out': cout})
                elif item == '2':
                    cout = self.channel('voh', '출력 신호 스코프 채널', '2')
                    b.out_type = self.prompt('출력 타입: PP / OD', b.out_type, lambda s: s.upper() in ('PP', 'OD')).upper()
                    self.state['voh'] = cout
                else:
                    label = self.prompt('시험 항목 이름 (R_pd / R_pu / IOH 등)', self.state.get('manual_label', 'R_pd'), lambda s: bool(s))
                    raw = self.prompt('저장할 채널 번호 (예: 1,2)', self.state.get('manual_channels', '1'), self.valid_channels)
                    chans = list(dict.fromkeys(int(x) for x in raw.replace(',', ' ').split()))
                    note = self.prompt('조건 메모 (저항·전원·부하 등, 생략 가능)', '', allow_quit=False)
                    self.state.update({'manual_label': label, 'manual_channels': raw})
                self.state.update({'pin': pin, 'item': item})
                while True:
                    title = {'1': 'VIL/VIH', '2': 'VOH/VOL', '3': '데이터·화면 저장만'}[item]
                    channels = ', '.join(f'CH{c}' for c in chans) if item == '3' else ((f'입력 CH{cin} / ' if item == '1' else '') + f'출력 CH{cout}')
                    self.echo(f'\nDUT 핀 {pin} | {title} | {channels}')
                    self.echo(f'시험: {b.name} / 온도: {b.temp}°C / 출력: {b.out_type} / 기준: {b.spec_name or "미지정"}')
                    if item == '3':
                        self.echo(f'항목: {label} / 메모: {note or "없음"}')
                        self.echo('스코프에서 원하는 측정·커서를 준비해주세요. 저항/전류 자동 계산은 하지 않습니다.')
                        self.echo('선택 채널의 현재 파형을 CSV로 저장한 뒤 화면을 추가 획득합니다. DC 신호는 Auto 트리거를 준비해주세요.')
                    else:
                        self.echo('삼각파 0~VCCIO와 트리거를 준비해주세요.' if item == '1' else
                                  'High/Low가 반복되는 출력 신호, 부하 조건과 트리거를 준비해주세요.')
                    self.echo('캡처 시 추가 획득이 발생하므로 측정 중 입력 조건을 유지해주세요.')
                    action = self.prompt('준비되면 Enter로 시작 (s: 시험 이름·온도 변경)', '', lambda s: s in ('', 's'))
                    if action == 's':
                        b.name = self.prompt('시험 이름', b.name, lambda s: bool(s))
                        b.temp = self.prompt('온도(°C)', b.temp, self.valid_temp)
                        continue
                    self.state.update({'name': b.name, 'temp': b.temp, 'out_type': b.out_type})
                    self.remember()
                    b.dut_pin = pin
                    try:
                        if item == '3':
                            rec = b.save_manual(label, chans, note)
                        else:
                            self.echo('\n[1/3] 측정')
                            rec = b.vih(cin, cout) if item == '1' else b.voh([cout])
                            self.echo('[2/3] 화면 캡처')
                            b.capture()
                            self.echo('[3/3] 파형·설정 저장')
                            b.save()
                    except KeyboardInterrupt:
                        self.echo('\n[중단] 측정을 취소했습니다. 일부 파일만 저장됐을 수 있습니다.')
                        return 130
                    except (ScopeError, OSError, ValueError, KeyError) as e:
                        self.echo(f'[중단] {e}\n완료되지 않았습니다. TekScope 화면 상태와 logs 폴더를 확인해주세요.')
                        return 1
                    self.echo(f'\n측정 완료 — DUT 핀 {pin}\n저장 위치: {rec.folder}')
                    next_action = self.prompt('1 같은 조건 재측정 / 2 다른 핀·항목 / 3 종료', '2', lambda s: s in ('1', '2', '3'))
                    if next_action == '3':
                        return 0
                    if next_action == '2':
                        break
        except (EOFError, KeyboardInterrupt):
            self.echo('\n측정을 종료합니다.')
            return 0

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
