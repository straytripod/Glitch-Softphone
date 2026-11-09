"""Settings > Queue agent…: turn the agent box on/off, agent ID and PIN."""

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout,
)

from app.config import AgentConfig


class AgentSettingsDialog(QDialog):
    def __init__(self, agent: AgentConfig, extension: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Queue agent")
        self._agent = agent

        self._enabled = QCheckBox("Show queue agent controls")
        self._enabled.setChecked(agent.enabled)
        self._agent_id = QLineEdit(agent.agent_id)
        self._agent_id.setPlaceholderText(f"{extension} (your extension)")
        self._pin = QLineEdit(agent.password)
        self._pin.setEchoMode(QLineEdit.Password)   # dots, not digits
        self._pin.setValidator(QRegularExpressionValidator(QRegularExpression(r"[0-9*#]*")))
        self._pin.setToolTip("The agent password set in FusionPBX; keyed in after *22 / *24")

        form = QFormLayout()
        form.addRow(self._enabled)
        form.addRow("Agent ID:", self._agent_id)
        form.addRow("PIN:", self._pin)
        hint = QLabel("Saved to settings.toml, readable only by you.")
        hint.setEnabled(False)  # greyed-out help text
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    def agent_config(self) -> AgentConfig:
        """The edited settings; the feature codes are kept as they were."""
        return AgentConfig(
            enabled=self._enabled.isChecked(),
            agent_id=self._agent_id.text().strip(),
            password=self._pin.text().strip(),
            login_code=self._agent.login_code,
            break_code=self._agent.break_code,
        )
