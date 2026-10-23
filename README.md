# scope_tech — MSO58B VIL/VIH/VOH/VOL 측정 도우미

Tektronix MSO58B 로 PLD(SLG4AD4449) 입출력 레벨을 측정하고, 시험 계획(`test_plan.csv`)대로 샘플·온도별 결과표를 만듭니다.
측정마다 측정/커서 생성 → 화면 캡처 → 파형 저장(`waveform.csv`, `CHx.wfm`, `setup.set`)을 자동으로 합니다.

- **필요한 것:** Windows 와 Python 3.13(python.org 설치본, tkinter 포함). 표준 라이브러리만 쓰므로 `pip install` 이 필요 없습니다.
- **오프라인 PC:** 이 폴더를 통째로 USB 메모리로 복사하면 됩니다.
- **연습:** `run_plan.bat --sim` / `py scope.py --sim` 은 스코프 없이 시뮬레이터로 실행합니다(결과는 `captures_sim/`).

| 실행 파일 | 하는 일 | 스코프 |
|---|---|---|
| `run_plan.bat` | **시험 계획대로 측정** (CLI) + 결과·설정 창(GUI) | 필요 |
| `run_plan_gui.bat` | 결과·설정 창만 열기 | 불필요 |
| `run_cleanup.bat` | 여러 버전의 측정 기록을 정리해 계획·요약표에 연결 | 불필요 |
| `run_plan_summary.bat` | 샘플별·전체 요약표 다시 만들기 | 불필요 |
| `run_scope.bat` / `run_nocap.bat` | 개별 측정 도우미 (캡처 포함 / 캡처 없이) | 필요 |
| `run_check.bat` | 처음 연결할 때 단계별 사전 점검 | 필요 |
| `run_report.bat`, `run_temp_table.bat`, `run_collect_photos.bat`, `run_analyze.bat` | 보고서 초안, 온도별 표, 사진 모음, 재판정 | 불필요 |

---

## 1. 연결

| | USB (기본) | LAN |
|---|---|---|
| PC 설치 | **TekVISA 또는 NI-VISA 필요**¹ | 없음 |
| 케이블 | 스코프 뒷면 **USB Device 포트**(USB 3.0 B타입) | 랜선으로 PC와 스코프 직결 |
| 스코프 설정 | Utility > I/O > USB Device Port **On** | Utility > I/O > LAN: IP 수동 지정(예: `192.168.0.10`), Socket Server **On**, Protocol **None**, Port **4000** |
| PC 설정 | — | 이더넷 어댑터 IP `192.168.0.100`, 서브넷 `255.255.255.0` |
| `config.ini` | `interface = usb` | `interface = lan`, `ip = 192.168.0.10` |

¹ Windows 에는 USB 계측기(USBTMC) 드라이버가 없어서 USB 로 연결하려면 VISA 가 꼭 필요합니다.

### 화면 캡처 서버 (기본 캡처 방식 `screen_api`)
측정 명령은 USB, 화면 캡처는 LAN 으로 스코프 Windows 에서 돌아가는 캡처 서버에서 받습니다(TekScope 에 화면 저장 명령을 보내지 않음).
서버·클라이언트는 `scope_capture/` 에 동봉되어 있습니다(scope-capture-api v1.2).
1. 스코프: `scope_capture/` 폴더를 복사 → 처음 한 번 `py -m pip install Pillow` → `run_capture_server.bat` (처음 실행 때 `capture_token.txt` 생성). 방화벽 등: `scope_capture/README_KO.md`.
2. PC: 스코프의 `capture_token.txt` 를 `scope.py` 옆에 복사.
3. `config.ini`: `[screen_api] host = 스코프 IP` (비우면 자동 검색).
- 캡처 순서: 실행 중이면 Single 1회 받고 정지 → 서버가 화면이 멈출 때까지(최대 2초) 기다렸다 찍음. 2초 안에 멈추지 않으면 오류로 알립니다.
- 이전 방식(Act On Event, 스코프에 저장 후 종료할 때 한 번에 가져오기)으로 돌아가려면 `[capture] method = aoe`. 남은 캡처는 `py scope.py --fetch`.

### 사전 점검 (`run_check.bat`)
통신, 임시 폴더, 파일 전송, 화면 캡처, 파형 전송, 측정/커서 명령을 **한 단계씩** 실행하고 걸린 시간과 응답을 기록합니다.
단계마다 Enter=진행, s=건너뛰기, q=끝내기. 결과는 `logs/check_*.txt` 와 `logs/scpi_*.log` — 문제가 생기면 이 두 파일을 보내주세요.

