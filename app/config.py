"""Load settings from TOML files into plain dataclasses.

Every part of the app reads settings from the AppConfig object built here,
so there is exactly one place that knows about the file format.

Two files live side by side:
  config.toml    hand-edited (account, password, logging); the app never writes it
  settings.toml  written by the app's settings dialogs; overrides config.toml
Keeping them apart means the app never throws away the comments in a
hand-written config.toml. The account (with its password) goes into
settings.toml only once it's set in Settings > Account…; both files are
readable by their owner only.
"""

import json
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from app.presence import DEFAULT_STATUS, STATUS_NAMES
from app import ringtones, routing
from app.paths import CONFIG_DIR

DEFAULT_CONFIG_PATH = CONFIG_DIR / "config.toml"
SETTINGS_FILE_NAME = "settings.toml"

VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
VALID_TRANSPORTS = ("udp", "tcp", "tls")
VALID_SRTP = ("required", "optional", "off")
VALID_THEMES = ("system", "light", "dark")
DEFAULT_PORTS = {"udp": 5060, "tcp": 5060, "tls": 5061}
# Best first: Opus and G.722 are wideband (HD), PCMU/PCMA the old phone sound.
DEFAULT_CODECS = ("opus", "G722", "PCMU", "PCMA")


class ConfigError(Exception):
    """Raised when the config file exists but contains bad values."""


@dataclass
class AccountConfig:
    server: str = ""
    port: int = 0                 # 0 = the transport's usual port (5060, TLS 5061)
    transport: str = "udp"
    extension: str = ""
    auth_username: str = ""
    password: str = ""
    display_name: str = ""
    # TLS only. The CA certificate (PEM) that signed the PBX's certificate;
    # empty = the system's trusted CAs (enough for e.g. Let's Encrypt).
    tls_ca_file: str = ""
    tls_verify: bool = True       # refuse a PBX whose certificate doesn't check out
    # Call audio encryption, TLS only (the keys travel in the SIP messages, so
    # SRTP over plain UDP/TCP would hand them to anyone listening).
    srtp: str = "required"

    @property
    def sip_port(self) -> int:
        return self.port or DEFAULT_PORTS[self.transport]

    @property
    def srtp_mode(self) -> str:
        """The SRTP setting that actually applies: always "off" without TLS."""
        return self.srtp if self.transport == "tls" else "off"


@dataclass
class AudioConfig:
    # PipeWire device (node) names; empty means the desktop's default device.
    # List them with:  python -m app.sip.audio
    speaker: str = ""
    microphone: str = ""
    ringer: str = ""      # where incoming calls ring; empty = same as speaker
    # A built-in ring (app/ringtones.py PRESETS) or "custom" for ringtone_file,
    # the app's mono copy of a WAV the user chose.
    ringtone: str = ringtones.DEFAULT_RINGTONE
    ringtone_file: str = ""
    # Codecs offered on calls, best first; others are turned off. The PBX
    # takes the first one on this list that it also has.
    codecs: list[str] = field(default_factory=lambda: list(DEFAULT_CODECS))


@dataclass
class PhoneConfig:
    # A second call during a call beeps and can be answered (the first goes
    # on hold). Off = the PBX gets 486 Busy Here (usually -> voicemail).
    call_waiting: bool = True
    # Dialed in front of an extension to pick up a call ringing there
    # (FusionPBX's directed pickup / "intercept-ext" feature code).
    pickup_prefix: str = "**"
    # Answer intercom and paging calls by themselves (when not on a call).
    auto_answer: bool = True
    # Dialed by the Voicemail button: FusionPBX's "my own mailbox" code.
    voicemail_code: str = "*97"
    # Dialed in front of an extension by the Intercom button (FusionPBX's
    # page-extension dialplan entry; see docs/FUSIONPBX.md for two-way).
    intercom_prefix: str = "*8"


@dataclass
class UiConfig:
    close_to_tray: bool = True    # closing the window keeps the phone running
    notifications: bool = True    # desktop notifications for incoming/missed calls
    theme: str = "system"         # "system" follows the desktop's light/dark setting


