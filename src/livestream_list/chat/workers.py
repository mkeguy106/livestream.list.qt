"""Generic one-shot worker thread for sync/async tasks."""

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)


class AsyncTaskWorker(QThread):
    """One-shot worker that runs a sync or async callable in a background thread.

    The callable should take no arguments — use a closure to capture context.
    """

    result_ready = Signal(object)
    error_occurred = Signal(str)

    def __init__(self, task: Callable[[], Any], *, parent=None):
        super().__init__(parent)
        self._task = task

    def run(self):
        try:
            if inspect.iscoroutinefunction(self._task):
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    result = loop.run_until_complete(self._task())
                finally:
                    loop.close()
            else:
                result = self._task()
            self.result_ready.emit(result)
        except Exception as e:
            logger.error(f"AsyncTaskWorker error: {e}", exc_info=True)
            self.error_occurred.emit(str(e))
