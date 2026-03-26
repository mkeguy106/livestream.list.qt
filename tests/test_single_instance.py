"""Tests for single-instance guard."""

from unittest.mock import MagicMock, patch

import pytest

from livestream_list.core.single_instance import SOCKET_NAME, SingleInstanceGuard


@pytest.fixture
def _no_qapp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure QLocalServer/QLocalSocket don't need a real QApplication."""
    # Tests that create real Qt objects will need a QApplication;
    # for unit tests we mock the Qt classes entirely.


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


def test_socket_name_is_stable() -> None:
    """Socket name should be a fixed, known value."""
    assert SOCKET_NAME == "livestream-list-qt"
