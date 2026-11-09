"""Entry point. Run with:  python -m app.main"""

import argparse
import logging
import signal
import sys
from pathlib import Path

import shiboken6
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from app import APP_DESKTOP_ID, APP_ID, APP_NAME, APP_VERSION
from app.config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from app.contacts import DEFAULT_CONTACTS_PATH, ContactBook
from app.history import DEFAULT_HISTORY_PATH, CallHistory
from app.logging_setup import setup_logging
from app.migrate import migrate_old_folders
from app.services.phone_service import PhoneService
from app.services.web_fetch import WebFetcher
from app.ui.main_window import MainWindow
from app.ui.theme import apply_theme, load_fonts

log = logging.getLogger(__name__)


def parse_args(argv):
    parser = argparse.ArgumentParser(description=f"{APP_NAME}: FusionPBX softphone")
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG_PATH,
        help=f"settings file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--history", type=Path, default=DEFAULT_HISTORY_PATH,
        help=f"call history database (default: {DEFAULT_HISTORY_PATH})",
    )
    parser.add_argument(
        "--debug", action="store_true", help="log at DEBUG level regardless of config"
    )
    return parser.parse_args(argv)


def show_startup_error(text: str):
    """Started from the app menu there's no terminal to see stderr, so say it
    in a window too."""
    app = QApplication.instance() or QApplication(sys.argv)
    QMessageBox.critical(None, APP_NAME, text)


def main(argv=None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    moved = migrate_old_folders()   # before anything reads those folders

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        show_startup_error(f"The settings file has a problem:\n\n{e}\n\nFile: {args.config}")
        return 2

    log_file = setup_logging("DEBUG" if args.debug else config.logging.level)
    log.info("Starting %s %s; config=%s log=%s", APP_NAME, APP_VERSION, args.config, log_file)
    for line in moved:
        log.info(line)
    if not args.config.exists():
        log.warning("No config file at %s; using defaults", args.config)

    app = QApplication(sys.argv)
    # Ties our windows to the installed .desktop file (taskbar name and icon):
    # Wayland matches the desktop file name, X11 the window class (app name).
    app.setApplicationName(APP_ID)
    app.setDesktopFileName(APP_DESKTOP_ID)
    load_fonts()
    apply_theme(app, config.ui.theme)
    phone = PhoneService(config, CallHistory(args.history), fetcher=WebFetcher(),
                         contact_book=ContactBook(DEFAULT_CONTACTS_PATH))
    window = MainWindow(config, phone)
    window.show()
    if not phone.has_account:
        # First run: ask for the account once the window is up.
        QTimer.singleShot(0, window.open_account_settings)
    # aboutToQuit fires while the event loop still runs, so PJSIP can send
    # its un-REGISTER before the process exits.
    app.aboutToQuit.connect(phone.stop)
    # Ctrl+C / kill: quit the normal way, so the phone logs out of the PBX.
    # Python runs signal handlers at the next Python line it executes, which
    # can be inside PJSIP's event processing (our log writer is Python).
    # Quitting right there would destroy PJSIP under its own feet (it
    # aborted), so the handler only schedules the quit for the event loop.
    # Qt's event loop is C++, so a timer hands control back to Python a few
    # times a second to notice the signal at all.
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: QTimer.singleShot(0, app.quit))
    wake_python = QTimer()
    wake_python.timeout.connect(lambda: None)
    wake_python.start(250)
    phone.start()
    exit_code = app.exec()
    # Destroy our Qt objects now, while QApplication still exists. Left to
    # Python's shutdown, PySide's cleanup visited half-destroyed widgets and
    # crashed (SIGBUS/SIGSEGV) on exit.
    shiboken6.delete(window)
    shiboken6.delete(phone)
    log.info("Exiting with code %s", exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
