# ScopeCaptureAPI — AI 작업 인계 문서

작성일: 2026-10-20 (Asia/Seoul)  
대상 코드: `ScopeCaptureAPI_v1.1.zip`  
문서 목적: 다른 AI가 배경을 다시 질문하지 않고 현재 구현을 분석하고 다음 작업을 진행하도록 한다.

> 이 문서와 `ScopeCaptureAPI_v1.1.zip`을 함께 전달한다. 문서 자체에는 전체 소스가 포함되어 있지 않다.
> 구현 완료 사항, 사용자 요구, 제안 단계의 기능을 구분한다. 자동 검색은 v1.1에서 구현되었다. Windows 실장비 검증은 아직 수행하지 않았다.

## 1. 사용자 요구와 환경

- 대상 장비: Tektronix MSO58B, Windows 기반. 사용자가 Windows 바탕화면에 접근 가능한 환경이라고 설명했다.
- 제어 측: 외부 Windows PC.
- 일반적인 연결: PC와 스코프 사이 LAN 케이블 직결(P2P). 공유기/DHCP가 항상 있다고 가정하지 않는다.
- 물리적으로 USB 연결도 가능하지만 본 캡처 프로그램은 LAN 통신으로 설계했다.
- 호출 프로그램은 반드시 Python이다. C#, C++, LabVIEW 등의 예제나 별도 타 언어 SDK는 필요 없다.
- 기존 문제: 스코프에 보내는 명령이 겹치면 스코프 프로그램이 멈춘다고 사용자가 보고했다.
- API 패키지 자체의 오류라고 단정하면 안 된다. 명령 처리 충돌의 정확한 원인은 실장비에서 아직 진단하지 않았다.
- 목적: 외부 Python 측정 프로그램에서 요청하면 스코프 Windows의 현재 화면을 PNG로 받고 저장한다.
- 사용자는 IP 자동 검색 가능 여부를 질문했고, 주로 LAN 직결한다고 추가 설명했다.
- 최신 요청: 다른 AI용 Markdown 문서를 만들고 자동 검색까지 구현한다. 이 배포본은 두 요청을 모두 반영한다.

## 2. 핵심 설계 결정

1. 캡처 서버는 TekScope API, VISA, SCPI를 호출하지 않는다.
2. 스코프의 로그인된 Windows 사용자 세션에서 Pillow ImageGrab으로 화면 픽셀을 캡처한다.
3. 외부 Python 코드는 ScopeCaptureClient 클래스를 import해서 사용한다.
4. 클래스 내부 통신은 HTTP이며 호출자는 HTTP를 직접 다룰 필요가 없다.
5. 캡처 서버와 기존 계측 제어 통신을 분리한다. 기존 제어 명령끼리의 충돌까지 해결하는 것은 아니다.
6. 캡처 중 추가 캡처 요청은 대기시키지 않고 즉시 Busy로 거절한다.
7. 자동 재시도하지 않는다. 재시도하면 측정 시점과 다른 화면이 저장될 수 있다.
8. 스코프에는 PNG를 쌓지 않는다. 메모리에서 인코딩하고 외부 PC에 반환한다.
9. 기존 파일을 덮어쓰지 않고 완성된 PNG만 최종 경로에 게시한다.
10. 현재 화면을 캡처하므로 취득 데이터/하드웨어 트리거 시점과 정확히 동기화된 이미지라는 보장은 없다.

## 3. 현재 배포 파일

ZIP 내부 최상위 디렉터리: `ScopeCaptureAPI/`

| 파일 | 역할 |
|---|---|
| scope_capture_server.py | 스코프 Windows 캡처 서버 및 단독 로컬 캡처 테스트 |
| scope_capture_client.py | Python 클래스 API, 편의 함수, CLI |
| example_integration.py | Python 호출 예제; 실제 IP와 토큰 경로 설정 필요 |
| scope_discovery.py | 인증된 UDP 검색, NIC 열거, 서버 responder |
| example_auto_connect.py | Python 자동 연결 예제 |
| test_discovery.py | UDP 검색/선택/인증/자동 연결 테스트 |
| ScopeCaptureAPI_AI_HANDOFF.md | 본 인계 문서 |
| test_capture_api.py | 가짜 PNG를 이용한 HTTP/오류 처리 테스트 |
| README_KO.md | 한글 설치, 실행, 네트워크, 운영 및 검증 안내 |