@dataclass
class PresenceConfig:
    status: str = DEFAULT_STATUS   # my status, one of app.presence.STATUS_NAMES


@dataclass
class RoutingConfig:
    """Where the PBX sends my calls for each status (app/routing.py)."""
    # status -> route ("voicemail", "forward:5551234"); missing = "ring"
    rules: dict[str, str] = field(default_factory=dict)
    # The route the PBX was last told to use, so the next change knows which
    # code undoes it. Kept up to date by the app.
    applied: str = routing.RING


@dataclass
class DirectoryConfig:
    """Colleagues' names from the PBX's phone directory (app/directory.py)."""
    enabled: bool = True
    mac: str = ""          # the device MAC the PBX knows us by; empty = this computer's
    # SHA-256 fingerprint of the PBX web certificate the user chose to trust
    # (only needed when it isn't properly signed, e.g. self-signed)
    web_cert: str = ""


@dataclass
class AgentConfig:
    """FusionPBX call-center agent controls (feature codes)."""
    enabled: bool = False
    agent_id: str = ""        # empty = same as the account's extension
    password: str = ""        # the agent's PIN, keyed in after the code
    login_code: str = "*22"   # toggles Logged Out <-> Available
    break_code: str = "*24"   # toggles Available <-> On Break (custom dialplan)


@dataclass
class ParkConfig:
    """FusionPBX valet park slots shown in the Parked calls panel (app/park.py)."""
    enabled: bool = True
    first_slot: int = 5901
    slots: int = 4            # how many slots to watch, from first_slot up


@dataclass
class BlfEntry:
    """One extension watched in the busy lamp field."""
    extension: str
    label: str = ""


@dataclass
class PageGroup:
    """A FusionPBX page group (Apps -> Paging) on the Page button."""
    number: str
    name: str = ""


@dataclass
class LoggingConfig:
    level: str = "INFO"
    sip_trace: bool = False


