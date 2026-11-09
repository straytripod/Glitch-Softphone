"""Move folders left by older versions to where this version looks.

The app was called Glitch SIP, with folders named "glitch-sip". Each old
folder is moved to the new name, and a link with the old name is left
pointing at it: config.toml may still name files by their old path (e.g.
tls_ca_file = "~/.config/glitch-sip/pbx-ca.pem"), and the app never edits
that file.
"""

from pathlib import Path

from app import APP_ID

OLD_IDS = ("glitch-sip",)
BASES = (Path(".config"), Path(".local/share"), Path(".local/state"))


def migrate_old_folders(home: Path | None = None) -> list[str]:
    """Do the moves; returns what was done, for the log (logging isn't set up yet)."""
    home = home or Path.home()
    done = []
    for base in BASES:
        new = home / base / APP_ID
        for old_id in OLD_IDS:
            old = home / base / old_id
            if old.is_symlink() or not old.is_dir() or new.exists():
                continue   # nothing to move, already moved, or both exist: leave them be
            try:
                old.rename(new)
                old.symlink_to(new.name)   # relative: the folders sit side by side
            except OSError as e:
                done.append(f"Could not move {old} to {new}: {e}")
            else:
                done.append(f"Moved {old} to {new} (old name kept as a link)")
    return done
