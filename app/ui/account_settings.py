"""Settings > Account…: the SIP account this phone registers."""

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from app.config import AccountConfig

TRANSPORTS = (("UDP", "udp"), ("TCP", "tcp"), ("TLS (encrypted)", "tls"))
SRTP_CHOICES = (("Required", "required"), ("Optional", "optional"), ("Off", "off"))

INTRO = ("The extension details from your PBX administrator (in FusionPBX: "
         "Accounts → Extensions → your extension).")
SAVED_HINT = "Saved to settings.toml, readable only by you."


class AccountDialog(QDialog):
    def __init__(self, account: AccountConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Account")

        self._server = QLineEdit(account.server)
        self._server.setPlaceholderText("pbx.example.com or 192.0.2.10")
        self._extension = QLineEdit(account.extension)
        self._extension.setPlaceholderText("101")
        self._password = QLineEdit(account.password)
        self._password.setEchoMode(QLineEdit.Password)
        self._display_name = QLineEdit(account.display_name)
        self._display_name.setPlaceholderText("Shown in the window title")
        self._transport = QComboBox()
        for label, value in TRANSPORTS:
            self._transport.addItem(label, value)
        self._transport.setCurrentIndex(max(self._transport.findData(account.transport), 0))

        # Advanced: rarely needed, so grouped below.
        self._auth_username = QLineEdit(account.auth_username)
        self._auth_username.setPlaceholderText("Same as the extension")
        self._port = QSpinBox()
        self._port.setRange(0, 65535)
        self._port.setSpecialValueText("Default")   # 0 = 5060, or 5061 for TLS
        self._port.setValue(account.port)
        self._ca_file = QLineEdit(account.tls_ca_file)
        self._ca_file.setPlaceholderText("The system's trusted certificates")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        ca_row = QHBoxLayout()
        ca_row.addWidget(self._ca_file, stretch=1)
        ca_row.addWidget(browse)
        self._verify = QCheckBox("Check the PBX's certificate")
        self._verify.setChecked(account.tls_verify)
        self._srtp = QComboBox()
        for label, value in SRTP_CHOICES:
            self._srtp.addItem(label, value)
        self._srtp.setCurrentIndex(max(self._srtp.findData(account.srtp), 0))
        self._tls_widgets = (self._ca_file, browse, self._verify, self._srtp)

        form = QFormLayout()
        form.addRow("Server:", self._server)
        form.addRow("Extension:", self._extension)
        form.addRow("Password:", self._password)
        form.addRow("Your name:", self._display_name)
        form.addRow("Connection:", self._transport)
        advanced = QGroupBox("Advanced")
        advanced_form = QFormLayout(advanced)
        advanced_form.addRow("Auth user name:", self._auth_username)
        advanced_form.addRow("Port:", self._port)
        advanced_form.addRow("CA certificate:", ca_row)
        advanced_form.addRow("", self._verify)
        advanced_form.addRow("Call encryption:", self._srtp)

        intro = QLabel(INTRO)
        intro.setWordWrap(True)
        hint = QLabel(SAVED_HINT)
        hint.setProperty("role", "muted")
        self._buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(advanced)
        layout.addWidget(hint)
        layout.addWidget(self._buttons)

        for field in (self._server, self._extension, self._ca_file):
            field.textChanged.connect(self._check)
        self._transport.currentIndexChanged.connect(self._check)
        self._check()

    def account(self) -> AccountConfig:
        return AccountConfig(
            server=self._server.text().strip(),
            port=self._port.value(),
            transport=self._transport.currentData(),
            extension=self._extension.text().strip(),
            auth_username=self._auth_username.text().strip(),
            password=self._password.text(),
            display_name=self._display_name.text().strip(),
            tls_ca_file=str(Path(ca).expanduser()) if (ca := self._ca_file.text().strip()) else "",
            tls_verify=self._verify.isChecked(),
            srtp=self._srtp.currentData(),
        )

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "PBX CA certificate", str(Path.home()),
                                              "Certificates (*.pem *.crt *.cer);;All files (*)")
        if path:
            self._ca_file.setText(path)

    def _check(self):
        account = self.account()
        tls = account.transport == "tls"
        for widget in self._tls_widgets:
            widget.setEnabled(tls)
        ca_ok = not (tls and account.tls_ca_file) or Path(account.tls_ca_file).expanduser().is_file()
        self._ca_file.setToolTip("" if ca_ok else "File not found")
        self._buttons.button(QDialogButtonBox.Save).setEnabled(
            bool(account.server and account.extension) and ca_ok)
