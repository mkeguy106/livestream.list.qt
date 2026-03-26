"""Single-instance guard using Qt local sockets.

Ensures only one instance of the application runs at a time.
The first instance creates a QLocalServer. Subsequent instances
detect it via QLocalSocket, send a "raise" command, and exit.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QByteArray, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger(__name__)

SOCKET_NAME = "livestream-list-qt"


class SingleInstanceGuard(QObject):
    """Guards against multiple application instances.

    Usage:
        guard = SingleInstanceGuard()
        if guard.is_already_running():
            # Show warning, exit
            ...
        guard.start_listening()
        # Connect guard.raise_requested to window raise logic
    """

    raise_requested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._server: QLocalServer | None = None

    def is_already_running(self) -> bool:
        """Check if another instance is already running.

        If one is, sends a 'raise' command to it and returns True.
        """
        socket = QLocalSocket()
        socket.connectToServer(SOCKET_NAME)
        if socket.waitForConnected(1000):
            # Another instance is running — tell it to raise its window
            logger.info("Another instance is already running, sending raise command")
            socket.write(QByteArray(b"raise"))
            socket.waitForBytesWritten(1000)
            socket.disconnectFromServer()
            return True
        return False

    def start_listening(self) -> None:
        """Start the local server to listen for new instance connections."""
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        if not self._server.listen(SOCKET_NAME):
            # Stale socket from a crash — remove and retry once
            logger.warning("Local server listen failed, removing stale socket and retrying")
            QLocalServer.removeServer(SOCKET_NAME)
            if not self._server.listen(SOCKET_NAME):
                logger.error(
                    "Failed to start single-instance server: %s",
                    self._server.errorString(),
                )

    def _on_new_connection(self) -> None:
        """Handle incoming connection from a new instance."""
        if self._server is None:
            return
        client = self._server.nextPendingConnection()
        if client is None:
            return
        client.waitForReadyRead(1000)
        data = bytes(client.readAll().data()).decode("utf-8", errors="replace")
        client.close()
        if data.strip() == "raise":
            logger.info("Received raise request from another instance")
            self.raise_requested.emit()

    def cleanup(self) -> None:
        """Close the server."""
        if self._server:
            self._server.close()
            self._server = None
