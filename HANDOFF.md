# 인수인계 메모 (다음 작업자/AI 용)

## 목표와 환경
- Tektronix **MSO58B** (FV 2.24.4, Windows 내장형, TPP1000 프로브)로 PLD **SLG4AD4449**(PP/OD 출력, 3.3 V/1.8 V)의
  VIL/VIH, VOH/VOL, POR, IOH, Rpu/Rpd 를 시험 계획(`test_plan.csv`)대로 측정하고 샘플·온도별 요약표를 만든다.
- 측정 PC: Windows, Python 3.13 **표준 라이브러리만**, 인터넷 없음, 스코프와 **USB 2.0**(TekVISA, `visa64.dll` 을 ctypes 로 직접 호출).
  화면 캡처만 LAN 으로 스코프 Windows 의 캡처 서버(`scope_capture/`, Pillow 필요)에서 받는다.
- 스코프 교체/펌웨어 업데이트 **불가**. 판정 기준(datasheet 값)은 사용자가 보안상 공유하지 않음 → `[spec:*]` 비워 둠.
- 사용자 선호: 한국어, 짧고 명확하게. **PC 에서 파형을 새로 그려 스코프 캡처를 대신하는 것은 절대 안 됨.**
  계획을 먼저 보여주고 "구현해줘" 를 받은 뒤 구현, 검증(멀티 에이전트 리뷰 등)은 하기 전에 계획을 확인받을 것.

## 시험 계획 (사용자 확정)
- `test_plan.csv` 19항목: VI 2→12, 20→4 PP / 3→16, 6→16, 5→19, 15→18 OD / AI 10→7 / POR 14 / IOH 12, 4, 14, 7 /
  RPU 5, 6, 15 / RPD 2, 3, 10, 20. 온도 25 → 85 → −40.
- 채널: VI/AI CH1 입력(삼각파)·CH2 출력, POR CH1 VDD·CH2 출력, IOH CH8 전류·CH2 핀(부하 2.4 V), Rp CH2 핀.
  IOH/Rp 중 CH1 은 트리거용 스윙(화면에서 숨김). IOH 때 입력은 모두 Low.
- 핀 10 = 아날로그 입력, 핀 14 = POR 후 약 2 ms 뒤 High(그 전 VOL 측정), 칩 내부 Rp ≈ 1 MΩ, 외부 1 MΩ(실온), VDD 3.3 V(좋은 서플라이).
- 요약표 순서 `test_plan_summary.csv`: VI 2, 20, 3, 6, 5, 15 (VIH→VIL) / AI 10 / VO_PP 12, 4, 14, 7 (VOH→VOL) / VO_OD 16, 19, 18 (VOL) / IOH / Rpu / Rpd.
- 샘플: fab(TSMC, VIS=뱅가드)마다 3개, 총 6번. 시험 이름으로 구분. **샘플끼리 값을 섞지 않음**(한 샘플 안의 같은 값은 최악값).

## 실장비에서 확인된 사실 (중요)
1. **원격 `SAVe:IMAGe`(그리고 이미지 저장 직후 READFile/DELETE)를 보내면 TekScope 가 멈춘다.** tm_devices 공식 라이브러리,
   LAN 연결에서도 같음 → TekScope 쪽 문제로 확정. 그래서 화면은 **캡처 서버(screen_api, 기본)** 로 받는다(2026-10-21 실장비 정상).
   예비 방식 `aoe`(Act On Event 로 스코프에 저장만 하고 측정이 끝난 뒤 한 번에 가져오기, 대기 arm/save/post = 1/2/2 초)도 남겨 둠.
2. USB: 1.25M 포인트 CURVe 0.1 s. `READFile` 뒤 항상 `\n` 1바이트 추가(제거 처리). 강제 종료 뒤 남은 통신은 연결 때 viClear 로 버림
   (`[connection] usb_clear`). `reset_state` 는 응답이 없을 때만 Device Clear, AOE/결과 바 명령은 상태가 다를 때만 보냄.
