"""Console window for displaying streamlink/yt-dlp output."""

import subprocess

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QMainWindow, QPlainTextEdit


class ProcessReaderThread(QThread):
    """Reads lines from a process stdout pipe and emits them as signals."""

    line_received = Signal(str)
    process_exited = Signal(int)

    def __init__(self, process: subprocess.Popen, parent=None):
        super().__init__(parent)
        self._process = process

    def run(self):
        try:
            for line in self._process.stdout:
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                self.line_received.emit(line.rstrip("\n\r"))
        except (ValueError, OSError):
            pass  # Pipe closed
        exit_code = self._process.wait()
        self.process_exited.emit(exit_code)


class StreamlinkConsoleWindow(QMainWindow):
    """Window displaying streamlink/yt-dlp console output for a stream."""

    def __init__(
        self,
        channel_name: str,
        process: subprocess.Popen,
        auto_close: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Streamlink — {channel_name}")
        self.resize(600, 400)
        self._auto_close = auto_close

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(QFont("monospace", 9))
        self._text.setMaximumBlockCount(5000)
        self.setCentralWidget(self._text)

        self._reader = ProcessReaderThread(process, parent=self)
        self._reader.line_received.connect(self._append_line)
        self._reader.process_exited.connect(self._on_exit)
        self._reader.start()

    def _append_line(self, line: str):
        self._text.appendPlainText(line)

    def _on_exit(self, exit_code: int):
        self._text.appendPlainText(f"\n--- Process exited with code {exit_code} ---")
        if self._auto_close:
            self.close()

    def closeEvent(self, event):  # noqa: N802
        self._reader.wait(2000)
        super().closeEvent(event)
