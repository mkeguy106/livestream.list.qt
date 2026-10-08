"""Guard against PySide6 builds that leak references to None.

PySide6 6.12.0 drops one reference to None on every call to a method that
returns nothing (QPainter.save(), setRenderHint(), ...). Before Python 3.12
None is an ordinary refcounted object, so painting drives its count to zero
and the interpreter aborts with "none_dealloc: deallocating None". The
Windows build ships Python 3.10, and CI tests on 3.10, so this runs there.
From 3.12 None is immortal and the leak is harmless, so the test is skipped.
"""

import os
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.version_info >= (3, 12), reason="None is immortal from Python 3.12"
)

CALLS = 1000


def test_void_qt_calls_do_not_release_references_to_none():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication, QImage, QPainter

    app = QGuiApplication.instance() or QGuiApplication([])
    image = QImage(8, 8, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    try:
        before = sys.getrefcount(None)
        for _ in range(CALLS):
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.restore()
        drift = sys.getrefcount(None) - before
    finally:
        painter.end()

    # A healthy build drifts by a few dozen at most; 6.12.0 loses 3 per iteration.
    assert drift > -CALLS, f"None lost {-drift} references over {CALLS} iterations"
    assert app is not None
