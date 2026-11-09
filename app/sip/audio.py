"""Route PJSIP's audio to specific PipeWire devices. No pjsua2 or Qt here.

PJSIP's ALSA driver only sees 'pipewire' and 'default', and both follow the
desktop's default device. To send the call to the headset but the ring to the
speakers, we write a small ALSA config file defining our own named devices
(glitch_speaker, glitch_mic, glitch_ringer), each pinned to a PipeWire node,
and add it to ALSA_CONFIG_PATH. PJSIP then finds them like any other device.
The variable is set inside this process only, so the system's audio setup is
never touched.

Run  python -m app.sip.audio  to list the device names for config.toml.
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app import APP_ID
from app.config import AudioConfig

log = logging.getLogger(__name__)

SPEAKER_DEV = "glitch_speaker"
MIC_DEV = "glitch_mic"
RINGER_DEV = "glitch_ringer"

SYSTEM_ALSA_CONFIG = Path("/usr/share/alsa/alsa.conf")
# Per-login tmpfs; the file is rewritten at every start, so it needn't persist.
ALSA_CONFIG_FILE = (
    Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / APP_ID / "alsa.conf"
)


@dataclass(frozen=True)
class AudioDevice:
    name: str          # PipeWire node name: what goes in config.toml
    description: str   # what people see, e.g. "USB Headset"
    is_output: bool    # True = speaker/headphones, False = microphone


def parse_pw_dump(objects: list) -> list[AudioDevice]:
    """Pick the speakers and microphones out of `pw-dump` JSON output."""
    devices = []
    for obj in objects:
        props = (obj.get("info") or {}).get("props") or {}
        media_class = props.get("media.class")
        name = props.get("node.name")
        if media_class not in ("Audio/Sink", "Audio/Source") or not name:
            continue
        description = props.get("node.description") or name
        devices.append(AudioDevice(name, description, media_class == "Audio/Sink"))
    return sorted(devices, key=lambda d: (not d.is_output, d.description.lower()))


def list_devices() -> list[AudioDevice]:
    """Ask PipeWire for its speakers and microphones right now.
    Returns an empty list if PipeWire's pw-dump tool isn't available."""
    if shutil.which("pw-dump") is None:
        log.warning("pw-dump not found; can't list audio devices")
        return []
    try:
        result = subprocess.run(
            ["pw-dump"], capture_output=True, text=True, timeout=5, check=True,
        )
        return parse_pw_dump(json.loads(result.stdout))
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        log.warning("Listing audio devices failed: %s", e)
        return []


def _pcm(name: str, description: str, node_key: str, node: str) -> str:
    # Without a node the PipeWire plugin follows the desktop's default device.
    # config.py has already rejected names containing quotes or backslashes.
    lines = [f"pcm.{name} {{", "    type pipewire"]
    if node:
        lines.append(f'    {node_key} "{node}"')
    lines.append(f'    hint {{ show on description "{description}" }}')
    lines.append("}")
    return "\n".join(lines)


def alsa_config_text(audio: AudioConfig) -> str:
    return "\n\n".join([
        _pcm(SPEAKER_DEV, "Glitch Softphone speaker", "playback_node", audio.speaker),
        _pcm(MIC_DEV, "Glitch Softphone microphone", "capture_node", audio.microphone),
        _pcm(RINGER_DEV, "Glitch Softphone ringer", "playback_node", audio.ringer or audio.speaker),
    ]) + "\n"


def install_alsa_config(audio: AudioConfig, path: Path = ALSA_CONFIG_FILE) -> bool:
    """Write our devices to `path` and make ALSA (in this process) load it.

    Must run before PJSIP starts. ALSA re-reads the file when it changes, so
    calling it again later with new settings also works. Returns False if
    the system's ALSA config isn't where we expect, in which case PJSIP just
    uses the default devices.
    """
    current = os.environ.get("ALSA_CONFIG_PATH")
    if not current and not SYSTEM_ALSA_CONFIG.exists():
        log.warning("No %s; using default audio devices", SYSTEM_ALSA_CONFIG)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-rename gives the file a new inode, which ALSA's
    # "did the config change?" check reliably notices.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(alsa_config_text(audio))
    os.replace(tmp, path)
    base = current or str(SYSTEM_ALSA_CONFIG)
    if str(path) not in base.split(":"):
        os.environ["ALSA_CONFIG_PATH"] = f"{base}:{path}"
    return True


def _print_devices():
    devices = list_devices()
    if not devices:
        print("No PipeWire audio devices found.")
        return
    print("Copy a name into the [audio] section of config.toml.\n")
    for device in devices:
        kind = "speaker" if device.is_output else "mic    "
        print(f"{kind}  {device.name}\n         ({device.description})")


if __name__ == "__main__":
    _print_devices()