실행 시 생성되는 파일:

- capture_token.txt: 서버가 최초 실행에 생성하는 인증 토큰. 코드/ZIP에는 실제 토큰이 없다.
- capture_device_id.txt: 검색 활성화 시 생성되는 영구 UUID. 다른 스코프로 복사하지 않는다.
- capture_server.log: 최대 1MB, 백업 3개의 회전 로그.
- --local-test로 지정한 PNG: 명시적으로 로컬 테스트를 실행할 때만 생성.

이 인계 문서는 별도 제공하며 v1.1 ZIP에도 동일 내용이 포함된다.
초기 대화의 collect_png.py는 PNG를 모아 복사하는 별개 프로그램이며 캡처 서버 코드에 통합되어 있지 않다.

## 4. 실행 환경과 첫 설치

요구 환경: Python 3.9 이상. 서버에는 Pillow 필요. 수동 IP 클라이언트는 표준 라이브러리만 사용한다. NIC 자동 열거에는 psutil이 추가로 필요하다.
Pillow 버전은 고정하지 않았으며 MSO58B에 설치된 Python/Windows 버전과의 호환성은 실장비에서 확인해야 한다.

스코프에서 가상환경을 사용하는 예:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install Pillow
.\.venv\Scripts\python.exe scope_capture_server.py --local-test test.png
```

실제 캡처가 정상일 때 서버 실행(IP는 예시):

```powershell
.\.venv\Scripts\python.exe scope_capture_server.py --bind 192.168.0.10 --allow-ip 192.168.0.20
```

생성된 capture_token.txt와 scope_capture_client.py를 외부 PC의 호출 프로그램에서 접근 가능한 위치에 둔다.
수동 모드의 기본 바인딩은 127.0.0.1이다. --discovery를 지정하면 기본 바인딩이 0.0.0.0으로 변경되어 직결 자동 IP 변경에 대응한다. 명시적 --bind는 우선한다.
기본 TCP 포트는 8765이다.
방화벽 설정 예와 작업 스케줄러 설정은 README_KO.md를 참고한다.
Windows 서비스/비대화형 세션이 아니라 로그인된 사용자 세션에서 실행한다.

## 5. 실제 구현된 Python 공개 인터페이스

```python
ScopeCaptureClient(
    host,
    *,
    token=None,
    token_file=None,
    port=8765,
    timeout=5,
)
```

- 수동 생성자 host는 IPv4 리터럴만 지원한다. 호스트명/IPv6는 미지원이다. 자동 검색은 별도 클래스 메서드 auto_connect로 지원한다.
- token: 직접 지정할 수 있다. 지정하지 않으면 token_file을 읽는다.
- token_file 미지정 시: scope_capture_client.py 옆의 capture_token.txt.
- 모든 메서드는 동기 호출이다. GUI 프로그램에서는 별도 작업 스레드에서 호출한다.

| 메서드 | 반환 | 기능 |
|---|---|---|
| auto_connect(...) | ScopeCaptureClient | 인증된 장비 검색/선택 후 HTTP health 확인; classmethod |
| health() | dict | 서버 상태와 busy 확인 |
| capture_png() | bytes | 새 화면 캡처 후 PNG 구조/CRC 검증 |
| capture_to_file(save_path) | pathlib.Path | 새 화면 캡처 후 외부 PC에 저장 |

```python
from scope_capture_client import ScopeCaptureClient, CaptureBusy, CaptureError

scope = ScopeCaptureClient(
    '192.168.0.10',
    token_file=r'C:\ScopeCapture\capture_token.txt',
    timeout=5,
)
print(scope.health())

try:
    path = scope.capture_to_file(r'D:\측정결과\85도\VIH\scope.png')
except CaptureBusy:
    print('다른 캡처 진행 중: 이번 요청은 실행되지 않음')
except (CaptureError, OSError) as error:
    print('캡처 또는 저장 실패:', error)
