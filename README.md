# scope_tech — MSO58B VIL/VIH/VOH/VOL 측정 도우미

Tektronix MSO58B로 PLD(SLG4AD4449) 입출력 레벨을 측정하고, 명령 하나로 아래 3가지를 수행합니다.

1. **측정/커서 생성**: VIL/VIH는 커서, VOH/VOL은 측정과 통계
2. **화면 캡처**: 측정/커서가 보이는 `meas.png`, 숨긴 `clean.png` 두 장
3. **파형 저장**: `waveform.csv`(분석용), `CHx.wfm`(스코프·TekScope에서 다시 열기용), `setup.set`(스코프 설정)

- **필요한 것:** Windows와 Python 3.13만 있으면 됩니다. 표준 라이브러리만 쓰므로 `pip install`이 필요 없습니다.
- **오프라인 PC 사용:** 이 폴더를 통째로 USB 메모리에 담아 복사하면 됩니다.

---

## 1. 연결 (둘 중 하나)

| | USB | LAN (설치 불필요, 권장) |
|---|---|---|
| PC 설치 | **TekVISA 또는 NI-VISA 필요**¹ | 없음 |
| 케이블 | 스코프 뒷면 **USB Device 포트**(USB 3.0 B타입) | 랜선으로 PC와 스코프를 직결 |
| 스코프 설정 | Utility > I/O > USB Device Port **On** | Utility > I/O > LAN: IP 수동 지정(예: `192.168.0.10`)<br>Utility > I/O > Socket Server **On**, Protocol **None**, Port **4000** |
| PC 설정 | — | 이더넷 어댑터 IP를 `192.168.0.100`, 서브넷 `255.255.255.0`으로 지정 |
| `config.ini` | `interface = usb` | `interface = lan`, `ip = 192.168.0.10` |

¹ Windows에는 USB 계측기(USBTMC) 드라이버가 기본으로 들어 있지 않습니다. 그래서 USB로 연결하려면 VISA가 꼭 필요합니다. 인터넷이 되는 PC에서 TekVISA 설치 파일을 받아 옮겨 설치하세요. 설치가 어려우면 LAN을 쓰면 됩니다.

## 2. 실행

- `run_scope.bat`을 더블클릭하거나 `py -3.13 scope.py`를 실행하면 **안내형 메뉴**가 시작됩니다.
- **DUT 핀 번호 → VIL/VIH 또는 VOH/VOL → 스코프 채널 → 준비 후 Enter** 순서입니다. Enter 한 번으로 측정·캡처·파형 저장을 진행합니다.
- VIL/VIH는 입력/출력 채널을, VOH/VOL은 출력 채널과 PP/OD를 선택합니다. 핀 번호는 스코프 채널과 별개이며 폴더의 `PIN12_`와 `result.json`의 `dut_pin`에 기록됩니다.
- 준비 화면에서 `s`를 누르면 시험 이름·온도를 바꿀 수 있습니다. 완료 후 같은 조건 재측정 / 다른 핀·항목 / 종료를 선택합니다. 입력 중 `q`로 종료할 수 있습니다.
- 직전 핀·채널·시험 이름·온도는 다음 실행의 기본값으로 기억합니다. Enter로 유지합니다. 실장비와 시뮬레이터 설정은 따로 기억하며, `--name`, `--temp`, `--type` 지정값이 우선합니다.
- 오류가 나면 완료로 표시하지 않고 종료합니다. 장비 화면과 로그를 확인해주세요.
- **종료 방법:** 메뉴에서 `q` 또는 완료 후 `3`을 선택하세요. 진행 중 취소는 Ctrl+C 한 번을 누르고 정리 후 종료될 때까지 기다리세요. Windows Ctrl+Break도 취소로 처리합니다. VISA 응답 대기 중에는 취소 반영이 지연될 수 있습니다.
- 측정 중 취소/오류는 획득 정지와 필요한 AOE 해제를 요청한 뒤 통신을 닫습니다. 일부 파일은 미완성일 수 있으며, 정리 요청이 TekScope UI 복구를 보장하지는 않습니다.
- **창 X / 작업 관리자 강제 종료 / 전원 차단은 정리 실행을 보장하지 못합니다.** 측정·저장 중에는 이 방법으로 종료하지 마세요. 강제 종료 후 TekScope 상태를 확인하고, UI가 멈췄거나 상태가 불명확하면 TekScope를 재시작한 뒤 다시 연결하세요.
- 기존 명령어 셸은 `py -3.13 scope.py --shell`로 실행합니다. 아래 명령어 예시는 이 셸 기준입니다.
- **연습:** `py scope.py --sim`으로 스코프 없이 시뮬레이터로 모든 명령을 해볼 수 있습니다. 결과는 `captures_sim/`에 저장됩니다.
- **한 번만 실행:** `py scope.py --temp 85 --name INV_3V3 all vih 1 2`처럼 명령 하나만 실행하고 끝낼 수도 있습니다.

