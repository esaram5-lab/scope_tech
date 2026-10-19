# 인수인계 메모 (다음 작업자/AI 용)

## 목표
Tektronix **MSO58B** (FV 2.24.4, Windows 내장형, TPP1000 프로브, CH1~7 연결)로 PLD(SLG4AD4449, PP/OD 출력, 3.3V/1.8V, 인버터·AND·레벨시프터·1V 비교기) 의
**VIL/VIH, VOH/VOL** 을 측정. 사용자 지시 시 ① 측정/커서 생성 ② 화면 캡처(측정 표시/숨김 2장) ③ 파형 저장.
- PC: Windows, Python 3.13 **표준 라이브러리만**, 인터넷 없음, **USB 2.0** 연결(TekVISA/NI-VISA 설치됨, `visa64.dll` 을 ctypes 로 직접 호출)
- 스코프 교체/펌웨어 업데이트 **불가**
- VIL/VIH 방식: CH1 = 함수발생기 삼각파 입력, CH2 = PLD 출력. 출력 전환 시점의 입력 전압 → VT+(VIH측)/VT-(VIL측)
- 판정 기준(datasheet 값)은 아직 없음 → `config.ini` `[spec:*]` 비워둠, 나중에 `analyze.py` 로 재판정
- 사용자 선호: 한국어 응답, 짧고 명확하게

## 저장소 / PR
- 브랜치 `claude/dreamy-edison-55q1f3` → PR https://github.com/esaram5-lab/scope_tech/pull/1 (draft, base `main`, CI 없음)
- 사용자는 실험 로그를 브랜치에 `log`, `scpi` 파일로 커밋해서 공유함 (merge 전에 지울 것)

## 구성 (모두 완료)
| 파일 | 내용 |
|---|---|
| `scope.py` / `run_scope.bat` | 대화형 셸: `vih`, `voh`, `capture`, `save`, `all vih 1 2`, `temp/name/spec/type`, `scpi` 등. `--sim` 시뮬레이터 |
| `scopetech/transport.py` | LAN 소켓 / USB(VISA ctypes) 통신 |
| `scopetech/mso.py` | MSO5B SCPI 드라이버 (매뉴얼 077-1898-05 기준 검증) |
| `scopetech/analysis.py` | TOP/BASE 레벨, VT+/VT- 검출(극성 자동, 떨림 묶음) |
| `scopetech/workflow.py` | 측정→캡처→저장 흐름, 결과 폴더/`results.csv` |
| `scopetech/sim.py` | MSO58B 시뮬레이터(테스트용) |
| `analyze.py` | 재판정 `summary.csv`, `--plot` XY SVG, `--recompute` |
| `scope_check.py` / `run_check.bat` | 실장비 단계별 사전 점검(10단계), `logs/check_*.txt` |
| `capture_test.py` / `run_capture_test.bat` | 화면 캡처 원인 분리 시험(1~5번) |
| `tests/` | `python -m unittest discover -s tests -t .` → 15개 통과 (가짜 TCP/가짜 VISA 포함) |

## 실장비에서 확인된 사실 (중요)
1. **원격 `SAVe:IMAGe` 를 보내면 TekScope 화면이 멈춘다** (NORMal/INVErted, 폴더, *OPC? 유무 무관. 원격 SCPI 는 한동안 응답하다 파일 명령에서 막힘). 스코프에서 직접 저장은 정상. 복구는 TekScope 재시작.
2. **해결: Act On Event 캡처** (capture_test 5번, 정상 확인): `SAVEONEVent:FILEDest/FILEName/IMAGe:FILEFormat PNG` + `ACTONEVent:TRIGger:ACTION:SAVEIMAGe:STATE ON` + `ACTONEVent:ENable 1` → Single 1회 → `FILESystem:DIR?` 로 파일 찾기(이름에 타임스탬프가 붙음: `prefix_YYYYMMDDhhmmssmmm.png`) → `READFile` → 해제. **`mso.screenshot()` 이 이 방식으로 재작성됨 (최신 커밋 cb1889a), 실장비 미검증.**
3. USB 통신 정상: 1.25M 포인트 CURVe 0.1s, `READFile` 은 END 정상, **파일 뒤에 항상 `\n` 1바이트 추가** → 제거 처리함.
4. `FILESystem:MKDir` 이미 있으면 오류 257 (정상 처리). `FILESystem:CWD` 변경/`LDIR?` 는 사용 중단(멈춤 직후 명령이었음; 원인은 1번이었을 가능성 높음).
5. 기본 CWD `C:/Users/Public/Tektronix/TekScope`, HOME `C:/Users/Tek_Local_Admin`. `HIGH/LOW` 측정은 아이 다이어그램용 → VOH/VOL 은 `TOP/BASE` 사용.
6. 측정 추가/삭제·커서 명령은 정상 (각 묶음 뒤 `*OPC?` + 0.1s 대기 넣음). capture 의 clean.png 는 측정 삭제 대신 `MAINWindow:RRBDisplaystate OFF/ON`.

