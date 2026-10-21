# ScopeCaptureAPI 1.2 — Python 호출, 직결 LAN 자동 검색, 화면 안정 대기·창 캡처

Tektronix MSO58B Windows의 현재 화면을 외부 Python 프로그램에서 PNG로 받습니다.
TekScope API/VISA/SCPI를 호출하지 않습니다. 기존 측정 명령끼리의 충돌 자체를 해결하는 프로그램은 아닙니다.

## 준비

- 스코프: Python 3.9 이상 + Pillow.
- 외부 PC: Python 3.9 이상. 자동 NIC 검색을 사용하려면 psutil 설치.
- 두 장비에 이 ZIP을 각각 압축 해제합니다.
- LAN 케이블 직결. 두 장비의 IPv4가 서로 통신 가능한 상태여야 합니다.
- 장비/회사 정책에 따라 인터넷 설치가 안 되면 호환되는 wheel을 별도로 준비합니다.

## 1. 스코프에서 로컬 캡처부터 확인

압축을 푼 폴더에서 PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install Pillow
.\.venv\Scripts\python.exe scope_capture_server.py --local-test test.png
```

test.png를 열어 실제 파형과 측정값이 보이는지 확인합니다.
기존 test.png가 있으면 이름을 바꾸세요. 기존 파일은 덮어쓰지 않습니다.

### 1-1. TekScope 창만 찍기 (선택)

작업표시줄·바탕화면 없이 TekScope 창 영역만 찍으려면 먼저 창 제목을 확인합니다.

```powershell
.\.venv\Scripts\python.exe scope_capture_server.py --list-windows
.\.venv\Scripts\python.exe scope_capture_server.py --window TekScope --local-test test_window.png
```

- `--window 제목일부`: 캡처할 때마다 제목에 이 글자가 들어간 창(대소문자 무시, 맨 앞 창 우선)을 찾아 그 영역만 찍습니다.
  창이 없거나 최소화돼 있으면 캡처를 거절하고 이유를 돌려줍니다(HTTP 503, 클라이언트 `CaptureError` 메시지).
- 서버를 띄운 터미널 창은 제목에 명령줄이 보여도 제외합니다.
- `--require-foreground`: 그 창이 맨 앞에 있을 때만 찍습니다. TekScope 대화상자도 별도 창이라 켜 두면 거절될 수 있습니다.
- health 응답의 `window` 항목에 창을 찾았는지, 맨 앞인지, 영역이 나옵니다.
- 창 영역 위에 겹친 알림/대화상자는 그대로 찍힙니다. `--bbox` 와 같이 쓸 수 없습니다.

## 2. 스코프에서 자동 검색 서버 실행

```powershell
.\.venv\Scripts\python.exe scope_capture_server.py --discovery --name MSO58B_01
```

- TCP 8765(캡처), UDP 8766(검색)을 사용합니다.
- --discovery 사용 시 --bind를 생략하면 0.0.0.0으로 모든 IPv4 인터페이스에서 대기합니다.
- --discovery 없이 --bind를 생략하면 종전처럼 127.0.0.1에서만 대기합니다.
- 첫 실행에 capture_token.txt, capture_device_id.txt가 생성됩니다.
- **capture_token.txt만 외부 PC에 복사**합니다. 장비 ID 파일을 다른 스코프에 복사하지 마세요.
- 토큰 파일 내용은 화면 출력/검색 패킷에 포함되지 않습니다.
- Ctrl+C로 서버를 종료합니다.
- 자동 IP가 바뀌는 환경에서는 기존 고정 --allow-ip를 붙이지 않습니다. 고정 IP일 때만 사용하세요.

## 3. 방화벽 설정

스코프에서 실제 유선 어댑터 이름을 확인합니다:

```powershell
Get-NetAdapter
```

필요하면 관리자 PowerShell에서 다음 규칙을 추가하세요. `$lan`은 실제 유선 어댑터 이름으로 바꿉니다.
직결 네트워크가 Public으로 분류되어도 동작하도록 Profile Any를 사용하되 유선 인터페이스와 같은 서브넷으로 제한합니다.

```powershell
$lan = "이더넷"
New-NetFirewallRule -DisplayName "Scope Capture API TCP" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -InterfaceAlias $lan -RemoteAddress LocalSubnet -Profile Any
New-NetFirewallRule -DisplayName "Scope Capture Discovery UDP" -Direction Inbound -Action Allow -Protocol UDP -LocalPort 8766 -InterfaceAlias $lan -RemoteAddress LocalSubnet -Profile Any
```

회사 보안 정책/기존 방화벽 규칙이 우선할 수 있습니다. 반복 생성하지 말고 기존 규칙을 확인하세요.
HTTP는 암호화되지 않습니다. 신뢰하는 장비 LAN에서 사용하고 인터넷 포트를 열지 마세요.

규칙 삭제:

```powershell
Remove-NetFirewallRule -DisplayName "Scope Capture API TCP"
Remove-NetFirewallRule -DisplayName "Scope Capture Discovery UDP"
```

## 4. 외부 PC에서 Python 자동 연결

```powershell
py -m pip install psutil
py example_auto_connect.py
```

다른 Python 프로그램에서 사용:

```python
from scope_capture_client import ScopeCaptureClient