---

## 2. 시험 계획대로 측정 (`run_plan.bat`)
`test_plan.csv` 의 19개 항목을 순서대로 측정하고 샘플·온도별 진행 기록과 문서용 요약표를 만듭니다. 계획·요약 순서 파일은 엑셀로 고칠 수 있습니다.

1. `run_plan.bat` → 온도(25 / 85 / −40) → 시험 이름(샘플). CLI 창과 함께 결과·설정 창(GUI)이 뜹니다.
2. 항목마다 준비 안내가 나옵니다. 배선이 바뀌는 묶음(VI → POR → IOH → Rpu → Rpd)이 시작될 때 연결 방법을 한 번 보여줍니다.
   ```
   [9/19] IOH  핀 12   채널 CH8, CH2
     준비: CH8 = 전류 프로브, CH2 = 핀 전압 2.4 V (부하)
   Enter: 측정 / k: 건너뛰기 / p: 이전 / g: 번호로 이동 / q: 멈춤(다음에 이어서)
   ```
3. 측정이 끝나면 결과 한 줄과 주의를 보여주고 다음 항목으로 갑니다. 실패하거나 요약표에 넣을 값이 안 나오면(예: Rpd 핀 전압이 VDD)
   `실패` 로 남기고 그 자리에서 Enter(다시) / k(건너뛰기). 이미 완료한 항목을 다시 재다 실패하면 앞의 결과를 그대로 둡니다.
4. 측정·저장이 끝나면 스코프를 Run 으로 되돌립니다(Stop 으로 두려면 `[measure] run_after = no`).
5. `q` 로 멈추면 다음에 같은 샘플·온도를 고를 때 남은 항목부터 이어서 합니다. 모두 끝나면 건너뛴 항목을 다시 할지 묻고, `g` 로 완료한 항목을 다시 잴 수 있습니다.

| 항목 | 측정 | 채널(기본 계획) | 결과 |
|---|---|---|---|
| VI / AI | VIL/VIH (+같은 파형의 출력 VOH/VOL) | CH1 입력, CH2 출력, 트리거 CH1 | 입력 핀 VIH/VIL, 출력 핀 VOH/VOL |
| POR | VDD 를 끈 채 Enter → VDD 켜기. POR 지연 중 Low = VOL, 그 뒤 High = VOH, 지연 시간 | CH1 VDD, CH2 출력, 트리거 CH1 상승 **Normal 모드** | 출력 핀 VOH/VOL |
| IOH / IOL | 측정마다 Single 1회, 전류 프로브 평균. 핀 전압이 `[plan] ioh_v`(2.4 V) ± 0.1 V 를 벗어나면 주의 | CH8 전류, CH2 핀, CH1 트리거 스윙(숨김) | mA |
| RPU / RPD | 측정마다 Single 1회, 외부 1 MΩ + TPP1000 10 MΩ 보정 (아래 계산식) | CH2 핀, CH1 트리거 스윙 | kΩ |

### 샘플 (fab × 번호)
같은 계획을 샘플마다 따로 진행합니다(예: TSMC 3개, VIS(뱅가드) 3개). **샘플끼리는 값을 섞지 않습니다.**
- 시험 이름이 샘플을 정합니다. `captures/plan/samples.csv`(시험이름, fab, 샘플)에 없는 이름은 이름에서 추정해 추가합니다
  (`TSMC` / `VIS`·`VANGUARD`·`뱅가드` + 마지막 숫자, 예: `TSMC_1` → TSMC_1, `VIS-2` → VIS_2). 틀리면 엑셀로 고칩니다.
  fab·번호가 같으면 이름이 달라도 같은 샘플입니다. fab 을 못 찾으면 시험 이름 자체가 샘플입니다.
- CLI 에서 시험 이름을 입력하면 “샘플 TSMC_1 (fab TSMC, 샘플 1)” 처럼 보여주고 그 샘플의 기록을 이어서 진행합니다.
  진행 중에 GUI 에서 다른 샘플의 이름으로 바꾸면 반영하지 않습니다(q 로 멈추고 다시 시작).