### 사전 점검 도구 (먼저 실행)
`run_check.bat`(또는 `py scope_check.py`)은 통신, 임시 폴더, 설정 파일 전송, 화면 캡처 저장/전송, STOP 후 캡처, 파형 전송, 측정/커서 명령을 **한 단계씩** 실행하고 걸린 시간과 응답을 기록합니다.
- 단계마다 Enter=진행, s=건너뛰기, q=끝내기. 특정 단계부터: `py scope_check.py --from 5`
- 결과는 `logs/check_날짜_시각.txt` 와 `logs/scpi_날짜.log` — 문제가 생기면 이 두 파일을 보내주세요.

### 처음 연결할 때 점검 순서
이 프로그램은 시뮬레이터로만 검증된 상태라, 실제 장비에서는 한 단계씩 확인하는 것을 권장합니다.
```
status          <- 연결/모델 확인
vih 1 2 hold    <- 지금 멈춰 있는 화면으로 분석 + 커서
capture         <- meas.png / clean.png
save 1 2        <- CSV + wfm + setup
```
오류가 나면 `logs/scpi_날짜.log` 파일을 보내주세요. 어떤 명령에서 막혔는지 바로 알 수 있습니다.

### 캡처 파일은 종료할 때 한 번에 가져옵니다
실장비(트리거 250Hz)에서 캡처 직후 PNG 를 PC 로 읽으면 TekScope 가 멈춘 사례가 있어, 기본값(`[capture] image_fetch = end`)은
**측정 중에는 스코프에 저장만** 하고 안내형 메뉴를 **종료할 때 한 번에** 가져옵니다. 셸에서는 `fetch` 명령.
- 가져오지 못한 파일은 `captures/_pending_images.json` 에 남습니다. TekScope 재시작 후 `py -3.13 scope.py --fetch` 로 다시 가져오세요.
- 캡처 직후 바로 가져오던 이전 방식은 `image_fetch = now`.

### 동작 순서 시험 (문제 재현·디버깅용)
`run_seq_test.bat` (또는 `py -3.13 seq_test.py`) — 측정 프로그램과 같은 코드로 **상태정리 → VIL/VIH 측정 → meas 캡처 → clean 캡처 → 저장** 을 3회 반복합니다.
- 옵션: `--repeat 5`, `--in 1 --out 2`, `--only capture`(캡처만 반복), `--save-wait 10`(캡처 대기 임시 변경)
- **대기 시간 줄이기 시험:** `--sweep` — 캡처 대기 시간을 config 값의 1 → 0.7 → 0.5 → 0.35 → 0.2 → 0.1 → 0 배로 줄이며 단계마다 `--repeat` 회 캡처. 처음 실패한 단계에서 멈추고, 마지막 통과값에 1.5배 여유를 둔 권장 `config.ini` 값을 출력합니다. 멈추면 TekScope 재시작 후 `--start-level 0.35` 처럼 이어서. 한 항목만: `--sweep-param save` (arm/save/post).
- **파일 이름 시각 분석:** 가져오기 단계 뒤에 캡처마다 `Single 시작 -> 트리거 -> AOE 해제` 간격을 출력합니다(스코프 시계를 TIME? 으로 맞춰 계산). 파일 이름 시각은 트리거 시각이라 저장 완료 시간은 알 수 없습니다(저장 완료는 scope_check 에서 약 0.7초로 확인).
- 단계별 소요 시간과 모든 SCPI 명령의 시각·간격을 `logs/seqtest_날짜_시각.txt` 에 기록합니다. 실패하면 **직전 SCPI 기록과 스코프 상태**를 출력하고 멈춥니다. 마지막 줄이 멈춘 명령입니다.

### 데이터·화면 저장만 (R_pd / R_pu / IOH 등)
기본 실행 메뉴에서 **3**을 선택합니다. DUT 핀 → 항목 이름 → 저장 채널(예: `1,2`) → 조건 메모 → 준비 후 Enter 순서입니다.

