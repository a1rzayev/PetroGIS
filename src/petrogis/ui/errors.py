"""User-facing error reporting: every error is logged with details and shown in a dialog."""

import logging
import sys
import traceback
from types import TracebackType

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

log = logging.getLogger(__name__)


def show_error(
    parent: QWidget | None, title: str, message: str, exc: BaseException | None = None
) -> None:
    """Log ``message`` (with traceback if ``exc`` is given) and show it in a modal dialog."""
    if exc is not None:
        log.error("%s: %s", title, message, exc_info=exc)
    else:
        log.error("%s: %s", title, message)
    box = make_message_box(QMessageBox.Icon.Critical, title, message, parent)
    if exc is not None:
        box.setDetailedText("".join(traceback.format_exception(exc)))
    box.exec()


def show_warning(parent: QWidget | None, title: str, message: str) -> None:
    """Log ``message`` as a warning and show it in a modal dialog."""
    log.warning("%s: %s", title, message)
    make_message_box(QMessageBox.Icon.Warning, title, message, parent).exec()


def make_message_box(
    icon: QMessageBox.Icon, title: str, message: str, parent: QWidget | None
) -> QMessageBox:
    """A message box that always shows ``message`` as plain text.

    Messages contain file names, well names and library error text. With Qt's default
    auto-detected format, any of these could be interpreted as HTML.
    """
    box = QMessageBox(icon, title, "", parent=parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(message)
    return box


def install_excepthook() -> None:
    """Route uncaught exceptions to the log and an error dialog instead of a silent crash."""

    def hook(exc_type: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        if QApplication.instance() is None:
            log.critical("Uncaught exception", exc_info=(exc_type, exc, tb))
            return
        show_error(None, "Unexpected error", str(exc) or exc_type.__name__, exc)

    sys.excepthook = hook
