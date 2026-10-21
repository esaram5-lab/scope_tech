"""Controller PC: pip install psutil, then copy the scope's capture_token.txt here."""
from datetime import datetime
from pathlib import Path
from scope_capture_client import ScopeCaptureClient
from scope_discovery import list_interfaces, discover_scopes


def main():
    token_file = Path(__file__).with_name('capture_token.txt')
    print('IPv4 interfaces:', list_interfaces())
    # Optional: interface_names=['Ethernet'] or ['이더넷'] using an actual name above.
    scope = ScopeCaptureClient.auto_connect(
        token_file=token_file,
        discovery_timeout=2,
        timeout=5,
        # name='MSO58B_01',
    )
    print('Connected:', scope.host, scope.port)
    name = datetime.now().strftime('scope_%Y%m%d_%H%M%S_%f.png')
    print('Saved:', scope.capture_to_file(Path('captures') / name))


if __name__ == '__main__':
    main()
