"""Run on the controller PC; edit SCOPE_IP and token path first."""
from datetime import datetime
from pathlib import Path
from scope_capture_client import ScopeCaptureClient, CaptureBusy, CaptureError

SCOPE_IP = '192.168.0.10'


def main():
    scope = ScopeCaptureClient(
        SCOPE_IP,
        token_file=Path(__file__).with_name('capture_token.txt'),
        timeout=5,
    )
    print(scope.health())
    # Call after your existing measurement and display update have completed.
    filename = datetime.now().strftime('scope_%Y%m%d_%H%M%S_%f.png')
    try:
        print('Saved:', scope.capture_to_file(Path('captures') / filename))
    except CaptureBusy:
        print('Capture busy. This request was not queued.')
    except (CaptureError, OSError) as error:
        print('Capture failed:', error)


if __name__ == '__main__':
    main()
