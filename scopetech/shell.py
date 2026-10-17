"""대화형 명령 셸."""
from __future__ import annotations

import cmd
import traceback

from .transport import ScopeError
from .workflow import Bench

INTRO = """
명령 요약 (자세히: help <명령>)
  vih [입력 출력] [hold]   VIL/VIH 측정 + 커서   (기본 1 2)
  voh [채널...] [hold]     VOH/VOL 측정 + 통계   (기본 2)
  capture                  화면 캡처 (meas.png / clean.png)
  save [채널...]           파형 저장 (CSV + .wfm + setup)
  all vih 1 2 / all voh 2  측정 -> 캡처 -> 저장 한 번에
  temp 85 | name INV_3V3 | spec 3V3 | type od     시험 조건 설정
  status | run | stop | clear | scpi <명령> | quit
""".strip("\n")


def _ints(tokens: list[str]) -> list[int]:
    out = []
    for t in tokens:
        t = t.upper().removeprefix("CH")
        out.append(int(t))
    return out


class ScopeShell(cmd.Cmd):
    def __init__(self, bench: Bench):
        super().__init__()
        self.bench = bench
        self.intro = INTRO

    # 셸 동작 -------------------------------------------------------------
    @property
    def prompt(self) -> str:  # type: ignore[override]
        b = self.bench
        return f"[{b.temp}C {b.name} {b.out_type} spec={b.spec_name or '-'}] scope> "

    def emptyline(self) -> bool:  # 빈 줄에 직전 명령이 반복되지 않도록
        return False

    def default(self, line: str) -> None:
        self.stdout.write(f"알 수 없는 명령: {line}  (help 로 목록 확인)\n")

    def onecmd(self, line: str) -> bool:
        try:
            return super().onecmd(line)
        except ScopeError as e:
            print(f"[오류] {e}")
            try:
                self.bench.scope.recover()
            except Exception:
                pass
        except (ValueError, KeyError) as e:
            print(f"[입력 오류] {e}")
        except KeyboardInterrupt:
            print("\n중단했습니다.")
            try:
                self.bench.scope.recover()
            except Exception:
                pass
        except Exception:
            traceback.print_exc()
        return False

    # 측정 ---------------------------------------------------------------
    def _split(self, arg: str) -> tuple[list[str], bool]:
        tokens = arg.split()
        hold = any(t.lower() == "hold" for t in tokens)
        return [t for t in tokens if t.lower() != "hold"], hold

    def do_vih(self, arg: str) -> None:
        """vih [입력채널 출력채널] [hold]
        입력(삼각파)과 출력의 전환 지점에서 VT+/VT-(VIH/VIL)을 계산하고 커서를 놓습니다.
        단일 획득(Single) 후 분석합니다. hold 를 붙이면 지금 멈춰 있는 화면 파형으로 분석합니다.
        예) vih   /   vih 1 2   /   vih 3 4 hold"""
        tokens, hold = self._split(arg)
        chs = _ints(tokens)
        cfg = self.bench.cfg
        if not chs:
            chs = [cfg.getint("defaults", "vih_in"), cfg.getint("defaults", "vih_out")]
        if len(chs) != 2:
            raise ValueError("vih 는 입력, 출력 채널 2개가 필요합니다. 예) vih 1 2")
        self.bench.vih(chs[0], chs[1], hold)

    def do_voh(self, arg: str) -> None:
        """voh [채널...] [hold]
        출력 채널에 TOP(=VOH), BASE(=VOL), MAX, MIN 측정을 추가하고 통계를 수집합니다.
        예) voh   /   voh 2   /   voh 2 3 4"""
        tokens, hold = self._split(arg)
        chs = _ints(tokens) or _ints(self.bench.cfg.get("defaults", "voh").replace(",", " ").split())
        self.bench.voh(chs, hold)

    def do_capture(self, arg: str) -> None:
        """capture
        화면을 캡처합니다. 측정/커서가 있으면 meas.png(표시)와 clean.png(숨김) 두 장을 저장합니다."""
        self.bench.capture()

    def do_save(self, arg: str) -> None:
        """save [채널...]
        파형을 CSV + .wfm 으로, 스코프 설정을 setup.set 으로 저장합니다.
        채널을 생략하면 화면에 켜진 채널 전부를 저장합니다.   예) save   /   save 1 2"""
        self.bench.save(_ints(arg.split()) or None)

    def do_all(self, arg: str) -> None:
        """all vih [입력 출력] [hold]  |  all voh [채널...] [hold]
        측정 -> 캡처 -> 저장 을 한 번에 실행합니다.   예) all vih 1 2   /   all voh 2"""
        tokens = arg.split()
        if not tokens or tokens[0].lower() not in ("vih", "vil", "voh", "vol"):
            raise ValueError("사용법: all vih 1 2  또는  all voh 2")
        rest = " ".join(tokens[1:])
        if tokens[0].lower() in ("vih", "vil"):
            self.do_vih(rest)
        else:
            self.do_voh(rest)
        self.bench.capture()
        self.bench.save()

    do_vil = do_vih
    do_vol = do_voh

    # 시험 조건 -----------------------------------------------------------
    def do_temp(self, arg: str) -> None:
        """temp <온도>   예) temp 85  /  temp -40   (폴더 이름에 들어갑니다)"""
        if arg.strip():
            float(arg.strip().removesuffix("C").removesuffix("c"))
            self.bench.temp = arg.strip().removesuffix("C").removesuffix("c")
        print(f"온도: {self.bench.temp}C")

    def do_name(self, arg: str) -> None:
        """name <시험 이름>   예) name INV_3V3   (폴더 이름에 들어갑니다)"""
        if arg.strip():
            self.bench.name = arg.strip()
        print(f"시험 이름: {self.bench.name}")

    def do_spec(self, arg: str) -> None:
        """spec <프로파일>   config.ini 의 [spec:<프로파일>] 기준으로 판정합니다.  spec - : 판정 안 함"""
        cfg = self.bench.cfg
        a = arg.strip()
        if a in ("-", "none"):
            self.bench.spec_name = ""
        elif a:
            try:
                cfg.spec(a)
            except KeyError:
                raise ValueError(f"config.ini 에 [spec:{a}] 섹션이 없습니다.") from None
            self.bench.spec_name = a
        print(f"판정 기준: {self.bench.spec_name or '(없음)'}   사용 가능: {', '.join(cfg.spec_profiles()) or '-'}")
        if self.bench.spec_name:
            print("  " + ", ".join(f"{k}={v if v is not None else '-'}" for k, v in cfg.spec(self.bench.spec_name).items()))

    def do_type(self, arg: str) -> None:
        """type pp|od   출력 타입. OD 는 VOH 판정을 하지 않습니다(풀업 전압이므로)."""
        a = arg.strip().upper()
        if a:
            if a not in ("PP", "OD"):
                raise ValueError("type 은 pp 또는 od")
            self.bench.out_type = a
        print(f"출력 타입: {self.bench.out_type}")

    def do_status(self, arg: str) -> None:
        """status   현재 설정/연결 상태"""
        b = self.bench
        print(f"스코프: {b.scope.idn}")
        print(f"연결: {b.scope.t.description}")
        print(f"획득: {'RUN' if b.scope.is_running() else 'STOP'}, 모드 {b.scope.acq_mode()}")
        print(f"온도 {b.temp}C / 이름 {b.name} / 출력 {b.out_type} / 기준 {b.spec_name or '-'}")
        print(f"현재 기록 폴더: {b.record.folder if b.record else '-'}")

    # 스코프 직접 제어 -------------------------------------------------------
    def do_run(self, arg: str) -> None:
        """run   연속 획득 시작"""
        self.bench.scope.run()

    def do_stop(self, arg: str) -> None:
        """stop  획득 정지"""
        self.bench.scope.stop()

    def do_single(self, arg: str) -> None:
        """single   단일 획득"""
        self.bench.scope.single(self.bench.cfg.getfloat("measure", "single_timeout_s"))

    def do_clear(self, arg: str) -> None:
        """clear   이 프로그램이 만든 측정/커서를 화면에서 지웁니다."""
        self.bench.clear_overlay()

    def do_idn(self, arg: str) -> None:
        """idn   스코프 모델/펌웨어"""
        print(self.bench.scope.query("*IDN?"))

    def do_scpi(self, arg: str) -> None:
        """scpi <명령>   SCPI 명령 직접 전송(? 가 있으면 응답 출력).  예) scpi ACQuire:MODe?"""
        if not arg.strip():
            return
        if "?" in arg:
            print(self.bench.scope.query(arg.strip()))
        else:
            self.bench.scope.write(arg.strip())
            err = self.bench.scope.check(arg.strip(), raise_=False)
            print(err or "OK")

    def do_quit(self, arg: str) -> bool:
        """quit   종료"""
        return True

    do_exit = do_quit

    def do_EOF(self, arg: str) -> bool:
        print()
        return True