- 자동 VIL/VIH·VOH/VOL 측정이나 저항/전류 계산 없이, 선택 채널의 `waveform.csv`, 기존 측정·커서를 표시한 `screen.png`, 조건을 담은 `result.json`을 저장합니다.
- 저장 위치는 `captures/MANUAL/PIN번호_시험이름_온도_채널_시각_항목/`입니다. 매번 별도 폴더를 만들며 WFM/SET은 이 메뉴에서 저장하지 않습니다.
- 저항·전류·전원 등 조건은 메모에 직접 적습니다. 예: `외부저항 1 Mohm, VDD 3.3 V`.
- CSV는 현재 파형을 정지한 뒤 저장하고, 화면은 AOE 방식으로 추가 Single 획득합니다. 같은 획득을 담는 것은 아니므로 입력 조건을 유지해주세요. DC 신호는 Auto 트리거를 준비해주세요.
- `result.json`의 status가 complete일 때만 전체 저장 완료입니다. 실패 시 일부 파일과 incomplete 상태가 남을 수 있습니다.

### 화면만 캡처 (커서)
기본 실행 메뉴에서 **4**를 선택합니다. DUT 핀 → 캡처 이름 → 메모 → 준비 후 Enter. 셸에서는 `snap [이름]`.

- 파형 데이터(CURVe?)·측정·설정 파일 없이, 커서를 켜고 지금 화면만 `screen.png` 로 저장합니다. 커서 위치는 스코프에 맞춰 둔 그대로입니다.
- 저장 위치: `captures/SNAP/PIN번호_시험이름_온도_시각_이름/` (`screen.png`, `result.json`). 이미지는 종료할 때 한 번에 가져옵니다.
- 화면 저장은 Act On Event 방식이라 Single 1회 획득이 일어납니다. 신호와 트리거가 들어오는 상태여야 합니다.

## 3. 사용 예
```
[25C TEST PP spec=-] scope> temp 25
[25C TEST PP spec=-] scope> name INV_3V3
[25C INV_3V3 PP spec=-] scope> spec 3V3
[25C INV_3V3 PP spec=3V3] scope> all vih 1 2      <- VIL/VIH: 측정 -> 캡처 -> 저장
[25C INV_3V3 PP spec=3V3] scope> all voh 2        <- VOH/VOL: 측정 -> 캡처 -> 저장
[25C INV_3V3 PP spec=3V3] scope> temp 85          <- 온도만 바꿔서 반복
```

### 명령어
| 명령 | 내용 |
|---|---|
| `vih [입력 출력] [hold]` | VIL/VIH 측정. 기본 `1 2`. Single 획득 후 분석하고, 입력 채널에 커서 A(VT+) / B(VT−)를 놓습니다. `hold`를 붙이면 새로 획득하지 않고 지금 화면으로 분석합니다. |
| `voh [채널...] [hold]` | VOH/VOL 측정. 기본 `2`. 채널마다 TOP, BASE, MAX, MIN 측정을 추가하고, N회 획득 통계를 냅니다. 첫 채널의 TOP/BASE 위치에 수평 커서를 놓습니다. |
| `capture` | 측정/커서가 있으면 `meas.png`와 `clean.png`, 없으면 `clean.png`만 저장합니다. |
| `save [채널...]` | 파형과 설정을 저장합니다. 채널을 생략하면 화면에 켜진 채널 전부를 저장합니다. |
| `all vih 1 2` / `all voh 2` | 측정 → 캡처 → 저장을 한 번에 실행합니다. |
| `temp`, `name`, `spec`, `type pp\|od` | 시험 조건을 설정합니다. 폴더 이름과 판정에 반영됩니다. |
| `status`, `run`, `stop`, `single`, `clear`, `idn` | 상태 확인과 스코프 직접 제어 |
| `scpi <명령>` | SCPI 명령을 직접 보냅니다. 예: `scpi ACQuire:MODe?` |
| `help <명령>`, `quit` | 도움말, 종료 |

> 측정 명령을 실행하면 스코프에 있던 기존 측정이 지워집니다. 프로그램이 만든 측정만 지우게 하려면 `config.ini`에서 `clear_existing_measurements = no`로 바꾸세요.

## 4. 측정 원리

### VIL / VIH (CH1 = 함수발생기 입력, CH2 = PLD 출력)
입력에 느린 삼각파를 넣고, **출력이 바뀌는 순간(출력 50% 지점)의 입력 전압**을 읽습니다.

| 결과 | 의미 | 판정 |
|---|---|---|
| VT+ | 입력이 **올라가다가** 출력이 바뀐 전압 (VIH 쪽 실제 문턱) | `max(VT+) ≤ VIH(min)` |
| VT− | 입력이 **내려가다가** 출력이 바뀐 전압 (VIL 쪽 실제 문턱) | `min(VT−) ≥ VIL(max)` |
| 히스테리시스 | VT+ − VT− | — |

