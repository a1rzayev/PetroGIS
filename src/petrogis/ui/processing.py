"""Processing UI: generated parameter dialogs, background runs with progress and cancel, history.

A dialog is built from the algorithm's parameter list. Run validates the values, then executes
the algorithm through the window's ``TaskRunner``; progress arrives through a Qt signal, and
Cancel sets the run's ``Feedback`` flag, which the algorithm checks between steps. Outputs are
loaded back as layers, and every run (ok, failed or cancelled) goes to the per-user history.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QStandardPaths, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from petrogis.analysis.processing import (
    Algorithm,
    BoolParam,
    Canceled,
    ChoiceParam,
    CrsParam,
    Feedback,
    LayerParam,
    NumberParam,
    OutputParam,
    ParameterError,
    RunContext,
    ValidationContext,
    get_algorithm,
)
from petrogis.io.errors import DataReadError
from petrogis.io.processing_history import HistoryEntry, ProcessingHistory
from petrogis.ui.text import plain_label

if TYPE_CHECKING:
    from petrogis.ui.main_window import MainWindow

log = logging.getLogger(__name__)


def default_history_path() -> Path:
    """The per-user history file (module-level so tests can redirect it)."""
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    return Path(base) / "processing_history.jsonl"


class _ProgressRelay(QObject):
    """Carries progress from the worker thread to the UI thread (queued signal)."""

    progress = Signal(float)


class AlgorithmDialog(QDialog):
    """Parameters, Run/Cancel, a progress bar and the run's log for one algorithm."""

    def __init__(self, algorithm: Algorithm, window: MainWindow) -> None:
        super().__init__(window)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.algorithm = algorithm
        self.window_ = window
        self.feedback: Feedback | None = None
        self.started = 0.0
        self.setWindowTitle(algorithm.name)
        self.resize(560, 480)

        self.inputs: dict[str, Any] = {}
        form = QFormLayout()
        for parameter in algorithm.parameters:
            widget = self._make_input(parameter)
            self.inputs[parameter.name] = widget
            form.addRow(parameter.label, widget)
        description = plain_label(algorithm.description)
        description.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.log = QPlainTextEdit()  # plain text: messages may quote data-derived names
        self.log.setReadOnly(True)
        self.run_button = QPushButton("Run")
        self.run_button.clicked.connect(self.run)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch(1)
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(description)
        layout.addLayout(form)
        layout.addWidget(self.progress)
        layout.addWidget(self.log, 1)
        layout.addLayout(buttons)
        self._relay = _ProgressRelay(self)
        self._relay.progress.connect(lambda f: self.progress.setValue(round(f * 1000)))
        self.refresh_layers()

    # --- inputs --------------------------------------------------------------------------------
    def _make_input(self, parameter: Any) -> QWidget:
        if isinstance(parameter, LayerParam):
            return QComboBox()
        if isinstance(parameter, ChoiceParam):
            combo = QComboBox()
            combo.addItems(list(parameter.choices))
            combo.setCurrentText(parameter.default)
            return combo
        if isinstance(parameter, NumberParam):
            spin = QDoubleSpinBox(minimum=parameter.minimum, maximum=parameter.maximum)
            spin.setDecimals(0 if parameter.integer else 3)
            spin.setValue(parameter.default)
            return spin
        if isinstance(parameter, BoolParam):
            check = QCheckBox()
            check.setChecked(parameter.default)
            return check
        if isinstance(parameter, CrsParam):
            row = _CrsInput(self.window_.project.crs)
            return row
        if isinstance(parameter, OutputParam):
            return _PathInput(parameter.extension)
        raise TypeError(f"No input widget for {type(parameter).__name__}")

    def refresh_layers(self) -> None:
        """Fill the layer combos with the project layers each parameter accepts."""
        for parameter in self.algorithm.parameters:
            if not isinstance(parameter, LayerParam):
                continue
            combo: QComboBox = self.inputs[parameter.name]
            current = combo.currentData()
            combo.clear()
            if parameter.optional:
                combo.addItem("(none)", None)
            for layer in self.window_.project.layers:
                if parameter.accepts(layer):
                    combo.addItem(layer.name, layer.id)
            index = combo.findData(current)
            if index >= 0:
                combo.setCurrentIndex(index)
        self._suggest_outputs()

    def _suggest_outputs(self) -> None:
        project_path = self.window_.project.path
        folder = Path(project_path).parent if project_path else Path.home()
        first_layer = next(
            (self.inputs[p.name].currentText() for p in self.algorithm.parameters
             if isinstance(p, LayerParam) and self.inputs[p.name].currentText()),
            "result",
        )  # fmt: skip
        stem = "".join(c if c.isalnum() else "_" for c in first_layer).strip("_") or "result"
        short = self.algorithm.id.split(":")[-1]
        for parameter in self.algorithm.parameters:
            if isinstance(parameter, OutputParam):
                box: _PathInput = self.inputs[parameter.name]
                if not box.text():
                    box.set_text(str(_unique(folder / f"{stem}_{short}{parameter.extension}")))

    def values(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for parameter in self.algorithm.parameters:
            widget = self.inputs[parameter.name]
            if isinstance(parameter, LayerParam | ChoiceParam):
                out[parameter.name] = (
                    widget.currentData()
                    if isinstance(parameter, LayerParam)
                    else widget.currentText()
                )
            elif isinstance(parameter, NumberParam):
                out[parameter.name] = widget.value()
            elif isinstance(parameter, BoolParam):
                out[parameter.name] = widget.isChecked()
            else:
                out[parameter.name] = widget.text()
        return out

    def set_values(self, values: dict[str, Any]) -> None:
        """Prefill from a history entry. Layers are matched by id, else by unique name."""
        context = ValidationContext({lyr.id: lyr for lyr in self.window_.project.layers})
        for parameter in self.algorithm.parameters:
            if parameter.name not in values:
                continue
            value = values[parameter.name]
            widget = self.inputs[parameter.name]
            if isinstance(parameter, LayerParam):
                layer = context.layer(value)
                if layer is not None and (index := widget.findData(layer.id)) >= 0:
                    widget.setCurrentIndex(index)
            elif isinstance(parameter, ChoiceParam) and value in parameter.choices:
                widget.setCurrentText(value)
            elif isinstance(parameter, NumberParam) and isinstance(value, int | float):
                widget.setValue(float(value))
            elif isinstance(parameter, BoolParam) and isinstance(value, bool):
                widget.setChecked(value)
            elif isinstance(value, str) and isinstance(parameter, CrsParam | OutputParam):
                widget.set_text(value)

    # --- running -------------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self.feedback is not None

    def run(self) -> None:
        if self.running:
            return
        project = self.window_.project
        context = ValidationContext({lyr.id: lyr for lyr in project.layers})
        try:
            values = self.algorithm.validate(self.values(), context)
        except ParameterError as exc:
            self._log(f"✗ {exc}")
            return
        self.feedback = Feedback(on_progress=self._relay.progress.emit)
        self.started = time.monotonic()
        self._started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        self._json_values = self.algorithm.to_json(values)
        self.progress.setValue(0)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._log(f"Running {self.algorithm.name}…")
        run_context = RunContext(project.crs, project.transformations)
        self.window_.tasks.submit(
            self.algorithm.execute,
            values,
            run_context,
            self.feedback,
            on_result=self.window_._current(self._finished),
            on_error=self.window_._current(self._failed),
        )

    def cancel(self) -> None:
        if self.feedback is not None:
            self.feedback.cancel()
            self.cancel_button.setEnabled(False)
            self._log("Cancelling…")

    def _finished(self, result: Any) -> None:
        self._log(f"✓ {result.summary}" if result.summary else "✓ Done")
        for path in result.outputs.values():
            self._log(f"Wrote {path}")
            if Path(path).suffix.lower() == ".tif":
                self.window_.load_raster(path)
            else:
                self.window_.load_vector(path)
        self._record("ok", result.summary)

    def _failed(self, exc: BaseException) -> None:
        if isinstance(exc, Canceled):
            self._log("Cancelled. Partial output was removed.")
            self._record("canceled", "Cancelled by the user")
            return
        if isinstance(exc, DataReadError | ParameterError):
            message = str(exc)
        else:
            log.error("Algorithm %s failed", self.algorithm.id, exc_info=exc)
            message = f"Unexpected error: {exc}"
        self._log(f"✗ {message}")
        self._record("failed", message)

    def _record(self, status: str, message: str) -> None:
        duration = time.monotonic() - self.started
        self.feedback = None
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        if status == "ok":
            self.progress.setValue(1000)
        ProcessingHistory(default_history_path()).append(
            HistoryEntry(
                self.algorithm.id, self.algorithm.name, self._started_at, round(duration, 3),
                status, message, self._json_values,
            )
        )  # fmt: skip

    def _log(self, text: str) -> None:
        self.log.appendPlainText(text)

    def closeEvent(self, event: Any) -> None:
        self.cancel()  # a run left behind is cancelled; its result is still recorded
        super().closeEvent(event)


class _PathInput(QWidget):
    def __init__(self, extension: str) -> None:
        super().__init__()
        self.extension = extension
        self.edit = QLineEdit()
        browse = QToolButton(text="…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.edit, 1)
        row.addWidget(browse)

    def text(self) -> str:
        return self.edit.text().strip()

    def set_text(self, text: str) -> None:
        self.edit.setText(text)

    def _browse(self) -> None:
        kind = "GeoTIFF (*.tif)" if self.extension == ".tif" else "GeoPackage (*.gpkg)"
        path, _ = QFileDialog.getSaveFileName(self, "Save Output", self.text(), kind)
        if path:
            self.set_text(path)


class _CrsInput(QWidget):
    def __init__(self, default: str) -> None:
        super().__init__()
        self.edit = QLineEdit(default)
        pick = QToolButton(text="…")
        pick.clicked.connect(self._pick)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.edit, 1)
        row.addWidget(pick)

    def text(self) -> str:
        return self.edit.text().strip()

    def set_text(self, text: str) -> None:
        self.edit.setText(text)

    def _pick(self) -> None:
        from petrogis.ui.dialogs.crs_picker import pick_crs

        chosen = pick_crs(self, self.text(), "Target CRS")
        if chosen:
            self.set_text(chosen)


def _unique(path: Path) -> Path:
    """``path``, or ``path`` with a number added, so suggestions never overwrite a file."""
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path


class HistoryDialog(QDialog):
    """Past runs, newest first, with Run Again."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__(window)
        self.window_ = window
        self.setWindowTitle("Processing History")
        self.resize(760, 420)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Started", "Algorithm", "Status", "Seconds", "Message"]
        )
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.doubleClicked.connect(lambda _index: self.run_again())
        again = QPushButton("Run Again…")
        again.clicked.connect(self.run_again)
        clear = QPushButton("Clear History")
        clear.clicked.connect(self.clear)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(again)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        self.entries: list[HistoryEntry] = []
        self.reload()

    def reload(self) -> None:
        self.entries = ProcessingHistory(default_history_path()).entries()
        self.table.setRowCount(len(self.entries))
        for row, entry in enumerate(self.entries):
            cells = (entry.started, entry.algorithm_name, entry.status, f"{entry.duration:.2f}",
                     entry.message)  # fmt: skip
            for column, text in enumerate(cells):
                self.table.setItem(row, column, QTableWidgetItem(text))  # plain item text

    def clear(self) -> None:
        ProcessingHistory(default_history_path()).clear()
        self.reload()

    def run_again(self) -> AlgorithmDialog | None:
        row = self.table.currentRow()
        if not 0 <= row < len(self.entries):
            return None
        entry = self.entries[row]
        try:
            algorithm = get_algorithm(entry.algorithm_id)
        except ParameterError as exc:  # an algorithm this version doesn't have
            self.window_.statusBar().showMessage(str(exc), 5000)
            return None
        dialog = open_algorithm(self.window_, algorithm)
        dialog.set_values(entry.parameters)
        return dialog


def open_algorithm(window: MainWindow, algorithm: Algorithm) -> AlgorithmDialog:
    """Show (or bring back) the non-modal dialog for ``algorithm``."""
    dialogs = window.algorithm_dialogs
    dialog = dialogs.get(algorithm.id)
    if dialog is None:
        dialog = dialogs[algorithm.id] = AlgorithmDialog(algorithm, window)
    else:
        dialog.refresh_layers()
    dialog.show()
    dialog.raise_()
    return dialog