### 결과 (`captures/plan/`)
- `<샘플>/plan_summary_<샘플>.csv`: 문서에 옮길 요약표. 행 = `test_plan_summary.csv` 순서(구분, 핀, 항목, 단위), 열 = 25 / 85 / −40°C.
  한 샘플 안에서 같은 값이 두 측정에서 나오면(예: 핀 16 VOL 은 3→16, 6→16) 최악값(VIH 최대, VIL·VOH 최소, VOL 최대).
- `plan_summary_all.csv`: 모든 샘플을 나란히(열 = 샘플 × 온도, VIS → TSMC, 번호 순). 샘플끼리 최악값·평균은 내지 않습니다.
- `<샘플>/plan_progress_25C.csv`: 항목별 상태(완료/건너뜀/실패), 결과 한 줄, 주의, 측정 폴더.
- 요약표만 다시 만들기: `run_plan_summary.bat`.

### 결과·설정 창 (GUI)
- 측정은 CLI 에서만 합니다. GUI 는 결과 보기·설정 바꾸기·예전 측정 가져오기만 하고, 닫아도 측정은 계속됩니다.
- 따로 열기: `run_plan_gui.bat`. 띄우지 않기: `run_plan.bat --no-gui` 또는 `[plan] gui = no`. 창이 이미 열려 있으면 새로 띄우지 않습니다.
- 위쪽에서 **샘플·온도**를 고릅니다. “CLI 따라가기” 를 켜면 CLI 가 진행 중인 샘플·온도·항목을 자동으로 보여줍니다.
- **진행** 탭: 항목별 상태·결과·주의, CLI 가 기다리는 항목은 `▶`. 항목을 누르면 스코프 캡처 이미지, 측정값과 계산 근거, 폴더 열기.
  파형을 PC 에서 새로 그리지 않고 스코프 캡처만 보여줍니다.
- **요약표** 탭: 고른 샘플의 요약표(고른 온도의 칸이 빈 줄은 붉은 배경). 이 샘플 / 전체 샘플 요약표를 엑셀로 열기.
- **설정** 탭: 시험 이름, 화면 캡처 켬/끔, Single 대기 시간, VDD·외부 저항·프로브 저항, IOH/IOL 부하 조건, 출력 전환 기준, 입력 평균 점수.
  `저장` 하면 CLI 가 **다음 측정 직전**에 반영하고 “설정 변경 반영: VDD 3.3 -> 3.25 V” 처럼 알려줍니다(측정 중인 값은 안 바뀜).
  `config.ini` 는 그대로 두고 `captures/plan/overrides.ini` 에 저장합니다(`config.ini 값으로` 버튼으로 되돌림). 온도는 CLI 에서 정합니다.
  측정마다 쓴 설정은 `result.json` 의 `plan_settings` 에 남고, 다시 계산할 때도 그 값을 씁니다.
- **가져오기** 탭: 정리 도구와 같은 연결을 창에서. `미리보기` → 항목별 체크(☑ 가져옴 / ☐ 안 가져옴, 계획 모드로 잰 줄은 기본 ☐)
  → 항목을 누르면 같은 샘플의 다른 후보 기록과 캡처, `이 기록 사용` 으로 최신 대신 고르기 → `적용`. CLI 가 진행 중이면 적용을 막습니다.
- 오류는 `logs/plan_gui.log`. tkinter 가 없으면 “GUI 를 띄우지 못했습니다” 한 줄만 나오고 CLI 로 진행합니다
  (확인: `py -c "import tkinter; tkinter.Tk()"` 로 빈 창이 뜨면 정상).

---

## 3. 예전 측정 섞어 넣고 정리 (`run_cleanup.bat`)
버전이 바뀌면서 형식이 다른 측정 기록을 한 `captures` 에 모아 계획·요약표에 연결합니다.
1. 예전 `captures` 에서 측정 폴더(`VIH`, `MANUAL`, `POR`, `VOH`, `SNAP`, `MISC`)만 복사해 최신 `captures` 에 붙여 넣습니다(폴더 병합 예, 파일 덮어쓰기는 건너뛰기).
   `plan/`, `result/`, `results.csv`, `results_old_*.csv`, `_pending_images.json` 은 붙여 넣지 않습니다(정리 도구가 다시 만듦).
2. `run_cleanup.bat` → 미리보기: 버전별 기록 수, 샘플 묶음(시험 이름 → 샘플), 샘플·온도별로 연결할 기록, 연결 못 한 기록과 이유.
   `plan/samples.csv` 와 `plan/missing_pins.csv`(핀 정보 없는 예전 기록)는 미리보기 때 만들어집니다. 고치거나 채운 뒤 다시 실행하세요.
   (`missing_pins.csv`: VIL/VIH 는 입력핀 → 출력핀, 그 외는 출력핀 칸에 대상 핀)