- 출력이 입력과 같은 방향으로 움직이는지(버퍼/AND/레벨시프터), 반대인지(인버터)는 자동으로 판별합니다. 1V 비교기도 같은 방식으로 문턱과 히스테리시스를 측정합니다.
- 화면에 3주기가 들어 있으면 상승 3회, 하강 3회를 측정해 최소/평균/최대를 냅니다.
- 문턱 근처에서 출력이 여러 번 바뀌면(떨림) 각 구간의 최악 전환(VT+는 가장 높은 값, VT−는 가장 낮은 값)을 쓰고 경고를 표시합니다.
- 측정할 때 획득 모드를 **High Res**로 바꿉니다(노이즈 감소, 최대 16bit).
- **문턱은 출력 반전 기준입니다.** 입력에 고정 문턱을 두지 않고, 출력의 High/Low 를 자동으로 찾아 출력이 바뀌는 순간(진폭의 `out_cross`, 기본 50%)의 입력 전압을 읽습니다.
- **같은 파형에서 출력 VOH/VOL 도 기록합니다.** 출력 High 구간들/Low 구간들의 레벨(구간 가운데 50% 평균)로 VOH(최악=가장 낮은 High), VOL(최악=가장 높은 Low)을 판정합니다. OD 출력은 VOH 판정 제외. 스코프 화면(meas.png)에도 출력 채널 TOP/BASE 측정이 표시됩니다(`vih_output_meas`).
- 안내형 메뉴 1번은 **입력 핀, 출력 핀, 출력 타입(PP/OD)** 을 묻고, 폴더 이름은 `PIN입력_OUT출력_...` 입니다.

**세팅 팁**
- **삼각파:** 0V → VCCIO(3.3V 또는 1.8V)까지만 넣으세요. **절대 최대 정격을 넘으면 안 됩니다.** 주파수는 100Hz~1kHz 정도가 적당합니다.
- **화면:** time/div를 3주기가 들어오게 맞춥니다(100Hz면 3ms/div). V/div는 파형이 화면 세로를 꽉 채우게 맞춥니다.
- **레코드 길이:** 10만~100만 포인트를 권장합니다. 100만 포인트 7채널이면 CSV가 약 60MB입니다.
- **OD 출력:** 상승이 느리면 `config.ini`의 `out_cross`를 0.1~0.2로 낮추세요. 출력이 실제로 바뀌기 시작한 지점 기준으로 계산합니다.

### VOH / VOL
- MSO5B에서 `HIGH`/`LOW`는 아이 다이어그램 전용 측정이라 **TOP(=VOH)과 BASE(=VOL)**를 씁니다. 둘 다 오버슈트를 뺀 안정 레벨(최빈값)입니다.
- N회(기본 50회) 획득 통계로 최악값을 냅니다. VOH는 TOP의 최솟값, VOL은 BASE의 최댓값입니다.
- MAX/MIN(오버슈트 포함 극값)은 참고용으로 같이 기록합니다.
- **OD 출력**(`type od`)은 High 레벨이 풀업(1.8V, 1kΩ) 전압이므로 **VOL만 판정**합니다.

## 5. 저장 구조
```
captures/
  results.csv                         <- 모든 측정 누적(엑셀로 열기)
  summary.csv                         <- analyze.py 재판정 결과
  VIH/INV_3V3_25C_CH1-CH2_20261017_153012/
    meas.png  clean.png               <- 화면 캡처(측정/커서 표시 / 숨김)
    waveform.csv                      <- time_s, CH1_V, CH2_V, ... (화면에 켜진 채널)
    CH1.wfm ... CH7.wfm  setup.set    <- 스코프에서 Recall 가능
    result.json                       <- 측정값, 전환 시점, 경고 등 전체 결과
    xy.svg                            <- (analyze.py --plot) 입력-출력 전달 특성
  VOH/...
logs/scpi_20261017.log                <- 스코프 송수신 기록(문제 확인용)
```

## 6. 판정 기준 넣기 / 나중에 다시 판정
1. `config.ini`의 `[spec:3V3]`, `[spec:1V8]`, `[spec:CMP1V]`에 datasheet 값을 채웁니다. 비워두면 판정 없이 측정값만 기록합니다.
2. `py analyze.py`를 실행하면 저장된 모든 결과를 현재 기준으로 재판정해서 `captures/summary.csv`를 만듭니다.

