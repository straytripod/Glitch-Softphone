"""Desktop notifications with buttons (freedesktop.org Notifications over D-Bus).

Qt's tray balloons can't have buttons, so we talk to the desktop's
notification service directly. Receiving its signals (a button was pressed,
a notification closed) works with QtDBus. Sending is done with the `gdbus`
tool: PySide6 can only send signed integers, and the service insists on
unsigned ones for notification ids; gdbus looks up the right types itself.
gdbus comes with GLib, so it's on every GNOME desktop.
"""

import logging
import re
import shutil
import subprocess

from PySide6.QtCore import QObject, Signal, Slot, SLOT
from PySide6.QtDBus import QDBusConnection

log = logging.getLogger(__name__)

SERVICE = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"
INTERFACE = "org.freedesktop.Notifications"
URGENCY_NORMAL, URGENCY_CRITICAL = 1, 2


def gvariant_string(text: str) -> str:
    """A string in GVariant text format, which is how gdbus reads arguments."""
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def notify_command(app_name: str, icon: str, summary: str, body: str,
                   actions: list[tuple[str, str]], urgency: int, timeout_ms: int,
                   replaces_id: int = 0) -> list[str]:
    """The gdbus command line for Notify."""
    flat = []
    for key, label in actions:
        flat += [gvariant_string(key), gvariant_string(label)]
    return [
        "gdbus", "call", "--session", "--dest", SERVICE, "--object-path", PATH,
        "--method", f"{INTERFACE}.Notify",
        "--",   # so a timeout of -1 (server default) isn't read as an option
        gvariant_string(app_name), str(replaces_id), gvariant_string(icon),
        gvariant_string(summary), gvariant_string(body),
        "[" + ", ".join(flat) + "]" if flat else "@as []",
        f"{{'urgency': <byte {urgency}>}}",
        str(timeout_ms),
    ]


def parse_notify_reply(output: str) -> int | None:
    """gdbus prints "(uint32 12,)" for Notify; return 12."""
    match = re.search(r"uint32 (\d+)", output)
    return int(match.group(1)) if match else None


class Notifier(QObject):
    # (notification id, action key) a button was pressed; "default" = body clicked
    action_invoked = Signal(int, str)

    def __init__(self, app_name: str, icon_path: str = "", parent=None):
        super().__init__(parent)
        self._app_name = app_name
        self._icon = icon_path
        self._mine: set[int] = set()   # ids of notifications we showed
        bus = QDBusConnection.sessionBus()
        self.available = bool(shutil.which("gdbus")) and bus.isConnected()
        if self.available:
            # Any sender (""), not SERVICE: on GNOME a helper process (gjs)
            # owns the service name, but gnome-shell itself sends the button
            # signals, so a filter on the name's owner drops every click.
            # Clicks on other apps' notifications arrive too; _on_action
            # keeps only ours.
            bus.connect("", PATH, INTERFACE, "ActionInvoked", self,
                        SLOT("_on_action(uint,QString)"))
        else:
            log.warning("Desktop notifications unavailable (no gdbus or no session bus)")

    def notify(self, summary: str, body: str, actions: list[tuple[str, str]] = (),
               urgency: int = URGENCY_NORMAL, timeout_ms: int = -1) -> int | None:
        """Show a notification; returns its id (for close()), or None."""
        if not self.available:
            return None
        command = notify_command(self._app_name, self._icon, summary, body,
                                 list(actions), urgency, timeout_ms)
        output = self._run(command)
        notification_id = parse_notify_reply(output) if output else None
        if notification_id is not None:
            self._mine.add(notification_id)
        return notification_id

    def close(self, notification_id: int | None):
        self._mine.discard(notification_id)
        if self.available and notification_id is not None:
            self._run(["gdbus", "call", "--session", "--dest", SERVICE, "--object-path", PATH,
                       "--method", f"{INTERFACE}.CloseNotification", str(notification_id)])

    @staticmethod
    def _run(command: list[str]) -> str:
        # Short and local (a few ms); a hung desktop mustn't hang the phone, hence the timeout.
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=2, check=True)
            return result.stdout
        except subprocess.CalledProcessError as e:
            log.warning("Notification call failed (exit %s): %s", e.returncode, e.stderr.strip())
            return ""
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("Notification call failed: %s", e)
            return ""

    @Slot("uint", str)
    def _on_action(self, notification_id, action_key):
        notification_id = int(notification_id)
        if notification_id not in self._mine:
            return   # another app's notification
        self._mine.discard(notification_id)   # a notification is clicked once
        log.info("Notification %s: %s clicked", notification_id, action_key)
        self.action_invoked.emit(notification_id, action_key)
