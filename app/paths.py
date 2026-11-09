"""Where the app keeps its files, following the XDG Base Directory spec.

Normally ~/.config, ~/.local/share and ~/.local/state. Flatpak points the
XDG_* variables into the app's own folder (~/.var/app/<id>/...), the only
place the sandbox lets it write, so the variables win when they're set.
"""

import os
from pathlib import Path

from app import APP_ID


def _base(variable: str, default: str) -> Path:
    value = os.environ.get(variable, "")
    # The spec says to ignore a relative path.
    return Path(value) if os.path.isabs(value) else Path.home() / default


CONFIG_DIR = _base("XDG_CONFIG_HOME", ".config") / APP_ID
DATA_DIR = _base("XDG_DATA_HOME", ".local/share") / APP_ID
STATE_DIR = _base("XDG_STATE_HOME", ".local/state") / APP_ID