추가 옵션:
- `py analyze.py --plot`: VIH 폴더마다 `xy.svg`를 생성합니다(입력 X, 출력 Y. 히스테리시스 루프와 기준선이 보입니다).
- `py analyze.py --recompute`: `vin_avg_points`나 `out_cross`를 바꾼 뒤, 저장된 CSV로 VT+/VT−를 다시 계산합니다.
- `py analyze.py --spec 1V8`: 결과에 기록된 기준 대신 지정한 기준으로 판정합니다.

## 보고서 초안 만들기 (Markdown)
`run_report.bat` (또는 `py report.py`) → `captures/report_날짜_시각.md`. 스코프 연결 없이 저장된 결과만 읽습니다.

- **핀별 요약:** 핀마다 VIL(VT-) / VIH(VT+) / 히스테리시스 / VOL / VOH 의 전 조건 최악값과 판정.
- **핀별 상세:** 조건(시험·온도·출력 타입)별 표, 측정마다 typ·최악값·n·기준·판정 표와 화면 캡처(meas/clean/screen, `xy.svg` 가 있으면 함께).
- VIL/VIH 측정의 출력 VOH/VOL 은 **출력 핀** 아래에 들어갑니다(캡처는 링크). 메뉴 3·4 저장은 해당 핀 아래 캡처로 들어갑니다.
- DUT·목적·메모 칸은 `(작성)` 으로 비워 둡니다. 그대로 편집해서 쓰세요.
- 골라 만들기: `run_report.bat --temp 85`, `--name INV_3V3`, `--spec 3V3`(판정 기준 바꿔 재판정), `--out 경로.md`.
- 이미지는 보고서 파일 위치 기준 상대 경로로 링크합니다. 보고서와 captures 폴더를 함께 옮기면 그대로 열립니다(VS Code, Typora 등).

## tm_devices 비교 시험 (선택)
Tektronix 공식 Python 라이브러리 `tm_devices` 로 같은 스코프를 시험합니다. 본 프로그램은 표준 라이브러리만 쓰며 이 시험에만 필요합니다.
1. 인터넷 PC: `get_tm_devices.bat` → `tm_devices_wheels` 폴더 생성(약 13MB, Python 아무 버전이나 있으면 됨)
2. 그 폴더를 측정 PC 의 scope_tech 폴더에 복사 → `install_tm_devices.bat`
3. `run_tmd_test.bat` (캡처 횟수: `run_tmd_test.bat --shots 10`) → `logs/tmd_test_*.txt` 업로드

시험 내용: 연결(*IDN?, 스코프 설정 초기화 안 함) → CH1 파형 받기 → tm_devices `save_screenshot()` 반복. 이 함수는 원격 `SAVe:IMAGe` 를 쓰므로 TekScope 가 멈출 수 있습니다(멈추면 재시작).

## 7. 문제 해결
| 증상 | 확인 |
|---|---|
| `VISA 라이브러리를 찾을 수 없습니다` | TekVISA/NI-VISA를 설치하거나 LAN으로 바꾸세요. |
| `USB 에서 Tektronix 스코프를 찾지 못했습니다` | 뒷면 USB Device 포트에 연결했는지, USB Device Port가 On인지 확인하세요. |
| `LAN 연결 실패` | IP 대역, Socket Server On, Protocol None을 확인하세요. |
| 프로그램을 강제 종료한 뒤 다음 실행이 멈춤/응답 없음 | 연결할 때마다 USB Device Clear 로 남은 통신을 자동으로 버립니다. 그래도 멈추면 순서대로: ① 작업 관리자에서 남은 `python.exe`/`py.exe` 종료 ② 스코프 뒷면 USB 케이블을 뽑고 10초 뒤 다시 연결 ③ 스코프 Utility > I/O > USB Device Port Off → On ④ TekScope 프로그램 재시작. |
| `트리거가 걸리지 않았습니다` | 트리거 설정을 확인하세요. `config.ini`의 `single_timeout_s`로 대기 시간을 늘릴 수 있습니다. |
| `… 중 스코프 오류: …` | 스코프가 거부한 명령입니다. `logs/` 파일을 보내주세요. |

## 개발자용
```
python -m unittest discover -s tests -t .
```
시뮬레이터(`scopetech/sim.py`)와 가짜 TCP 서버, 가짜 VISA DLL로 LAN/USB 전체 흐름을 테스트합니다. SCPI 명령은 Tektronix *4/5/6 Series MSO Programmer Manual* (077-1898-05) 기준입니다.