```

편의 함수도 존재한다:

```python
capture_scope(host, save_path, timeout=5, *, token=None, token_file=None, port=8765)
```

예외:

- CaptureError(RuntimeError): 통신/응답/PNG 검증 관련 오류.
- CaptureBusy(CaptureError): HTTP 409 응답.
- FileExistsError: 저장 대상이 이미 존재함.
- OSError 및 하위 클래스: 토큰 파일/파일 저장 관련 오류 등.
- 일부 인수 검증은 ValueError/OSError로 전파될 수 있다. 모든 오류가 CaptureError로 통일되어 있지는 않다.

## 6. 내부 통신 규약

HTTP 요청 헤더: Authorization: Bearer <token>
프록시 사용 및 HTTP redirect 추적은 클라이언트에서 비활성화한다.
HTTPS는 구현하지 않았다. 신뢰하는 장비 LAN을 전제로 한다.

| 요청 | 응답 |
|---|---|
| GET /health | 200 JSON: status='ok', busy=bool, desktop_checked=false |
| POST /capture | 200 image/png; 빈 요청 본문 |

health는 서버 생존만 확인한다. 데스크톱 캡처 가능 여부나 TekScope 정상 동작을 판정하지 않는다.
캡처 응답 헤더 X-Capture-Started-UTC는 캡처 함수 직전의 스코프 Windows 시각이다.
현재 Python API는 이 헤더를 반환하지 않고 PNG 데이터만 반환한다.

| 상태 코드 | 의미 |
|---|---|
| 400 | 캡처 요청 본문이 비어 있지 않거나 Transfer-Encoding 사용 |
| 401 | 토큰 불일치 |
| 403 | --allow-ip와 요청 PC IP 불일치 |
| 404 | 없는 경로 |
| 409 | 캡처/전송 중; 추가 요청 미실행 |
| 503 | 캡처 함수 오류; 서버 로그 확인 |

일반 오류 응답은 message 필드가 있는 JSON이다. 지원하지 않는 메서드는 표준 HTTP 서버의 501 응답이다.
서버의 캡처 잠금은 PNG 전송이 완료되거나 실패할 때까지 유지한다.

## 7. 구현 세부 사항

### 서버

- ThreadingHTTPServer 사용. 단일 capture_lock을 non-blocking acquire하여 캡처를 직렬화한다.
- Pillow ImageGrab.grab(bbox=..., all_screens=False) 사용.
- 기본 대상은 주 화면, --bbox LEFT TOP RIGHT BOTTOM으로 영역 지정 가능.
- 시작 시 Windows DPI awareness 설정 시도.
- OpenInputDesktop/SwitchDesktop으로 대화형 데스크톱 접근 가능 여부를 확인한다.
- 이 데스크톱 검사와 Pillow의 실제 동작은 Windows 실장비에서 아직 검증하지 않았다.
- 토큰은 secrets.token_urlsafe(32)로 생성, secrets.compare_digest로 비교.
- --allow-ip는 단일 IPv4 문자열이며 HTTP/검색 둘 다 제한한다. 자동 IP 환경에서는 생략하고 유선 인터페이스 방화벽 범위와 토큰을 사용한다.
- 연결 소켓 timeout은 10초. 캡처 자체를 강제로 중단하는 timeout은 없다.
- 서버 프로세스 한 개 안에서만 잠금이 공유된다. 여러 포트에 여러 서버를 실행하면 상호 잠금되지 않는다.

### 클라이언트

- urllib 기반, 요청마다 독립 연결.
- 이미지 최대 응답 크기 64MiB.
- Content-Length, Content-Type 및 수신 완결성 확인.
- PNG 시그니처, IHDR 크기/이미지 크기, chunk CRC, IDAT 존재, IEND 등을 확인.
- PNG 전체 디코딩 검사는 아니다. 압축 이미지가 실제로 디코딩 가능한지까지 보장하지 않는다.
- 동일 저장 폴더의 임시 파일에 기록 후 flush/fsync.
- Windows는 os.rename으로 기존 파일을 덮어쓰지 않고 게시한다.
- 비-Windows 테스트 경로는 os.link 후 임시 파일 제거.
- 저장 폴더 자동 생성. 기존 파일 번호 증가/덮어쓰기는 구현하지 않았다.
- timeout은 정확한 전체 작업 시간 상한이 아니다. 네트워크 timeout과 수신 중 경과 시간 검사를 조합했다.
- 클라이언트 timeout/종료가 서버 캡처 취소를 의미하지 않는다.

## 8. 검증된 범위와 미검증 범위

개발 환경에서 unittest 총 15개가 통과했다. 기존 캡처 API 테스트 6개는 다음과 같다:

1. health, PNG bytes 반환, 한글 경로 저장, 기존 파일 보존.
2. 토큰 오류 및 IP 제한.
3. 첫 캡처 중 두 번째 요청 Busy 거절, 큐 미적재.
4. 캡처 함수 예외 발생 후 잠금 해제 및 다음 캡처 성공.
5. 클라이언트 timeout 후 서버 복구 및 다음 캡처 성공.
6. 손상 PNG 검출, 손상 파일 미저장.

검색 테스트 9개도 통과했다:

1. 중복 검색 응답 제거 및 auto_connect 후 캡처.
2. 다른 토큰의 장비 제외/장비 없음 오류.
3. 여러 장비의 임의 선택 거절, 이름/ID 선택.
4. 같은 장비 ID의 여러 경로 중 연결 가능한 경로 선택.
5. APIPA/고정 IP broadcast 주소 계산 및 NIC 이름 필터.
6. malformed packet/잘못된 서명/프로토콜 버전 제외, responder 계속 동작.
7. 이전 nonce/유효하지 않은 포트의 응답 제외.
8. responder의 --allow-ip 제한.
9. mock psutil의 활성 Ethernet/Wi-Fi 열거 및 down/loopback 제외.

명령: `python -m unittest -v test_capture_api.py test_discovery.py`

주의: 실패 시나리오 테스트 중 ERROR/WARNING 로그는 의도한 오류 주입 결과였다. 최종 unittest 결과는 OK였다.
v1.1에서 기존 캡처 API 테스트도 함께 다시 실행했다.

아직 확인하지 않은 항목:

- 실제 MSO58B Windows의 Pillow 캡처, DPI, 잠금/원격 세션 동작.
- Windows rename 경로의 실환경 저장.
- LAN 직결 통신 및 Windows 방화벽.
- 연속 100회 캡처와 기존 측정 병행 시 안정성/성능.
- 실제 APIPA 주소 할당/Windows 복수 NIC broadcast. 자동 검색 코드는 구현하고 루프백 UDP 및 mock 기반으로 테스트했다.

## 9. 직결 LAN 자동 검색 — v1.1 구현 완료

### 실행 및 Python 사용

스코프:

```powershell
py scope_capture_server.py --discovery --name MSO58B_01
```

외부 PC에서 psutil 설치 및 스코프 capture_token.txt 복사 후:

```python
from scope_capture_client import ScopeCaptureClient
from scope_discovery import discover_scopes, list_interfaces, DiscoveryError