## 다음에 할 일 (우선순위)
### 2026-10-17 7단계 멈춤 후 수정
- 사용자 로그: 7단계 첫 반복에서 PNG 이름 발견 후 AOE 해제의 `*OPC?`가 10초 타임아웃. TekScope 화면도 멈췄으나 Windows는 정상. 복구 후 `*IDN?` 응답은 UI 복구 증거가 아님.
- 원인 후보: RUN 중 AOE를 켜서 Single 전환 전 여러 저장이 시작되는 경쟁 상태(미확정).
- `mso.screenshot()`와 점검 도구 모두 AOE 설정 전에 STOP/획득 정지/BUSY 해제를 확인하도록 변경. 정지 또는 BUSY 확인 실패 시 AOE 활성화 금지.
- 점검 도구는 오류 후 SCPI 복구 여부와 관계없이 중단. `--from 7`에서도 현재 폴더를 조회하도록 수정.
- 다음 실장비 시험: TekScope 재시작 후 `py -3.13 scope_check.py --from 7`. 우선 7단계 5회 반복을 확인하고 다음 단계 질문에서 q로 종료, check/scpi 로그 공유. 실장비 해결 여부는 아직 미검증.

1. **실장비 확인**: 최신 브랜치로 `run_check.bat` 1~10단계 (5·6·7·10단계가 AOE 캡처) → 통과 시 `scope.py` 에서 `all vih 1 2`, `all voh 2`.
2. **미검증: `SAVe:WAVEform`(.wfm 저장)과 `SAVe:SETUp`**. SETUp 은 정상 확인됨. WAVEform 이 멈추면 `config.ini [save] wfm = no` 로 끄거나 AOE `ACTONEVent:TRIGger:ACTION:SAVEWAVEform:STATE` 방식으로 교체.
3. VIL/VIH 실측: 함수발생기 High-Z, 삼각파 0~VCCIO, 100Hz~1kHz, 3주기 화면. 커서 위치/값이 프로그램 출력과 일치하는지 확인.
4. 코드 리뷰(멀티 에이전트)에서 확인된 미수정 항목 — 우선 반영 권장:
   - 떨림 시 VT+ 는 그룹 내 **최대**, VT- 는 **최소** 전환을 대표값으로(현재 첫 전환 → 낙관적 판정) — `analysis.py`
   - 극성 unknown 이면 판정 N/A, 짧은 글리치를 전환으로 세지 않게 최소 지속 샘플 조건 — `analysis.py`
   - `results.csv` 가 엑셀에서 열려 있으면 PermissionError 로 `all` 이 중단 → try/except 후 경고 — `workflow._finish`, `analyze.py`
   - 같은 Record 에서 capture/save 반복 시 파일 덮어씀 → 번호 붙이기; temp/name 변경 시 `bench.record = None`
   - `waveform.csv` 를 `utf-8-sig` 로(엑셀 한글)
   - voh hold: STOP 을 측정 추가 전에, 결과 POPUlation≥1 까지 폴링
   - USB 자동검색이 Tek 함수발생기(0x0699)를 잡을 수 있음 → `*IDN?` 으로 MSO 확인
   - 소켓 OSError → ScopeError 변환, 셸 ValueError 분기에서도 `recover()`
   - 시작 시 잘못된 `--spec` 검증, `--temp` 정규화
   - `fetch_waveform` 은 이미 `DATa:STOP 2000000000` 로 수정됨
5. 판정 기준 값 입력(사용자에게 SLG4AD4449 datasheet 값 받기) → `analyze.py` 재판정.
6. merge 전: `log`, `scpi` 같은 사용자 업로드 파일 제거, PR 설명 업데이트, draft 해제는 사용자 확인 후.

