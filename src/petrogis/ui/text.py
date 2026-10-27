"""Safe display of data-derived text in Qt widgets.

Qt guesses whether a string is rich text (`Qt::mightBeRichText`) for labels, tooltips, message
boxes and input dialogs. Text taken from data files, such as well names, file names, CRS names or
band descriptions, must never be interpreted as HTML. Use these helpers wherever such text reaches
a widget that auto-detects the format.
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget


def as_rich_text(text: str) -> str:
    """Escape ``text`` and wrap it so Qt renders it as rich text showing the literal characters.

    Use this for APIs that only take a string and auto-detect the format, such as tooltips and the
    static ``QInputDialog`` helpers. Newlines are kept.
    """
    return "<p>" + escape(text).replace("\n", "<br>") + "</p>"


def plain_label(text: str = "", parent: QWidget | None = None) -> QLabel:
    """A QLabel that always shows ``text`` literally, whatever it contains."""
    label = QLabel(parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setText(text)
    return label
