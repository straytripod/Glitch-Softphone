"""Colleagues' names from the PBX's phone directory.

No Qt or PJSIP here. FusionPBX hands every desk phone a directory through
its provisioning service; we download it the way a Yealink phone would, so
the PBX needs only standard settings (see docs/FUSIONPBX.md):
  https://<pbx>/app/provision/?mac=<device MAC>&file=directory.xml&contacts=extensions

The PBX finds "the phone" by its MAC address, so the admin adds this
computer as a device. local_mac() works out which address that is.

The device's own config file (Yealink {mac}.cfg) carries its BLF keys,
set in FusionPBX under Devices -> Keys. It also carries the line's SIP
password, so parse_blf_keys() keeps only the BLF key lines; nothing else
from that file is kept or logged.
"""

import fcntl
import hashlib
import os
import re
import socket
import struct
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from urllib.parse import urlencode

SYS_NET = Path("/sys/class/net")
SIOCGIFADDR = 0x8915   # Linux ioctl: an interface's IPv4 address
NO_MAC = "00:00:00:00:00:00"

BLF_KEY_TYPE = "16"   # Yealink's key type for BLF
# linekey.2.value = 102, memorykey.1.type = 16, expansion_module.1.key.3.label = Ann
_KEY_LINE = re.compile(
    r"^(linekey|memorykey|expansion_module)\.(\d+)(?:\.key\.(\d+))?\.(type|value|label)[ \t]*=[ \t]*(.*?)[ \t]*$",
    re.MULTILINE)
_KEY_ORDER = {"linekey": 0, "memorykey": 1, "expansion_module": 2}
_EXTENSION = re.compile(r"[0-9A-Za-z*#+_.-]{1,32}")


class DirectoryError(Exception):
    """The PBX's answer wasn't a directory we can read."""


def directory_url(server: str, mac: str, contacts: str = "extensions") -> str:
    """contacts: "extensions" (colleagues) or "all" (also the PBX's Contacts app)."""
    query = urlencode({"mac": mac.replace(":", "").lower(),
                       "file": "directory.xml", "contacts": contacts})
    return f"https://{server}/app/provision/?{query}"


def device_config_url(server: str, mac: str) -> str:
    """The device's own config file: with no file= FusionPBX serves the
    template's {$mac}.cfg."""
    return f"https://{server}/app/provision/?{urlencode({'mac': mac.replace(':', '').lower()})}"


def parse_blf_keys(text: str) -> list[tuple[str, str]]:
    """Yealink device config -> [(extension, label)] for its BLF keys, in
    key order: line keys, then memory keys, then expansion modules."""
    keys = {}
    for kind, first, second, field, value in _KEY_LINE.findall(text):
        # linekey.N / memorykey.N, or expansion_module.M.key.N
        where = (_KEY_ORDER[kind], int(first), int(second or 0))
        keys.setdefault(where, {})[field] = value
    entries, seen = [], set()
    for where in sorted(keys):
        key = keys[where]
        if key.get("type") != BLF_KEY_TYPE:
            continue
        extension = key.get("value", "").partition("@")[0]   # "102" or "102@domain"
        if _EXTENSION.fullmatch(extension) and extension not in seen:
            seen.add(extension)
            entries.append((extension, key.get("label", "")))
    return entries


def parse_entries(text: str) -> list[tuple[str, list[str]]]:
    """Yealink directory XML -> [(name, [numbers])]. Entries without a name
    or number are skipped."""
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as e:
        raise DirectoryError(f"not a directory: {e}") from e
    entries = []
    for entry in root.iter("DirectoryEntry"):
        name = " ".join((entry.findtext("Name") or "").split())   # also tidies "Bob " etc.
        numbers = [n for n in ((p.text or "").strip() for p in entry.iter("Telephone")) if n]
        if name and numbers:
            entries.append((name, numbers))
    return entries


def parse_directory(text: str) -> dict[str, str]:
    """Yealink directory XML -> {number: name}."""
    names = {}
    for name, numbers in parse_entries(text):
        for number in numbers:
            names.setdefault(number, name)
    return names


def fingerprint(der: bytes) -> str:
    """SHA-256 of a certificate, as "AB:CD:…" (the format openssl prints)."""
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def local_mac(server: str, port: int = 443) -> str:
    """MAC address ("2c:cf:…") of the network card used to reach `server`,
    or "" if it can't be found."""
    try:
        # Connecting a UDP socket only picks a route; nothing is sent.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((server, port))
            local_ip = s.getsockname()[0]
    except OSError:
        return ""
    try:
        interfaces = sorted(os.listdir(SYS_NET))
    except OSError:
        return ""
    for name in interfaces:
        if _ipv4_address(name) == local_ip:
            mac = _read(SYS_NET / name / "address")
            return "" if mac == NO_MAC else mac
    return ""


def _ipv4_address(interface: str) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            packed = fcntl.ioctl(s.fileno(), SIOCGIFADDR,
                                 struct.pack("256s", interface[:15].encode()))
        except OSError:
            return ""   # no IPv4 address (or not up)
    return socket.inet_ntoa(packed[20:24])


def _read(path: Path) -> str:
    try:
        return path.read_text().strip().lower()
    except OSError:
        return ""