@dataclass
class AppConfig:
    account: AccountConfig = field(default_factory=AccountConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    phone: PhoneConfig = field(default_factory=PhoneConfig)
    blf: list[BlfEntry] = field(default_factory=list)
    page_groups: list[PageGroup] = field(default_factory=list)
    agent: AgentConfig = field(default_factory=AgentConfig)
    park: ParkConfig = field(default_factory=ParkConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    presence: PresenceConfig = field(default_factory=PresenceConfig)
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    directory: DirectoryConfig = field(default_factory=DirectoryConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    # Where the settings dialogs save; set by load_config().
    settings_path: Path | None = field(default=None, compare=False)
    # The account was set in the app (Settings > Account…), so settings.toml
    # holds it; otherwise it comes from config.toml and isn't copied over.
    account_in_settings: bool = field(default=False, compare=False)


def _build_section(cls, data: dict, section_name: str):
    """Create a dataclass from a TOML table, rejecting unknown keys.

    Unknown keys are almost always typos (e.g. "pasword"), so failing loudly
    beats silently ignoring them.
    """
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(
            f"Unknown setting(s) in [{section_name}]: {', '.join(sorted(unknown))}"
        )
    return cls(**data)


def parse_config(data: dict) -> AppConfig:
    """Turn an already-parsed TOML dict into a validated AppConfig."""
    config = AppConfig(
        account=_build_section(AccountConfig, data.get("account", {}), "account"),
        audio=_build_section(AudioConfig, data.get("audio", {}), "audio"),
        phone=_build_section(PhoneConfig, data.get("phone", {}), "phone"),
        blf=_build_blf(data.get("blf", [])),
        page_groups=_build_page_groups(data.get("page_group", [])),
        agent=_build_section(AgentConfig, data.get("agent", {}), "agent"),
        park=_build_section(ParkConfig, data.get("park", {}), "park"),
        ui=_build_section(UiConfig, data.get("ui", {}), "ui"),
        presence=_build_section(PresenceConfig, data.get("presence", {}), "presence"),
        routing=_build_section(RoutingConfig, data.get("routing", {}), "routing"),
        directory=_build_section(DirectoryConfig, data.get("directory", {}), "directory"),
        logging=_build_section(LoggingConfig, data.get("logging", {}), "logging"),
    )

    config.logging.level = config.logging.level.upper()
    if config.logging.level not in VALID_LOG_LEVELS:
        raise ConfigError(
            f"[logging] level must be one of {', '.join(VALID_LOG_LEVELS)}"
        )

    _check_account(config.account)
    _check_audio(config.audio)
    _check_agent(config.agent)
    _check_presence(config.presence)
    _check_routing(config.routing)
    _check_directory(config.directory)
    _check_ui(config.ui)
    _check_park(config.park)
    return config


def _check_park(park: ParkConfig):
    # FusionPBX's park dialplan matches *5901 to *5999.
    if not (5901 <= park.first_slot and park.first_slot + park.slots - 1 <= 5999 and park.slots >= 1):
        raise ConfigError("[park] slots must lie within 5901-5999 (first_slot, slots >= 1)")


def _check_ui(ui: UiConfig):
    ui.theme = ui.theme.lower()
    if ui.theme not in VALID_THEMES:
        raise ConfigError(f"[ui] theme must be one of {', '.join(VALID_THEMES)}")


def _check_directory(directory: DirectoryConfig):
    directory.mac = directory.mac.strip().lower()
    digits = directory.mac.replace(":", "").replace("-", "")
    if directory.mac and (len(digits) != 12 or any(c not in "0123456789abcdef" for c in digits)):
        raise ConfigError("[directory] mac must look like 00:15:65:aa:bb:cc")
    directory.web_cert = directory.web_cert.strip().upper()


def _check_presence(presence: PresenceConfig):
    if presence.status not in STATUS_NAMES:
        raise ConfigError(f"[presence] status must be one of {', '.join(STATUS_NAMES)}")


def _check_routing(config: RoutingConfig):
    if not isinstance(config.rules, dict):
        raise ConfigError("[routing.rules] must be a table of status = route")
    for status, route in config.rules.items():
        if status not in STATUS_NAMES:
            raise ConfigError(f"[routing.rules] unknown status {status!r}")
        if not isinstance(route, str) or not routing.is_valid(route):
            raise ConfigError(f"[routing.rules] {status}: route must be \"ring\", "
                              "\"voicemail\" or \"forward:<2-20 digits>\"")
    if not routing.is_valid(config.applied):
        raise ConfigError("[routing] applied must be a valid route")
    # "ring" rules are the default; dropping them keeps settings.toml short.
    config.rules = {s: r for s, r in config.rules.items() if r != routing.RING}


def _check_account(account: AccountConfig):
    account.transport = account.transport.lower()
    if account.transport not in VALID_TRANSPORTS:
        raise ConfigError(f"[account] transport must be one of {', '.join(VALID_TRANSPORTS)}")
    account.srtp = account.srtp.lower()
    if account.srtp not in VALID_SRTP:
        raise ConfigError(f"[account] srtp must be one of {', '.join(VALID_SRTP)}")
    if account.tls_ca_file:
        account.tls_ca_file = str(Path(account.tls_ca_file).expanduser())
        if account.transport == "tls" and not Path(account.tls_ca_file).is_file():
            raise ConfigError(f"[account] tls_ca_file not found: {account.tls_ca_file}")


def _check_agent(agent: AgentConfig):
    # The PIN is keyed in as DTMF, so only keypad characters make sense.
    if any(c not in "0123456789*#" for c in agent.password):
        raise ConfigError("[agent] password may only contain digits, * and #")


def _check_audio(audio: AudioConfig):
    for name in ("speaker", "microphone", "ringer"):
        # Device names end up quoted inside an ALSA config file (app/sip/audio.py).
        if any(c in getattr(audio, name) for c in '"\\\n'):
            raise ConfigError(f"[audio] {name} contains a quote, backslash or newline")
    if not ringtones.is_valid(audio.ringtone):
        raise ConfigError(f"[audio] ringtone must be one of {', '.join(ringtones.PRESETS)} "
                          f"or \"{ringtones.CUSTOM}\"")
    if (not isinstance(audio.codecs, list) or not audio.codecs
            or not all(isinstance(c, str) and c.strip() for c in audio.codecs)):
        raise ConfigError('[audio] codecs must be a list of codec names, e.g. ["opus", "G722"]')


def _build_blf(items) -> list[BlfEntry]:
    """[[blf]] tables -> entries; blank and repeated extensions are dropped."""
    if not isinstance(items, list):
        raise ConfigError("blf must be a list of [[blf]] tables")
    entries, seen = [], set()
    for item in items:
        entry = _build_section(BlfEntry, item, "blf")
        entry.extension = str(entry.extension).strip()
        entry.label = str(entry.label).strip()
        if entry.extension and entry.extension not in seen:
            seen.add(entry.extension)
            entries.append(entry)
    return entries


def _build_page_groups(items) -> list[PageGroup]:
    """[[page_group]] tables -> groups; blank and repeated numbers are dropped."""
    if not isinstance(items, list):
        raise ConfigError("page_group must be a list of [[page_group]] tables")
    groups, seen = [], set()
    for item in items:
        group = _build_section(PageGroup, item, "page_group")
        group.number, group.name = str(group.number).strip(), str(group.name).strip()
        if group.number and group.number not in seen:
            seen.add(group.number)
            groups.append(group)
    return groups


def parse_settings(data: dict, config: AppConfig):
    """Apply an already-parsed settings.toml on top of `config`."""
    unknown = set(data) - {"account", "audio", "phone", "blf", "page_group", "ui", "agent",
                           "presence", "routing", "directory"}
    if unknown:
        raise ConfigError(f"Unknown section(s) in settings: {', '.join(sorted(unknown))}")
    if "account" in data:
        config.account = _build_section(AccountConfig, data["account"], "account")
        _check_account(config.account)
        config.account_in_settings = True
    if "audio" in data:
        # The dialogs don't write codecs, so keep config.toml's.
        current = {f.name: getattr(config.audio, f.name) for f in fields(AudioConfig)}
        config.audio = _build_section(AudioConfig, current | data["audio"], "audio")
        _check_audio(config.audio)
    if "phone" in data:
        config.phone = _build_section(PhoneConfig, data["phone"], "phone")
    if "blf" in data:
        config.blf = _build_blf(data["blf"])
    if "page_group" in data:
        config.page_groups = _build_page_groups(data["page_group"])
    if "ui" in data:
        config.ui = _build_section(UiConfig, data["ui"], "ui")
        _check_ui(config.ui)
    if "agent" in data:
        config.agent = _build_section(AgentConfig, data["agent"], "agent")
        _check_agent(config.agent)
    if "presence" in data:
        config.presence = _build_section(PresenceConfig, data["presence"], "presence")
        _check_presence(config.presence)
    if "routing" in data:
        config.routing = _build_section(RoutingConfig, data["routing"], "routing")
        _check_routing(config.routing)
    if "directory" in data:
        config.directory = _build_section(DirectoryConfig, data["directory"], "directory")
        _check_directory(config.directory)


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """Read config.toml plus the settings.toml next to it. Missing files
    just mean defaults."""
    config = parse_config(_read_toml(path)) if path.exists() else AppConfig()
    config.settings_path = path.parent / SETTINGS_FILE_NAME
    if config.settings_path.exists():
        try:
            parse_settings(_read_toml(config.settings_path), config)
        except ConfigError as e:
            raise ConfigError(f"{config.settings_path}: {e}") from e
    return config


def _toml_string(value: str) -> str:
    # JSON string escapes are also valid TOML basic-string escapes.
    return json.dumps(value, ensure_ascii=False)


def save_settings(config: AppConfig):
    """Write the app-managed settings ([account] once set in the app,
    [audio], [phone], [ui], [agent], [presence], [routing], [directory],
    [[blf]], [[page_group]]) to settings.toml.

    Writes a temp file and renames it over the old one, so a crash mid-write
    can't leave a half-written file. The file holds the SIP password and
    agent PIN, so it is readable by the owner only (0600), like config.toml
    should be.
    """
    path = config.settings_path
    audio = config.audio
    text = (
        "# Written by Glitch Softphone's settings dialog; overrides config.toml.\n"
        "# Safe to edit by hand while the app is closed (comments are not kept).\n"
    )
    if config.account_in_settings:
        a = config.account
        text += (
            "\n[account]\n"
            f"server = {_toml_string(a.server)}\n"
            f"port = {a.port}\n"
            f"transport = {_toml_string(a.transport)}\n"
            f"extension = {_toml_string(a.extension)}\n"
            f"auth_username = {_toml_string(a.auth_username)}\n"
            f"password = {_toml_string(a.password)}\n"
            f"display_name = {_toml_string(a.display_name)}\n"
            f"tls_ca_file = {_toml_string(a.tls_ca_file)}\n"
            f"tls_verify = {'true' if a.tls_verify else 'false'}\n"
            f"srtp = {_toml_string(a.srtp)}\n"
        )
    text += (
        "\n[audio]\n"
        f"speaker = {_toml_string(audio.speaker)}\n"
        f"microphone = {_toml_string(audio.microphone)}\n"
        f"ringer = {_toml_string(audio.ringer)}\n"
        f"ringtone = {_toml_string(audio.ringtone)}\n"
        f"ringtone_file = {_toml_string(audio.ringtone_file)}\n"
        "\n[phone]\n"
        f"call_waiting = {'true' if config.phone.call_waiting else 'false'}\n"
        f"pickup_prefix = {_toml_string(config.phone.pickup_prefix)}\n"
        f"auto_answer = {'true' if config.phone.auto_answer else 'false'}\n"
        f"voicemail_code = {_toml_string(config.phone.voicemail_code)}\n"
        f"intercom_prefix = {_toml_string(config.phone.intercom_prefix)}\n"
        "\n[ui]\n"
        f"close_to_tray = {'true' if config.ui.close_to_tray else 'false'}\n"
        f"notifications = {'true' if config.ui.notifications else 'false'}\n"
        f"theme = {_toml_string(config.ui.theme)}\n"
        "\n[agent]\n"
        f"enabled = {'true' if config.agent.enabled else 'false'}\n"
        f"agent_id = {_toml_string(config.agent.agent_id)}\n"
        f"password = {_toml_string(config.agent.password)}\n"
        f"login_code = {_toml_string(config.agent.login_code)}\n"
        f"break_code = {_toml_string(config.agent.break_code)}\n"
        "\n[presence]\n"
        f"status = {_toml_string(config.presence.status)}\n"
        "\n[routing]\n"
        f"applied = {_toml_string(config.routing.applied)}\n"
        "\n[routing.rules]\n"
        + "".join(f"{_toml_string(status)} = {_toml_string(route)}\n"
                  for status, route in config.routing.rules.items()) +
        "\n[directory]\n"
        f"enabled = {'true' if config.directory.enabled else 'false'}\n"
        f"mac = {_toml_string(config.directory.mac)}\n"
        f"web_cert = {_toml_string(config.directory.web_cert)}\n"
    )
    for entry in config.blf:
        text += (
            "\n[[blf]]\n"
            f"extension = {_toml_string(entry.extension)}\n"
            f"label = {_toml_string(entry.label)}\n"
        )
    for group in config.page_groups:
        text += (
            "\n[[page_group]]\n"
            f"number = {_toml_string(group.number)}\n"
            f"name = {_toml_string(group.name)}\n"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    # Create it owner-only from the start, so the PIN is never briefly readable.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o600)   # in case an old .tmp existed with wider permissions
    os.replace(tmp, path)