3. `y` 로 적용:
   - 구버전 기록의 빠진 값(출력 핀, 출력 VOH/VOL, Rpd/Rpu/IOH 계산값)을 저장된 파형으로 채움(원본은 처음 한 번 `result.orig.json`)
   - 샘플·온도·항목마다 **같은 샘플 안의 가장 최근 기록**을 연결(그 기록으로 값이 안 나오면 그 전 기록, 미리보기에 표시)
   - 샘플별·전체 요약표, `results.csv`(이전 것은 `results_before_cleanup_*.csv`), 사진 모음(`result/`)을 다시 만듦. 내역은 `plan/cleanup_*.csv`
- 여러 번 실행해도 됩니다(새로 생긴 것만 반영). 계획 모드로 잰 줄은 그대로 두며 바꾸려면 `--replace`. `--dry-run` 미리보기만, `--apply` 묻지 않고 적용.
- 계획 진행(CLI)이 실행 중이면 하지 않습니다. 이전 버전의 `plan/progress_25C.json` 은 그 기록의 시험 이름 샘플 폴더로 옮깁니다.

---

## 4. 개별 측정 도우미 (`run_scope.bat`)
계획 밖의 측정용입니다. 현재 설정을 한 화면에 보여주고 **Enter = 핀 번호만 바꿔 바로 측정, `r` = 같은 설정으로 다시 측정, `s` = 설정 변경, `q` = 종료**.
```
──────────── 현재 설정 ────────────
 항목 : 1 VIL/VIH + 출력 VOH/VOL
 핀   : 입력 2 -> 출력 12
 채널 : 입력 CH1 / 출력 CH2
 출력 : PP
 시험 : INV_3V3 / 온도 25°C / 기준 미지정
 캡처 : 켬 (Single 1회 추가 획득)
──────────────────────────────────
```
| 항목 | 내용 | 저장 위치 |
|---|---|---|
| 1 VIL/VIH | 입력·출력 핀, 채널, PP/OD. 같은 파형의 출력 VOH/VOL 도 기록 | `captures/VIH/PIN입력_OUT출력_...` |
| 2 VOH/VOL | 출력 채널. TOP/BASE N회(기본 50) 통계 | `captures/VOH/...` |
| 3 데이터·화면 저장 | 지금 측정·커서는 그대로 두고 선택 채널 CSV + 화면. 항목 이름이 `R_pd`/`R_pu`/`IOH`/`IOL` 이면 값 계산 | `captures/MANUAL/..._항목` |
| 4 화면만 캡처 | 커서를 켜고 지금 화면만 | `captures/SNAP/..._이름` |

- 직전 핀·채널·시험 이름·온도는 다음 실행의 기본값으로 기억합니다(`--name`, `--temp`, `--type` 지정값이 우선).
- `run_nocap.bat`: 같은 메뉴지만 화면 캡처를 하지 않습니다(`scope.py --no-capture`, 항상 끄려면 `[capture] enabled = no`).
- 명령 셸: `py scope.py --shell` (`all vih 1 2`, `all voh 2`, `temp 85`, `capture`, `save`, `scpi <명령>`, `help`). 한 번만: `py scope.py --temp 85 --name INV all vih 1 2`.
- **종료:** `q`. 진행 중 취소는 Ctrl+C 한 번 누르고 정리될 때까지 기다리세요. 창 X·강제 종료는 정리를 보장하지 못하니 측정·저장 중에는 피하세요.
  강제 종료 뒤 TekScope 가 멈춰 있으면 TekScope 를 재시작하세요.

### R_pd / R_pu / IOH / IOL 계산
| 항목 | 회로 | 채널 | 계산 |
|---|---|---|---|
| R_pd | 외부 1 MΩ 핀→VDD | 핀 전압 | Rpd = 1/(1/(R_ext·V/(VDD−V)) − 1/R_probe) |
| R_pu | 외부 1 MΩ 핀→GND | 핀 전압 | Rpu = (R_ext‖R_probe)·(VDD−V)/V |
| IOH/IOL | 부하 | 전류 프로브(먼저), 핀 전압(선택) | 전류 평균, 그때 핀 전압 |
- TPP1000 입력 저항(10 MΩ)을 병렬로 보정합니다. 설정: `config.ini [manual]` `vdd_v = 3.3`, `r_ext_ohm = 1e6`, `probe_r_ohm = 10e6`.
- 핀 전압이 VDD 이상/0 이하이거나 결과가 프로브 저항보다 크면 값을 비우고 이유를 적습니다(연결 확인).