3. `HIGH/LOW` 측정은 아이 다이어그램용 → VOH/VOL 은 `TOP/BASE`. 측정·커서 명령은 묶음 뒤 `*OPC?` + 0.1 s.
4. 측정 배지는 획득·파형 전송 뒤에 추가(획득 중 측정 이벤트 2200번대가 오류 비트를 세움). `check()` 는 2200·400~500번대를 경고로만.
5. 기본 CWD `C:/Users/Public/Tektronix/TekScope`. `FILESystem:MKDir` 이 이미 있으면 오류 257(정상 처리).

## 구조
README 의 "개발자용" 참고. 핵심 흐름:
- `scope.py --plan` (`run_plan.bat`) → `scopetech/plan.py` `PlanRunner`: 시험 이름 → 샘플(`samples.py`, `plan/samples.csv`),
  진행 기록 `plan/<샘플>/progress_온도C.json`, 측정은 `Bench`(workflow.py) → `steps/*`, 캡처 방법 `capture/*`.
  측정 직전마다 `plan/overrides.ini`(GUI 설정) 반영, 쓴 값은 result.json `plan_settings`. 측정 뒤 `Bench.resume()` 으로 Run.
- GUI `plan_gui.py` → `scopetech/plan_gui.py`(tkinter) + `plan_view.py`(데이터) + `plan_state.py`(state.json 생존 표시, overrides.ini).
  별도 프로세스, 측정은 CLI 만. Windows 에서 GUI 가 읽는 순간 파일 교체가 막힐 수 있어 `replace_retry`.
- 정리 `cleanup.py` (`run_cleanup.bat`) → `scopetech/plan_import.py`: 기록 → (샘플, 온도, 계획 줄), 같은 샘플 안 최신 기록,
  구버전 빠진 값은 `recompute.py` 로 채움(원본 `result.orig.json`), 핀 없는 기록은 `plan/missing_pins.csv` 를 사용자가 채움.
- 공용 파일은 `captures/plan/` 바로 아래: state.json, overrides.ini, gui.json, samples.csv, missing_pins.csv, plan_summary_all.csv.

- 온도 고치기 `fix_temp.py`(`scopetech/fix_temp.py`, `run_fix_temp.bat`): 샘플 → 잘못된 온도 → 원래 온도 → 측정 묶음(30분 간격) 순으로 물어 result.json temp(백업, `temp_fixed` 기록)·진행 기록 이동(원래 온도에 완료된 같은 항목은 묻고 유지/교체)·사진 모음 이름·요약표·results.csv 를 고침.

## 테스트
- `python -m unittest discover -s tests -t .` (시뮬레이터, 가짜 TCP/VISA). 이 개발 환경의 Python 3.13 에는 tkinter 가 없어 GUI 시험은
  `xvfb-run -a python3.12 -m unittest tests.test_plan_gui` 로 확인(3.13 에선 건너뜀).

## 아직 실장비·Windows 에서 확인 안 된 것
- 계획 모드 전체(특히 POR: 트리거 Normal 모드 필요, `power_on_levels` 의 출력 상승 판정), IOH/Rp 의 측정마다 Single.
- GUI: 콘솔 창 없이 뜨는지(pythonw/CREATE_NO_WINDOW), 한글 글꼴, 폴더·CSV 열기(os.startfile).
- 정리 도구: 실제 섞인 captures(초기 형식 등)에서의 결과.
- 측정 뒤 Run 복귀.

## 참고
- 매뉴얼: https://download.tek.com/manual/4-5-6-Series-MSO_Programmer_077189805.pdf
- 사용자 업로드 `log`, `scpi` 파일은 사용자 요청으로 레포에 유지.
- 캡처 서버 원본(테스트·예제·개발 인계 문서)은 esaram5-lab/scope-capture-api. 동봉본 수정은 원본에서 하고 다시 복사.
- 커밋 메시지 끝 attribution 은 세션 지침을 따를 것. 모델명은 커밋/PR 에 쓰지 말 것.
