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
- results.csv 에 `pin` 열 추가: VIL/VIH 결과의 VT 행은 입력 핀, 출력 VOH/VOL 행은 출력 핀. 이전 형식 results.csv 는 `results_old_시각.csv` 로 옮기고 새로 시작.
- 메뉴 2(VOH/VOL만)는 50회 통계가 필요할 때만 사용. 메뉴 1 한 번으로 입력 VIL/VIH + 출력 VOH/VOL.

## 2026-10-19 VIL/VIH Single 단계 오류 대응 (사용자: 핀 2→12, 20→4 측정 시 Single 에서 오류, 로그 미수신)
- 추정 원인 1: 직전 변경으로 Single 전에 출력 TOP/BASE 측정을 추가 → 획득 중 측정 이벤트(2200번대: 클리핑/계산 중 등)가 ESR 오류 비트를 세워 `single()` 의 check 가 예외.
- 추정 원인 2: HiRes + 긴 레코드 처리 중 `ACQuire:STATE?` 응답 지연 → VISA 10초 읽기 타임아웃.
- 수정: 출력 측정 배지를 획득·파형 전송 후에 추가. `check()` 는 2200번대·400~500번대 이벤트를 경고(`scope.warnings`)로만 처리. Single 상태 폴링 질의 타임아웃을 max(single_timeout_s, 30)초로.
- 실제 원인 확정은 사용자 로그(오류 메시지 + logs/scpi_날짜.log) 필요.
- 사용자 로그(수정 전, 13:42) 분석: Single·파형 전송·커서는 정상. 화면 캡처(AOE)에서 PNG 가 DIR? 에 보인 0.5초 뒤 AOE 해제 → `*OPC?` 무응답(TekScope 멈춤). 2026-10-17 7단계 멈춤과 같은 패턴.
- 수정: AOE 는 켠 채로 BUSY 해제 + PNG 를 끝(IEND)까지 읽은 뒤에 해제(`_read_png_when_complete`), 해제 후 `*OPC?` 대기 30초. 점검 도구도 BUSY 해제 + 2초 뒤 해제.
- 참고: TekScope 기본 폴더에 예전 캡처 PNG(cap_/chk_/aoe_*)가 쌓여 있음. 실패 시 남은 파일이며 스코프에서 지워도 됨.
- 매 측정 전 상태 정리 추가(`[measure] reset_each_run`, `MSO5B.reset_state`, 안내형 메뉴 [0/3], 셸 `all`): 통신 버퍼 비우기(viClear)·*CLS·남은 AOE 해제·결과 바 ON·*IDN? 응답 확인(10초). *RST 는 사용자 스코프 설정을 지우므로 쓰지 않음. TekScope 가 이미 멈췄으면 측정 전에 "재시작 필요"로 중단.
- 2026-10-19 실제 환경(트리거 250Hz)에서 캡처 중 멈춤(사용자 보고, 로그 미수신). 캡처 단계별 대기 추가: AOE 켠 뒤 `aoe_arm_wait_s`(2), Single 후 질의 없이 `aoe_save_wait_s`(5), 파일 확인 후·AOE 해제 후 `aoe_post_wait_s`(3), DIR? 폴링 1초 간격. config [capture] 에서 조절. 테스트는 tests/__init__.py 에서 대기 0.
- `seq_test.py` / `run_seq_test.bat` 추가: 실제 코드(Bench/MSO5B)로 상태정리→VIL/VIH→meas 캡처→clean 캡처→저장을 N회 반복. 모든 SCPI 명령 시각/간격 기록, 실패 시 최근 60줄 + 스코프 상태 스냅샷(질의당 5초). `--only capture`, `--save-wait`, `--post-wait`.
- `seq_test.py --sweep`: 캡처 대기(arm/save/post)를 비율(기본 1,0.7,0.5,0.35,0.2,0.1,0)로 줄이며 단계당 --repeat 회 캡처, 첫 실패에서 중단, 마지막 통과값×1.5 권장값 출력. `--sweep-param`, `--levels`, `--start-level`.
- 2026-10-19 seq_test 스윕 로그: 대기 최대(2/5/3초)에서도 2회차 캡처에서 멈춤. PNG(158KB) 를 READFile 로 다 받은 직후 *OPC? 무응답 → 대기 시간 문제 아님. 사용자: PC 에서 그림 새로 그리기는 절대 불가, "나중에 한 번에 가져오기" 제안.
- 구현: `MSO5B.capture_deferred`(AOE 저장만, DIR?/READFile/DELEte 없음) + `fetch_images`(DIR? 1회, 파일 간 gap). `Bench._take` 가 `[capture] image_fetch=end`(기본)면 `captures/_pending_images.json` 에 기록, `fetch_pending()` 으로 일괄 가져오기. 안내형 메뉴 정상 종료 시 자동, 셸 `fetch`/quit, `scope.py --fetch`. seq_test 도 이 방식(반복 끝에 가져오기 단계). 실장비 미검증.
- 2026-10-19 16:47 seq_test `--only capture` 5회(대기 2/5/3초, 일괄 가져오기) 실장비 5/5 성공. 캡처 1회 15.4초 + 해제 후 3초. 일괄 가져오기 5개 12.2초 정상. 전체 순서(VIL/VIH→meas→clean→저장)는 아직 이 방식으로 미시험.
- 대기 단축 준비: `MSO5B.clock_offset()`(TIME? 초 경계로 스코프-PC 시계 차), `capture_deferred` 가 `last_capture`(single_at, disarm_at) 기록 → pending 항목에 저장, `fetch_images` 가 `fetched_names` 기록. seq_test 가져오기 뒤 "Single 시작 → 파일 이름 시각 → AOE 해제" 간격 출력. 파일 이름 시각이 저장 완료 시각인지 트리거 시각인지는 실측으로 판단(값이 ~0.1s 로 일정하면 트리거 시각). 다음: `run_seq_test.bat --sweep` 결과로 config 대기값 결정.
- 2026-10-19 16:52 scope_check 10단계 모두 통과(7단계 캡처+READFile 5회 포함). 캡처마다 "Single+저장 0.7s"(DIR? 에 파일 나타남, 끝까지 읽힘). 파일 이름 시각 - Single 시각이 scope_check·seq_test 모두 약 9.0초로 같음 → 두 시험의 저장 소요가 같고, scope_check 기준 0.7초 안에 저장 완료(스코프 시계가 PC 보다 약 8.3초 빠름).
- 그래서 기본 대기를 arm/save/post 2/5/3 → 1/2/1 로 줄임(Single 후 해제까지 9초 → 4초, 캡처 1회 약 18초 → 약 8초). 멈추면 config.ini 에서 2/5/3 으로 복귀. 실장비 확인 필요: `run_seq_test.bat --only capture --repeat 10`, 이후 전체 순서 `run_seq_test.bat --repeat 3`.
- 2026-10-19 사용자: `--only capture --repeat 10` 이 시작 직후 무한 대기, 직전 강제 종료한 USB 통신 탓으로 추정. `VisaTransport` 연결 직후 viClear(USBTMC Device Clear, 5초 제한)를 항상 수행해 남은 전송/응답을 버리도록 함. README 문제 해결에 수동 복구 순서 추가.
- 2026-10-19 19:34 사용자: seq_test 가 "capture 대기:" 줄 다음(=USB 연결 단계)에서 진행 안 됨. 콘솔에는 SCPI 가 안 보여 막힌 위치 불명. 조치: `scope._open_usb` 가 VisaTransport 를 별도 스레드에서 열고 단계(라이브러리/VISA 시작/장치 찾기/장치 열기/Device Clear)를 콘솔·로그에 출력, max(20, 2×timeout_s)초 안에 안 끝나면 멈춘 단계와 복구 순서를 출력하고 os._exit(3). `[connection] usb_clear`(기본 yes)로 연결 시 Device Clear 끌 수 있음.
- 2026-10-19 19:42 1/2/1 대기로 seq_test: 1회차 캡처 성공(7.6s), 2회차 상태정리에서 AOE 해제 1.3초 뒤 Device Clear + *CLS + AOE OFF 재전송 후 *OPC? 무응답(TekScope 화면은 정상, USB 만 끊김). 조치: post_wait 3 으로 복귀(1/2/3). `reset_state` 는 *OPC? 가 5초 안에 안 오면 그때만 Device Clear, AOE/결과 바 명령은 상태가 다를 때만 전송.
- 2026-10-19 19:51 1/2/3 대기로 seq_test --only capture 10/10 성공(캡처 11.6s, 가져오기 10개 22.5s). 파일 이름 시각 - Single = 0.03s → AOE 파일 이름 시각은 트리거 시각(저장 완료 아님). 저장 완료는 scope_check 의 DIR? 기준 약 0.7초가 유일한 근거. seq_test 분석 문구를 "트리거"로 정정.
- 11.6s 구성: settle 1.0 + arm 1.1 + Single 0.2 + save 2.0 + settle 1.0 + post(해제 전) 3.0 + 해제 0.1 + post(해제 후) 3.0. 제안(사용자 답 대기): post 를 해제 전/후로 분리, 해제 전은 줄이고 해제 후 3초 유지.
- 2026-10-19 사용자 요청으로 tm_devices(3.7.0) 비교 시험 추가: `tmd_test.py`/`run_tmd_test.bat`, 설치용 `get_tm_devices.bat`(인터넷 PC, pip download win_amd64/py3.13)·`install_tm_devices.bat`(오프라인, --no-index). DMConfigOptions(setup_cleanup/teardown_cleanup=False)로 *RST 방지. 참고: tm_devices `_save_screenshot` 은 `SAVE:IMAGE` + opc → READFILE → DELETE 로, 이 스코프에서 멈췄던 원격 SAVe:IMAGe 와 같은 방식.
- 2026-10-19 20:14 tm_devices 시험 결과: 연결·curve_query 정상, save_screenshot 1회차에서 실패. SAVE:IMAGE → *OPC? 0.15초에 1 → READFILE(20ms) → DELETE → *OPC? 45초 무응답. 우리 프로그램 초기 실패와 같은 패턴(이미지 저장 직후 파일 읽기/삭제). 공식 라이브러리도 같은 문제 → 스코프 펌웨어 쪽 원인으로 판단. 현재 방식(AOE 저장 + 측정 끝난 뒤 일괄 가져오기, 10/10 성공) 유지.
- 2026-10-19 사용자: LAN(TCPIP) 으로도 tm_devices 캡처가 똑같이 멈춤 → 통신 경로 문제 아님, TekScope 쪽 확정. 결정: USB 유지, 현재 대기(arm/save/post = 1/2/3, 10/10 성공) + 측정 끝난 뒤 일괄 가져오기 방식으로 확정. 남은 확인: 전체 순서 `run_seq_test.bat --repeat 3` 과 실제 메뉴 측정.
- 2026-10-19 사용자: 전체 순서 seq_test 를 대기 1/1/1 로 3/3 통과 → 기본값 1/1/1 로 확정(캡처 1회 약 6초). 메인 프로그램 구현 마무리. 남은 것: 실제 측정 사용 중 문제 생기면 로그로 대응(멈추면 1/2/3 → 2/5/3 순으로 늘림).
- 2026-10-20 사용자: 실사용 대기 1/2/2 로 결정(config 반영). 메뉴 3(데이터·화면 저장)에서 문제가 있어 보인다며 "데이터 없이 커서 켜고 지금 화면만 캡처" 요청 → `Bench.snap(label, note)` (CURSOR STATE ON → AOE 캡처 1장, CURVe?/측정/설정 저장 없음, `captures/SNAP/..._이름/screen.png`), 메뉴 4, 셸 `snap [이름]`. 테스트 37개 통과. 실장비 미검증.
- 2026-10-20 보고서 초안 생성기 `report.py` / `run_report.bat` 추가: captures/*/*/result.json → `captures/report_날짜_시각.md`. 핀별 요약(VIL=VT- 최솟값, VIH=VT+ 최댓값, Hys 최솟값, VOL 최댓값, VOH 최솟값, 판정), 핀별 조건 표, 측정별 상세 표 + 캡처 이미지(상대 경로). VIL/VIH 의 VOH/VOL 은 출력 핀 아래(이미지는 링크). config spec 으로 재판정, `--temp/--name/--spec/--out`. tests/test_report.py.
- 2026-10-20 사용자: "모든 문제는 화면 캡처에서 생기니 캡처 뺀 서브" 요청 → `run_nocap.bat` (`scope.py --no-capture`) + `[capture] enabled`(기본 yes). 끄면 Bench.capture 는 생략(result.json `capture: off`), save_manual 은 CSV 만, snap 은 ValueError, 메뉴 4 숨김, 셸 all 캡처 생략, 보고서는 "화면 캡처 안 함" 표시. .wfm/setup.set(스코프 파일 저장+READFile)은 그대로이며 `[save] wfm/setup = no` 로 끌 수 있음. 테스트 40개 통과.
- 참고: 메인 실행 파일 이름은 `run_scope.bat` (이전 대화에서 run.bat 이라 잘못 안내함).
- 2026-10-20 사용자: 매 측정마다 설정 확인 Enter 가 번거로움 → 안내형 메뉴를 "현재 설정 한 화면 표시 → Enter 바로 측정 / s 설정 변경(지금 값이 기본값) / q 종료"로 변경. 저장된 설정이 완전하면 질문 없이 설정 화면부터, 측정 후 다시 설정 화면으로. 이전의 "1 재측정/2 다른 핀/3 종료" 질문 제거. 메뉴 3·4 메모는 실행 중에만 유지. 테스트 41개 통과.
- 2026-10-20 설정 화면에 `p`(핀만 변경) 추가: 핀 번호만 묻고(VIL/VIH 는 입력→출력 핀) 설정 화면으로 복귀. 테스트 43개 통과.
- 2026-10-20 모듈화(사용자: 화면 캡처를 직접 구현해 보고 싶음): `scopetech/steps/`(reset/vih/voh/capture/save/manual/snap, 각 run(bench, ...)) 로 동작 분리, `Bench` 는 공통 상태·도구(new_record/base_result/finish/ensure_record/label_folder/prepare·hide·restore_overlay)와 위임만. 화면 캡처 방법은 `scopetech/capture/`(base 인터페이스 take/pending_count/fetch_pending, aoe 기본, off, template) 에서 `[capture] method` 로 선택(직접 만든 모듈은 scope.py 옆 파일도 가능, `모듈:클래스`). `enabled = no`/--no-capture 는 off. Bench 공개 메서드 이름 변경(_new_record→new_record 등), seq_test·테스트 반영. 테스트 47개 통과. 동작은 이전과 동일(시뮬레이터 확인), 실장비 미확인.
- 2026-10-20 `scopetech/capture/screen_api.py` 추가(`[capture] method = screen_api`, 설정 `[screen_api]`): 사용자 저장소 esaram5-lab/scope-capture-api 의 ScopeCaptureClient 로 스코프 Windows 화면을 LAN 으로 받음. TekScope 에는 AOE/SAVe:IMAGe/READFile 을 보내지 않음. take: acquire(stop 기본/single/keep) → *OPC? → settle_s → capture_png. 클라이언트는 client_dir → scope.py 옆 → scope-capture-api 폴더에서 찾음. 토큰·연결 실패는 ScopeError 로 안내. 실서버 코드(가짜 화면 grab) + 실클라이언트 종단 시험 통과, 테스트 51개. 실장비 미검증.
- 2026-10-21 scope-capture-api 서버 v1.2 (esaram5-lab/scope-capture-api, 브랜치 claude/dreamy-edison-55q1f3, 초안 PR esaram5-lab/scope-capture-api#1): `/capture?stable_ms=&interval_ms=&frames=` 화면이 멈출 때까지 찍기(안 멈추면 504), `--window TITLE` 창 영역만(못 찾으면 503+이유, 서버 콘솔 창 제외), `--require-foreground`, `--list-windows`, health `features`/`window`. 서버 테스트 26개.
- scope_tech `screen_api`: `[screen_api] stable_ms = 2000`(0 이면 끔), `stable_interval_ms = 100`, `stable_frames = 3`, `settle_s = 0.3`. 연결 시 health 로 stable 지원 확인·창 상태 표시. 클라이언트 모듈을 찾은 파일 그대로 로드하도록 수정(다른 폴더의 같은 이름 파일이 먼저 import 되던 문제). 시뮬레이터 + 실서버 코드 시험, 테스트 52개. 실장비 미검증.
- 2026-10-21 사용자: screen_api 실장비 정상 동작 확인 → 기본 캡처 방식을 `screen_api` 로 변경(config.ini, config.py 기본값, capture/__init__). 시뮬레이터 시험은 tests/__init__.py 에서 aoe 로 고정. 방식을 바꿔도 예전 aoe 미회수 캡처(_pending_images.json)는 Bench.pending_count/fetch_pending 이 aoe 로 가져옴. 테스트 53개.