---

## 5. 측정 원리

### VIL / VIH (CH1 = 함수발생기 입력, CH2 = PLD 출력)
입력에 느린 삼각파를 넣고 **출력이 바뀌는 순간(출력 진폭의 `out_cross`, 기본 50%)의 입력 전압**을 읽습니다.

| 결과 | 의미 | 판정 |
|---|---|---|
| VT+ | 입력이 **올라가다가** 출력이 바뀐 전압 (VIH 쪽 실제 문턱) | `max(VT+) ≤ VIH(min)` |
| VT− | 입력이 **내려가다가** 출력이 바뀐 전압 (VIL 쪽 실제 문턱) | `min(VT−) ≥ VIL(max)` |
| 히스테리시스 | VT+ − VT− | — |

- 버퍼/AND/레벨시프터(같은 방향)와 인버터(반대)를 자동으로 판별합니다. 1V 비교기도 같은 방식입니다.
- 화면에 3주기가 있으면 상승·하강 3회씩 측정해 최소/평균/최대를 냅니다. 떨림이 있으면 최악 전환을 쓰고 경고합니다. 획득 모드는 High Res.
- 같은 파형에서 출력 VOH(최악 = 가장 낮은 High), VOL(최악 = 가장 높은 Low)도 기록합니다. OD 출력은 VOH 판정 제외.
- **세팅 팁:** 삼각파 0 V → VCCIO(3.3 / 1.8 V, 절대 최대 정격 넘지 않게), 100 Hz~1 kHz. time/div 는 3주기, V/div 는 화면을 꽉 채우게.
  레코드 10만~100만 포인트. OD 출력 상승이 느리면 `out_cross` 를 0.1~0.2 로.

### VOH / VOL
- MSO5B 의 `HIGH`/`LOW` 는 아이 다이어그램 전용이라 **TOP(=VOH)·BASE(=VOL)** 를 씁니다(오버슈트를 뺀 안정 레벨).
- N회 획득 통계로 최악값: VOH = TOP 최솟값, VOL = BASE 최댓값. MAX/MIN 은 참고용. OD 출력은 VOL 만 판정.

---

## 6. 결과 파일과 다른 도구
```
captures/
  plan/                               <- 계획 진행 (위 2·3절)
  results.csv                         <- 모든 측정 누적(엑셀)
  result/                             <- 사진 모음 '시험_온도C_PIN핀_항목.png'
  VIH/PIN2_OUT12_INV_3V3_25C_CH1-CH2_20261017_153012/
    meas.png  clean.png               <- 화면 캡처(측정/커서 표시 / 숨김)
    waveform.csv                      <- time_s, CH1_V, CH2_V, ... (화면에 켜진 채널)
    CH1.wfm ...  setup.set            <- 스코프에서 Recall 가능
    result.json                       <- 측정값, 전환 시점, 경고, 측정에 쓴 설정
logs/                                 <- 스코프 송수신 기록(scpi_*.log), GUI 오류
```
- **판정 기준:** `config.ini` 의 `[spec:3V3]`, `[spec:1V8]`, `[spec:CMP1V]` 에 datasheet 값을 채우면 판정합니다. 비워두면 측정값만 기록.
- **재판정 `run_analyze.bat`:** 저장된 결과를 현재 기준으로 재판정해 `captures/summary.csv`. `--recompute`(분석 설정 바꾼 뒤 다시 계산), `--spec 1V8`.
- **보고서 초안 `run_report.bat`:** 핀별 요약·상세와 화면 캡처를 담은 `captures/report_*.md`. `--temp 85`, `--name INV_3V3`.
- **온도별 표 `run_temp_table.bat`:** 계획 밖의 측정까지 행 = 핀·항목, 열 = 온도로 `captures/temp_table_*.csv`.
- **사진 모음 `run_collect_photos.bat`:** 측정 폴더의 사진을 `captures/result/` 에 같은 이름 규칙으로 복사(측정할 때도 자동 복사).
  VIL/VIH 사진은 입력 핀(`VIL-VIH`)과 출력 핀(`VOH-VOL`) 이름으로 두 번 복사합니다.

