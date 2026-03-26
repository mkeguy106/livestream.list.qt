"""Tests for single-instance guard."""

from unittest.mock import MagicMock, patch

from PySide6.QtCore import QByteArray

from livestream_list.core.single_instance import SOCKET_NAME, SingleInstanceGuard


class TestSingleInstanceGuard:
    """Tests for SingleInstanceGuard."""

    @patch("livestream_list.core.single_instance.QLocalSocket")
    def test_not_running_when_connection_fails(self, mock_socket_cls: MagicMock) -> None:
        """First instance: no existing server, is_already_running returns False."""
        mock_socket = MagicMock()
        mock_socket_cls.return_value = mock_socket
        mock_socket.waitForConnected.return_value = False

        guard = SingleInstanceGuard()
        assert guard.is_already_running() is False
        mock_socket.connectToServer.assert_called_once_with(SOCKET_NAME)

    @patch("livestream_list.core.single_instance.QLocalSocket")
    def test_already_running_when_connection_succeeds(
        self, mock_socket_cls: MagicMock
    ) -> None:
        """Second instance: server exists, is_already_running returns True."""
        mock_socket = MagicMock()
        mock_socket_cls.return_value = mock_socket
        mock_socket.waitForConnected.return_value = True
        mock_socket.waitForBytesWritten.return_value = True

        guard = SingleInstanceGuard()
        assert guard.is_already_running() is True
        # Should have sent "raise" command
        mock_socket.write.assert_called_once()
        written = mock_socket.write.call_args[0][0]
        assert b"raise" in (written if isinstance(written, bytes) else written.data())

    @patch("livestream_list.core.single_instance.QLocalSocket")
    def test_socket_disconnected_after_sending_raise(
        self, mock_socket_cls: MagicMock
    ) -> None:
        """Second instance cleans up socket after sending raise."""
        mock_socket = MagicMock()
        mock_socket_cls.return_value = mock_socket
        mock_socket.waitForConnected.return_value = True
        mock_socket.waitForBytesWritten.return_value = True

        guard = SingleInstanceGuard()
        guard.is_already_running()
        mock_socket.disconnectFromServer.assert_called_once()

    def test_on_new_connection_emits_raise_requested(self) -> None:
        """Server emits raise_requested when client sends 'raise'."""
        guard = SingleInstanceGuard()
        mock_server = MagicMock()
        guard._server = mock_server

        mock_client = MagicMock()
        mock_client.waitForReadyRead.return_value = True
        mock_client.readAll.return_value = QByteArray(b"raise")
        mock_server.nextPendingConnection.return_value = mock_client

        received: list[bool] = []
        guard.raise_requested.connect(lambda: received.append(True))

        guard._on_new_connection()

        assert received == [True]
        mock_client.close.assert_called_once()

    def test_cleanup_closes_server_and_sets_none(self) -> None:
        """cleanup() closes the server and sets it to None."""
        guard = SingleInstanceGuard()
        mock_server = MagicMock()
        guard._server = mock_server

        guard.cleanup()

        mock_server.close.assert_called_once()
        assert guard._server is None


def test_socket_name_is_stable() -> None:
    """Socket name should be a fixed, known value."""
    assert SOCKET_NAME == "livestream-list-qt"


from livestream_list.main import parse_args


class TestParseArgs:
    """Tests for CLI argument parsing."""

    def test_default_no_allow_multiple(self) -> None:
        """Default: allow_multiple is False."""
        args = parse_args([])
        assert args.allow_multiple is False

    def test_long_flag(self) -> None:
        """--allow-multiple sets the flag."""
        args = parse_args(["--allow-multiple"])
        assert args.allow_multiple is True

    def test_short_flag(self) -> None:
        """-m sets the flag."""
        args = parse_args(["-m"])
        assert args.allow_multiple is True
