"""Settings > Audio and ringtone…: speaker, microphone, ringer and ringtone."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QVBoxLayout,
)

from app import ringtones
from app.config import AudioConfig
from app.sip.audio import AudioDevice

if TYPE_CHECKING:  # type hint only; keeps this module importable without pjsua2
    from app.services.phone_service import PhoneService


def device_choices(devices: list[AudioDevice], outputs: bool, current: str,
                   default_label: str) -> list[tuple[str, str]]:
    """(label, device name) pairs for one drop-down. An empty name means
    "use the default". A saved device that isn't plugged in right now stays
    in the list, so opening the dialog doesn't silently forget it."""
    choices = [(default_label, "")]
    names = set()
    for device in devices:
        if device.is_output == outputs:
            choices.append((device.description, device.name))
            names.add(device.name)
    if current and current not in names:
        choices.append((f"{current} (not connected)", current))
    return choices


def ringtone_choices(ring_file: str) -> list[tuple[str, str]]:
    """(label, ringtone setting) pairs: the built-in rings, then the user's
    own sound if they have chosen one."""
    choices = [(name, name) for name in ringtones.PRESETS]
    if ring_file:
        choices.append((f"My sound: {Path(ring_file).name}", ringtones.CUSTOM))
    return choices


PREVIEW_MS = 8000   # a preview stops by itself after this long


class AudioSettingsDialog(QDialog):
    def __init__(self, phone: PhoneService, parent=None):
        super().__init__(parent)
        self._phone = phone
        self.setWindowTitle("Audio and ringtone")

        self._speaker = QComboBox()
        self._microphone = QComboBox()
        self._ringer = QComboBox()
        refresh = QPushButton("Refresh")
        refresh.setToolTip("Look again for devices, e.g. after connecting a headset")
        refresh.clicked.connect(self._refresh)

        audio = phone.audio_config
        self._ring_file = audio.ringtone_file   # the app's copy of the user's WAV
        self._ringtone = QComboBox()
        self._fill_ringtones(audio.ringtone)
        choose = QPushButton("Choose a sound file…")
        choose.setToolTip("Use your own sound as the ringtone (WAV; MP3 and others need ffmpeg)")
        choose.clicked.connect(self._choose_file)
        self._play = QPushButton("Play")
        self._play.setCheckable(True)
        self._play.setToolTip("Hear the ringtone on the ringer")
        self._play.toggled.connect(self._play_toggled)
        self._ringtone.currentIndexChanged.connect(self._ringtone_changed)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(PREVIEW_MS)
        self._preview_timer.timeout.connect(lambda: self._play.setChecked(False))
        ring_row = QHBoxLayout()
        ring_row.addWidget(self._ringtone, stretch=1)
        ring_row.addWidget(self._play)

        form = QFormLayout()
        form.addRow("Speaker:", self._speaker)
        form.addRow("Microphone:", self._microphone)
        form.addRow("Ringer:", self._ringer)
        form.addRow("Ringtone:", ring_row)
        form.addRow("", choose)
        hint = QLabel("The ringer is where incoming calls ring, e.g. desk speakers\n"
                      "so you hear calls without wearing the headset.")
        hint.setEnabled(False)  # greyed-out help text

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        bottom = QHBoxLayout()
        bottom.addWidget(refresh)
        bottom.addStretch()
        bottom.addWidget(buttons)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addLayout(bottom)

        self._fill(audio.speaker, audio.microphone, audio.ringer)

    def _fill(self, speaker: str, microphone: str, ringer: str):
        devices = self._phone.audio_devices()
        rows = (
            (self._speaker, True, speaker, "System default"),
            (self._microphone, False, microphone, "System default"),
            (self._ringer, True, ringer, "Same as speaker"),
        )
        for combo, outputs, current, default_label in rows:
            combo.clear()
            for label, name in device_choices(devices, outputs, current, default_label):
                combo.addItem(label, name)
                combo.setItemData(combo.count() - 1, name or default_label, Qt.ToolTipRole)
            combo.setCurrentIndex(max(combo.findData(current), 0))

    def _fill_ringtones(self, current: str):
        self._ringtone.blockSignals(True)
        self._ringtone.clear()
        for label, value in ringtone_choices(self._ring_file):
            self._ringtone.addItem(label, value)
        self._ringtone.setCurrentIndex(max(self._ringtone.findData(current), 0))
        self._ringtone.blockSignals(False)

    def _choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a ringtone", str(Path.home()),
                                              f"Sound files ({ringtones.SOUND_FILE_PATTERNS});;All files (*)")
        if path:
            self.use_sound_file(Path(path))

    def use_sound_file(self, path: Path, folder: Path = ringtones.RINGTONE_DIR):
        """Check and copy the chosen WAV, then select it."""
        # A song takes a second or two to convert (more with ffmpeg).
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._ring_file = str(ringtones.prepare_wav(path, folder))
        except ringtones.RingtoneError as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Ringtone", str(e))
            return
        QApplication.restoreOverrideCursor()
        self._fill_ringtones(ringtones.CUSTOM)
        self._ringtone_changed()

    def _ringtone_changed(self):
        if self._play.isChecked():
            self._play_toggled(True)   # play the new one instead

    def _play_toggled(self, playing: bool):
        if playing and self._phone.can_preview_ringtone():
            self._phone.preview_ringtone(self._ringtone.currentData(), self._ring_file)
            self._play.setText("Stop")
            self._preview_timer.start()
            return
        self._phone.stop_preview()
        self._preview_timer.stop()
        self._play.blockSignals(True)
        self._play.setChecked(False)
        self._play.blockSignals(False)
        self._play.setText("Play")

    def done(self, result: int):
        self._play.setChecked(False)   # stops a preview
        super().done(result)

    def _refresh(self):
        self._fill(self._speaker.currentData(), self._microphone.currentData(),
                   self._ringer.currentData())

    def _save(self):
        audio = AudioConfig(
            speaker=self._speaker.currentData(),
            microphone=self._microphone.currentData(),
            ringer=self._ringer.currentData(),
            ringtone=self._ringtone.currentData(),
            ringtone_file=self._ring_file,
        )
        try:
            self._phone.set_audio_config(audio)
        except OSError as e:
            QMessageBox.warning(self, "Audio devices",
                                f"The new devices are in use, but saving them failed:\n{e}")
        self.accept()