## 7. 문제 해결
| 증상 | 확인 |
|---|---|
| `VISA 라이브러리를 찾을 수 없습니다` | TekVISA/NI-VISA 를 설치하거나 LAN 으로 바꾸세요. |
| `USB 에서 Tektronix 스코프를 찾지 못했습니다` | 뒷면 USB Device 포트, USB Device Port On 을 확인하세요. |
| `LAN 연결 실패` | IP 대역, Socket Server On, Protocol None 을 확인하세요. |
| 강제 종료 뒤 다음 실행이 멈춤 | ① 남은 `python.exe`/`py.exe` 종료 ② 스코프 USB 케이블을 뽑고 10초 뒤 다시 연결 ③ USB Device Port Off → On ④ TekScope 재시작 |
| `트리거가 걸리지 않았습니다` | 트리거 설정을 확인하세요. `[measure] single_timeout_s` 로 대기 시간을 늘릴 수 있습니다. |
| 화면 캡처 오류 | 캡처 서버 실행·`capture_token.txt`·`[screen_api] host` 확인. 화면이 2초 안에 멈추지 않으면 획득이 실행 중인지 확인 |
| `… 중 스코프 오류: …` | 스코프가 거부한 명령입니다. `logs/` 파일을 보내주세요. |
| GUI 가 안 뜸 | `py -c "import tkinter; tkinter.Tk()"` 로 tkinter 확인, `logs/plan_gui.log` |

---

## 개발자용
### 구조 (모듈)
```
scope.py                 실행 (계획 / 안내 메뉴 / 셸 / --fetch / --no-capture / --sim)
plan_gui.py  cleanup.py  plan_summary.py  analyze.py  report.py  temp_table.py  collect_photos.py  scope_check.py
scopetech/
  transport.py           통신 (USB: visa64.dll 직접 호출, LAN: 소켓)
  mso.py                 MSO58B 드라이버 (SCPI 명령 단위)
  workflow.py            Bench: 시험 조건·현재 기록 폴더 등 공통 상태와 도구, 각 동작을 steps/ 에 맡김
  steps/                 동작 하나 = 모듈 하나, run(bench, ...): reset vih voh por capture save manual snap
  capture/               화면 캡처 '방법' (config [capture] method): screen_api(기본) aoe off template
  plan.py                시험 계획 진행(PlanRunner)·샘플별 진행 기록·요약표
  plan_import.py         기록 -> (샘플, 온도, 계획 줄) 연결, 빠진 값 채우기 (정리 도구·GUI 가져오기)
  samples.py             시험 이름 -> 샘플(fab·번호) 대응표 plan/samples.csv
  plan_state.py          CLI <-> GUI 연결 파일: plan/state.json(진행 상태), plan/overrides.ini(GUI 설정)
  plan_view.py           GUI 가 보여줄 데이터(tkinter 없이 시험 가능), GUI 띄우기
  plan_gui.py            결과·설정 창 (tkinter)
  wizard.py / shell.py   안내 메뉴 / 명령 셸 (Bench 메서드만 호출)
  analysis.py results.py recompute.py manual_calc.py gallery.py wavefile.py config.py sim.py
scope_capture/           동봉: 스코프 화면 캡처 서버·클라이언트 (scope-capture-api v1.2)
```
- `steps/capture.py` 는 **어떤 화면을 몇 장** 찍을지만 정하고, **어떻게 찍을지**는 `capture/` 의 방법이 정합니다.
- 새 동작: `steps/새동작.py` 에 `run(bench, ...)` 을 만들고 `Bench` 에 한 줄 메서드를 추가합니다.
- 캡처 방법 직접 만들기: `scopetech/capture/template.py` 를 `scope.py` 옆 `my_capture.py` 로 복사해 `take(self, local)` 구현 → `[capture] method = my_capture`.

### 테스트
```
python -m unittest discover -s tests -t .
xvfb-run -a python3 -m unittest tests.test_plan_gui     # 리눅스에서 GUI 시험(tkinter·화면 필요, 없으면 건너뜀)
```
시뮬레이터(`scopetech/sim.py`)와 가짜 TCP 서버, 가짜 VISA DLL 로 LAN/USB 전체 흐름을 테스트합니다.
SCPI 명령은 Tektronix *4/5/6 Series MSO Programmer Manual* (077-1898-05) 기준입니다.