scope = ScopeCaptureClient.auto_connect(
    token_file='capture_token.txt',
    discovery_timeout=2,
    timeout=5,
    # interface_names=['이더넷'],  # 실제 어댑터 이름
    # name='MSO58B_01',             # 선택 조건
    # device_id='영구 UUID',        # 선택 조건
)
scope.capture_to_file('capture.png')
```

### 검색 API

```python
discover_scopes(
    timeout=2, *, token=None, token_file=None, discovery_port=8766,
    interface_names=None, interfaces=None, targets=None,
) -> list[ScopeDevice]

ScopeCaptureClient.auto_connect(
    *, token=None, token_file=None, name=None, device_id=None,
    discovery_timeout=2, timeout=5, discovery_port=8766,
    interface_names=None, interfaces=None, targets=None,
) -> ScopeCaptureClient
```

- NetworkInterface(name, address, netmask): 명시적 NIC 정보. broadcast 속성을 계산한다.
- list_interfaces(): psutil로 활성 IPv4 NIC 열거. loopback/unspecified/multicast 및 /31,/32 제외.
- 유선만 자동 판별하는 것은 아니다. Wi-Fi/가상 NIC도 포함될 수 있어 interface_names로 제한한다.
- interfaces를 직접 제공하면 psutil 없이도 검색 가능.
- targets는 테스트/진단용 명시적 대상 IPv4 목록. 기본값은 NIC별 subnet broadcast + limited broadcast이다.
- ScopeDevice 필드: device_id, name, host, port, interface, local_address.
- 발견 IP는 UDP 실제 발신 주소를 사용한다.
- 같은 (device_id, host, port)의 응답을 중복 제거한다.
- auto_connect는 다른 device_id가 여러 개면 예외. 같은 장비의 여러 경로는 health가 성공하는 순서로 선택.
- 이름이 중복되면 device_id로 선택해야 한다.
- DiscoveryError는 NIC 없음/바인딩 실패/검색 후 선택 불가/HTTP 확인 실패 등 진단에 사용한다.
- discover_scopes는 검색 응답이 없으면 빈 리스트, auto_connect는 조건에 맞는 장비가 없으면 예외.
- 검색은 제한 시간 내 재전송한다. 캡처 자동 재시도는 하지 않는다.

### 프로토콜

- UDP 8766. 요청/응답 모두 HMAC-SHA256으로 인증.
- signature 제외 JSON을 sort_keys=True, separators=(',', ':'), ensure_ascii=True로 직렬화해 서명.
- 공통 필드: service='scope-capture', version=1, type, nonce(32자리 hex), signature.
- 요청 type='discover'. 응답 type='reply', 추가 필드 name, device_id, port.
- 최대 패킷 2048 bytes, 응답 nonce/포트/이름/ID 검증.
- 토큰을 패킷에 포함하지 않는다. 공유 토큰 기반이므로 최초 토큰 전달은 필요하다.
- 다른 토큰을 가진 장비는 발견되지 않는다. 여러 토큰은 각 토큰으로 따로 검색해야 한다.
- responder는 전역 초당 최대 20응답, 동일 발신 (IP, port)에 최소 0.15초 간격 제한.
- UDP responder는 0.0.0.0에 바인딩. HTTP가 특정 IP에 바인딩된 경우 응답 경로 로컬 IP와 일치하는지 검사.
- UDP 시작 실패 시 자동 검색 없이 조용히 계속하지 않고 시작 오류로 드러난다.

### IP/방화벽 조건

- IP 설정을 자동 변경하지 않는다. 두 장비에 통신 가능한 주소가 먼저 있어야 한다.
- APIPA 169.254/16과 고정 IPv4의 broadcast 계산 모두 지원한다. 실제 주소 할당은 Windows의 역할이다.
- 케이블 연결 직후 주소가 없거나 바뀌면 사용자가 auto_connect를 다시 호출한다.
- client.host는 생성 후 자동 갱신되지 않는다. 자동 재연결/상시 검색은 없다.
- TCP 8765 및 UDP 8766을 스코프 방화벽에서 허용해야 한다.
- README는 Public으로 분류될 수 있는 직결 LAN을 위해 Profile Any + 실제 유선 InterfaceAlias + RemoteAddress LocalSubnet 규칙을 제공한다.
- --discovery는 전체 IPv4 바인딩을 기본으로 하므로 방화벽 인터페이스 범위를 함께 확인한다.
- 기존 고정 --allow-ip는 주소 변경 시 검색/HTTP 모두 차단할 수 있다. 자동 IP 환경에서는 생략한다.
- 라우터 너머 검색, IPv6, USB fallback, 자동 IP 구성은 미지원.

## 10. 다음 AI에게 권장하는 작업 순서

1. v1.1 ZIP과 이 문서의 API를 확인한다. 자동 검색을 다시 처음부터 구현할 필요가 없다.
2. 사용자에게 MSO58B의 local-test 결과를 확인한다. 아직 실장비 성공 보고는 없다.
3. 양쪽 IPv4 주소/마스크와 유선 NIC 이름, 토큰 일치 여부를 확인한다.
4. Windows 직결 환경에서 검색 → health → 실제 PNG 캡처를 검증한다.
5. 검색 실패는 NIC/IP/UDP 방화벽/토큰, 발견 후 HTTP 실패는 TCP 방화벽/bind/allow-ip로 나누어 진단한다.
6. 이후 기존 측정 프로그램과 연동하고 중복 요청·연속 100회·측정 병행을 실장비에서 확인한다.
7. 요구하지 않은 GUI, 타 언어 SDK, Tektronix 명령 기능을 추가하지 않는다.
8. Python 인터페이스를 유지하고 실장비 검증과 모의 테스트 결과를 구분한다.

## 11. 분석 시 우선 확인할 한계

- OS 캡처가 영구 정지하면 프로세스 내 캡처 lock이 계속 유지될 수 있다. 별도 프로세스/감시 복구는 미구현.
- ThreadingHTTPServer는 간단한 제한된 장비 LAN용 구현이다. 동시 연결 수의 상한은 구현하지 않았다.
- 파일시스템/서버 설정 오류 등 시작 실패는 일부 traceback으로 표시된다.
- 데스크톱 캡처는 창 뒤 데이터나 최신 취득 완료 여부를 알지 못한다.
- 자동 검색 구현 및 모의 테스트는 완료했으나 Windows 실장비 검증은 남아 있다.
- 실장비 테스트를 통과했다고 주장하지 않는다.

## 12. v1.2 추가 — 화면 안정 대기(stable)와 창 캡처(--window)

요청: 측정 프로그램(esaram5-lab/scope_tech, `scopetech/capture/screen_api.py`)에서 이 API 로 화면을 받을 때
(1) 화면 갱신을 고정 대기로 짐작하지 않고, (2) TekScope 창만 찍기. Single/정지는 측정 프로그램이 SCPI 로 처리한다.

서버
- `POST /capture?stable_ms=N&interval_ms=I&frames=F`: `capture_until_stable()` 가 I ms 간격으로 grab, F 장 연속 동일하면 그 프레임만 PNG 인코딩해 반환.
  N ms 초과 시 `ScreenNotStable` → HTTP 504. 옵션 검증(`parse_capture_options`) 실패 → 400, 캡처 전에 거절. 옵션이 없으면 기존과 같음.
- 비교 키: 실서버 `grab()` 은 `(image.size, image.tobytes())`, `CaptureServer(grab=None)` 이면 `capture()` PNG bytes 비교(테스트용).
- `--window TITLE`: `WindowTarget` 이 캡처마다 `WinWindows`(user32 EnumWindows, IsWindowVisible/IsIconic, DwmGetWindowAttribute
  DWMWA_EXTENDED_FRAME_BOUNDS → 실패 시 GetWindowRect, 주 화면으로 자름)로 제목 부분 일치(대소문자 무시, z-order 맨 앞) 창의 영역을 bbox 로 사용.
  서버 자신의 콘솔 창(GetConsoleWindow)과 제목에 scope_capture_server 가 들어간 창은 제외(터미널 제목에 명령줄이 보여 오인하는 것 방지).
  `--require-foreground` 면 GetForegroundWindow 일치 필요. 창 문제는 `WindowError` → HTTP 503 + 이유 메시지(클라이언트에 그대로 전달).
  `--list-windows` 로 제목 확인. `--bbox` 와 배타.
- health: `features: ['stable', 'window']`, `--window` 일 때 `window: {title, found, matched, foreground, rect}` (실패해도 예외 없음).

클라이언트
- `capture_png(stable_ms=0, interval_ms=None, frames=None)`, `capture_to_file(path, **같은 옵션)`, `capture_scope(..., stable_ms=...)`, CLI `--stable-ms`.
- `CaptureUnstable(CaptureError)` (504). 400/503 은 서버 메시지 포함. 옵션을 보냈는데 404 면 "서버 업데이트 필요".
- stable_ms 를 timeout 에 더해 요청한다.

검증: unittest 26개 통과(가짜 grab/가짜 창 목록). 실제 Windows 창 열거·DPI·DWM 경계, TekScope 창 제목, 실화면 안정 시간은 미검증.