## 참고
- 매뉴얼: https://download.tek.com/manual/4-5-6-Series-MSO_Programmer_077189805.pdf (`pdftotext` 후 grep 으로 명령 확인)
- 커밋 메시지 끝 attribution 은 세션 지침을 따를 것. 모델명은 커밋/PR 에 쓰지 말 것.


## 안내형 실행 추가
- 사용자 보고: AOE 수정 후 사전 점검 및 실제 측정/캡처/저장 정상 동작 확인.
- 기본 `scope.py` 실행은 `scopetech/wizard.py`의 핀 → 항목 → 채널 → 준비 Enter 안내형 메뉴. 기존 셸은 `--shell`, 단일 명령 인자는 그대로 지원.
- DUT 핀은 `Bench.dut_pin`, 결과 JSON `dut_pin`, 폴더 `PIN번호_`로 기록. VIL/VIH와 VOH/VOL은 각각 쌍으로 측정.
- 이전 설정은 .session.json(실장비)/.session_sim.json(연습)에 저장, git 제외. 시험 이름·온도는 준비 화면 s로 변경. 오류 시 종료.
- 새 안내형 메뉴의 실장비 사용자 테스트는 아직 필요.

## 종료 처리 보완
- AOE 활성화와 pace가 기존 try 바깥에 있어 중단 시 해제 누락 가능. 설정 전체를 정리 범위에 포함.
- 측정 중 Ctrl+C는 코드 130으로 반환, 메인 finally에서 정지/필요한 AOE 해제 요청 후 transport와 로그 닫음. 연결 초기화 실패도 닫음.
- 정리는 2초 통신 타임아웃으로 최선 노력, *OPC? 대기/재획득 없음. 성공 표시는 하지 않으며 UI 복구는 별도 확인.
- Windows 창 X/작업 관리자 강제 종료는 보장 불가. q/완료 메뉴 3 또는 Ctrl+C 후 종료 대기를 안내. 정확한 사용자 종료 방식은 아직 미확인.

## 수동 시험 저장 메뉴
- 기본 메뉴 3: R_pd/R_pu/IOH 등 자유 항목 이름, 다중 채널, 조건 메모 입력.
- Bench.save_manual: 자동 측정/판정 없이 현재 파형 CSV → 추가 획득 화면 screen.png 저장. 기존 측정/커서 유지.
- MANUAL/PIN..._항목 폴더와 result.json에 조건/완료 여부 기록, CSV와 화면은 동일 획득 아님. DC는 Auto 트리거 안내.

## 2026-10-18 리뷰 반영 (완료)
- `results.csv` 쓰기 실패(엑셀 열림) 시 중단하지 않고 `results_pending_*.csv` 로 저장, 판정 출력은 먼저 표시. `analyze.py` summary 쓰기 실패도 안내 후 종료.
- 떨림 구간 대표값을 최악값으로(VT+ 최대, VT- 최소). README 반영.
- 출력 극성 불명이면 VIH/VIL 판정 `N/A(극성불명)`.
- USB 자동검색: Tek 장비가 여러 대면 `*IDN?` 으로 MSO/DPO/LPD 를 선택, 못 고르면 목록과 함께 `usb_resource` 지정 안내.
- `waveform.csv` UTF-8 BOM. 안내형 메뉴 조건 메모에서 `q` 는 종료가 아닌 메모로 처리.
- 테스트 33개 통과. 사용자 업로드 `log` 파일은 사용자 요청으로 유지.

## 2026-10-19 VIL/VIH 측정에 출력 VOH/VOL 추가
- 사용자 요청: 문턱을 출력 반전 기준으로 → 기존 `find_thresholds` 가 이미 출력 전환(출력 진폭의 out_cross) 시점의 입력 전압을 사용함을 설명.
- 추가: 같은 파형의 출력 High/Low 구간(가운데 50% 평균)으로 VOH/VOL 계산(`ThresholdResult.out_highs/out_lows`, `results.vih_output_metrics`), 판정 포함(OD 는 VOH 제외).
- VIH 측정 시 출력 채널에 TOP/BASE 측정 배지 추가(`[measure] vih_output_meas`).
- 안내형 메뉴 1번: 입력 핀 → 항목 → 출력 핀 → 채널 → PP/OD. `Bench.out_pin`, 결과 `out_pin`, 폴더 `PIN입력_OUT출력_`.
- 테스트 33개 통과. 실장비 미검증.
