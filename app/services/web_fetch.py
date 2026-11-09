"""Download a small file from the PBX's web server, in the background.

Uses Qt's network module (part of PySide6), so the download runs inside
the Qt event loop: no threads, and the UI never waits on the network.

Certificates are trusted on first use, like SSH host keys. A properly
signed certificate (e.g. Let's Encrypt) is accepted as normal. Anything
else (self-signed, issued for another name) is refused and reported
with its fingerprint; once the user trusts that fingerprint, exactly that
certificate is accepted and any other is refused again.
"""

import logging

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from app.directory import fingerprint

log = logging.getLogger(__name__)

TIMEOUT_MS = 10_000
MAX_BYTES = 2_000_000   # a directory is a few KB; refuse anything silly


class WebFetcher(QObject):
    loaded = Signal(str)          # the response body
    failed = Signal(int, str)     # (HTTP status or 0, message)
    # (fingerprint) the server's certificate isn't trusted: neither properly
    # signed nor the one pinned
    untrusted = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._reply: QNetworkReply | None = None
        self._pinned = ""
        self._untrusted = ""

    def get(self, url: str, pinned: str = ""):
        """Start a download; one at a time (a second call while busy is ignored)."""
        if self._reply is not None:
            return
        self._pinned = pinned
        self._untrusted = ""
        request = QNetworkRequest(QUrl(url))
        request.setTransferTimeout(TIMEOUT_MS)
        reply = self._manager.get(request)
        reply.sslErrors.connect(lambda errors: self._ssl_errors(reply, errors))
        reply.finished.connect(lambda: self._finished(reply))
        self._reply = reply

    def _ssl_errors(self, reply: QNetworkReply, errors):
        cert = reply.sslConfiguration().peerCertificate()
        seen = fingerprint(bytes(cert.toDer())) if not cert.isNull() else ""
        if seen and seen == self._pinned:
            reply.ignoreSslErrors()   # the certificate the user chose to trust
            return
        log.info("PBX web certificate not trusted (%s): %s",
                 "; ".join(e.errorString() for e in errors), seen)
        self._untrusted = seen   # leave the errors in place: Qt aborts the download

    def _finished(self, reply: QNetworkReply):
        self._reply = None
        reply.deleteLater()
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) or 0
        if self._untrusted:
            self.untrusted.emit(self._untrusted)
        elif reply.error() != QNetworkReply.NoError and not status:
            self.failed.emit(0, reply.errorString())
        elif status != 200:
            self.failed.emit(status, f"HTTP {status}")
        else:
            body = bytes(reply.read(MAX_BYTES))
            self.loaded.emit(body.decode("utf-8", errors="replace"))
