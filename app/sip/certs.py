"""Where this computer keeps its trusted CA certificates.

No PJSIP here. PJSIP trusts nothing unless given a CA bundle file. Python's
own default works when Python comes from the distro, but a bundled Python
(the AppImage) was built elsewhere and looks in the wrong place, so we also
check where the common distros keep the bundle.
"""

import os
import ssl
from pathlib import Path

KNOWN_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",                # Debian, Ubuntu, Arch, Gentoo
    "/etc/pki/tls/certs/ca-bundle.crt",                  # Fedora, RHEL
    "/etc/ssl/ca-bundle.pem",                            # openSUSE
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # Fedora/RHEL, newer layout
    "/etc/ssl/cert.pem",                                 # Alpine, macOS-style
)


def system_ca_file() -> str:
    """The system CA bundle's path, or "" if none is found."""
    candidates = [os.environ.get("SSL_CERT_FILE", ""), ssl.get_default_verify_paths().cafile or ""]
    candidates += KNOWN_BUNDLES
    for path in candidates:
        if path and Path(path).is_file():
            return path
    return ""