scope = ScopeCaptureClient.auto_connect(
    token_file=r"C:\ScopeCapture\capture_token.txt",
    discovery_timeout=2,
    timeout=5,
)
print(scope.host)  # 자동으로 찾은 스코프 IP
scope.capture_to_file(r"D:\측정결과\capture.png")
# png_bytes = scope.capture_png()  # 별도의 새 캡처를 bytes로 받기
```

호출 프로그램 옆에 scope_capture_client.py와 scope_discovery.py를 두세요.
토큰 파일은 절대 경로를 지정하거나 해당 모듈 옆에 둡니다.
자동 검색 시 HMAC-SHA256으로 요청/응답을 인증합니다. 같은 토큰을 가진 장비만 발견됩니다.
최초 토큰 파일 전달은 여전히 필요합니다. 자동 검색이 비밀번호 없는 자동 페어링은 아닙니다.

## 5. 유선 인터페이스 지정 / 여러 장비 선택

```python
from scope_discovery import list_interfaces, discover_scopes
from scope_capture_client import ScopeCaptureClient

print(list_interfaces())
found = discover_scopes(
    timeout=2,
    token_file="capture_token.txt",
    interface_names=["이더넷"],  # 실제 이름 사용. 생략하면 활성 IPv4 NIC 모두 검색
)
for device in found:
    print(device.name, device.device_id, device.host, device.port)

scope = ScopeCaptureClient.auto_connect(
    token_file="capture_token.txt",
    name="MSO58B_01",  # 또는 device_id="실제 장비 ID"
    interface_names=["이더넷"],
)
```

자동 열거는 유선뿐 아니라 활성 Wi-Fi/가상 NIC도 포함합니다. 유선만 원하면 interface_names로 제한하세요.
장비가 여러 대면 자동 연결은 임의로 하나를 선택하지 않고 오류를 반환합니다.
서로 다른 토큰을 가진 여러 스코프는 각 토큰으로 따로 검색해야 합니다.

## 6. LAN 직결 시 IP 조건

자동 검색은 IP를 설정하거나 네트워크 설정을 바꾸지 않습니다.
DHCP가 없는 자동 IP 구성에서는 169.254.x.x / 16 주소가 생길 수 있습니다.
케이블 연결 직후 아직 주소가 없으면 기다린 뒤 다시 검색하세요. ipconfig로 양쪽 주소를 확인합니다.

고정 IP를 선호하면 예를 들어 PC 192.168.50.1, 스코프 192.168.50.2, 마스크 255.255.255.0으로 구성할 수 있습니다.
다른 NIC와 주소 대역이 겹치지 않게 정하세요. 자동 검색은 고정 IP에서도 가능합니다.
각 NIC의 서브넷 broadcast와 255.255.255.255에 검색 패킷을 보냅니다. 라우터 너머 검색은 지원하지 않습니다.
IP가 바뀐 뒤에는 auto_connect를 다시 호출합니다. 이미 생성한 클라이언트의 host는 자동 갱신되지 않습니다.

psutil 없이 직접 NIC 정보를 제공하는 고급 사용법:

```python
from scope_discovery import NetworkInterface
scope = ScopeCaptureClient.auto_connect(
    token_file="capture_token.txt",
    interfaces=[NetworkInterface("LAN", "169.254.10.20", "255.255.0.0")],
)
```

## 7. 기존 수동 IP 호출도 지원

```python
scope = ScopeCaptureClient("192.168.50.2", token_file="capture_token.txt", timeout=5)
print(scope.health())
scope.capture_to_file("capture.png")
```

이 방식은 psutil이 필요 없습니다. 기존 CLI도 유지합니다:

```powershell
py scope_capture_client.py --host 192.168.50.2 --health
py scope_capture_client.py --host 192.168.50.2 --output capture.png
```

## 7-1. 화면이 멈출 때까지 기다렸다 찍기 (stable)

```python
png = scope.capture_png(stable_ms=2000)                     # 최대 2초
png = scope.capture_png(stable_ms=2000, interval_ms=100, frames=3)
scope.capture_to_file("capture.png", stable_ms=2000)
```

- 서버가 interval_ms(기본 100, 20~1000) 간격으로 계속 찍어 frames(기본 3, 2~10)장이 연속으로 똑같으면 그 화면을 돌려줍니다.
  화면 갱신을 고정 대기로 짐작하지 않아도 됩니다. 스코프 획득을 멈춘 뒤(Single/Stop) 부르세요.
- stable_ms(최대 10000) 안에 멈추지 않으면 `CaptureUnstable` (HTTP 504). 화면을 저장하지 않습니다.
  획득이 Run 상태면 파형이 계속 바뀌어 멈추지 않습니다.
- 바뀐 직후 첫 장이 아직 이전 화면일 수 있으니, 측정 프로그램에서 화면을 바꾼 직후라면 짧은 대기(0.2~0.5초)를 함께 두는 것이 안전합니다.
- 응답 헤더 `X-Capture-Frames`(찍은 장수), `X-Capture-Stable-Ms`(멈출 때까지 걸린 시간)는 서버 로그에도 남습니다.
- 클라이언트 timeout 에 stable_ms 가 자동으로 더해집니다. 이전 버전 서버에 stable_ms 를 보내면 404 → "update scope_capture_server.py".
- CLI: `py scope_capture_client.py --host 192.168.50.2 --output capture.png --stable-ms 2000`

## 8. 오류 처리와 동작 제약

```python
from scope_discovery import DiscoveryError
from scope_capture_client import CaptureError, CaptureBusy, CaptureUnstable

try:
    scope = ScopeCaptureClient.auto_connect(token_file="capture_token.txt")
    scope.capture_to_file("capture.png")
except DiscoveryError as error:
    print("검색/선택/연결 확인 실패:", error)
except CaptureBusy:
    print("다른 캡처 진행 중: 이번 요청은 실행되지 않았습니다")
except CaptureUnstable:
    print("화면이 stable_ms 안에 멈추지 않았습니다(획득 정지 확인)")
except (CaptureError, OSError, ValueError) as error:
    print("캡처/설정/저장 오류:", error)
```

- 모든 호출은 동기 방식. GUI에서는 작업 스레드에서 실행합니다.
- 같은 서버 프로세스는 한 번에 하나만 캡처합니다. 겹친 요청은 Busy로 거절하고 큐에 넣지 않습니다.
- 캡처 요청은 자동 재시도하지 않습니다. 검색 요청만 제한 시간 내 반복 전송합니다.
- health는 서버 응답 여부만 확인하며 실제 캡처 가능 여부나 TekScope 상태는 확인하지 않습니다.
- PNG 구조/CRC 확인 후 임시 파일에서 최종 파일로 저장합니다. 이미 있는 파일은 덮어쓰지 않습니다.
- timeout은 네트워크 대기 제한입니다. 엄격한 전체 실행시간 제한/서버 캡처 강제 취소는 아닙니다.
- Windows 캡처가 멈춰 Busy가 지속되면 서버를 재시작해야 합니다.
- 스코프는 로그인/잠금 해제 상태, TekScope 창은 전면에 둡니다. 다른 창과 알림도 그대로 찍힙니다.
- 측정 완료와 화면 갱신은 다릅니다. 기존 측정 코드가 이를 확인한 뒤 호출해야 합니다.
- 주 모니터 기본 캡처. --bbox LEFT TOP RIGHT BOTTOM으로 영역 지정, --window 제목으로 창 영역만 캡처 가능.
- PNG는 서버 메모리에서만 생성. 로그는 capture_server.log, 1MB × 현재 파일 및 백업 3개.

## 9. 자동 실행

작업 스케줄러: '로그온할 때', '사용자가 로그온한 경우에만 실행'.
프로그램은 가상환경 python.exe 절대 경로, 인수는 서버 파일 절대 경로와 --discovery --name MSO58B_01,
시작 위치는 압축을 푼 디렉터리로 지정합니다. 처음에는 수동 실행으로 확인하세요.

## 10. 테스트 범위

```powershell
py -m unittest -v test_capture_api.py test_discovery.py
```

26개 자동 테스트: 기본 캡처 API 6개 + 화면 안정 대기·창 캡처 11개 + 검색 9개.
창 찾기(user32 EnumWindows/DwmGetWindowAttribute)는 가짜 창 목록으로만 시험했습니다.
가짜 PNG/로컬 HTTP·UDP 소켓, NIC 열거 mock으로 검증했습니다.
실제 MSO58B Windows 화면, 실제 Windows NIC broadcast/방화벽, APIPA 직결은 아직 실장비 미검증입니다.
다른 AI에게는 이 ZIP과 ScopeCaptureAPI_AI_HANDOFF.md를 함께 전달하세요. 인계 문서는 ZIP에도 포함되어 있습니다.
