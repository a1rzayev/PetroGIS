"""Run blocking IO off the UI thread and deliver the results back on it."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger(__name__)


class _TaskSignals(QObject):
    # Created on the UI thread, so emitting from a worker queues the callbacks onto the UI thread.
    finished = Signal(object)
    failed = Signal(object)


class _Task(QRunnable):
    def __init__(
        self, fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> None:
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _TaskSignals()

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001 - forwarded to the UI thread's error handler
            self.signals.failed.emit(exc)
        else:
            self.signals.finished.emit(result)


class TaskRunner(QObject):
    """Submit callables to a thread pool. ``on_result`` and ``on_error`` run on the UI thread."""

    idle = Signal()
    """Emitted when the last running task finishes."""

    def __init__(self, parent: QObject | None = None, pool: QThreadPool | None = None) -> None:
        super().__init__(parent)
        self._pool = pool or QThreadPool.globalInstance()
        self._running: set[_Task] = set()
        self._shut_down = False

    @property
    def pending(self) -> int:
        return len(self._running)

    def shutdown(self) -> None:
        """Drop the callbacks of tasks still running, for example when the window closes.

        Running tasks can't be interrupted, but their results are discarded instead of reaching
        widgets that no longer exist.
        """
        self._shut_down = True

    def submit(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_result: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
        **kwargs: Any,
    ) -> None:
        task = _Task(fn, args, kwargs)
        task.setAutoDelete(False)
        # Keep a reference until done so neither the task nor its signals get garbage-collected.
        self._running.add(task)

        def done() -> None:
            self._running.discard(task)
            if not self._running:
                self.idle.emit()

        def finished(result: Any) -> None:
            try:
                if on_result is not None and not self._shut_down:
                    on_result(result)
            finally:
                done()

        def failed(exc: BaseException) -> None:
            try:
                if self._shut_down:
                    log.info("Discarded failure of %s after shutdown: %s", fn, exc)
                elif on_error is not None:
                    on_error(exc)
                else:
                    log.error(
                        "Background task %s failed", getattr(fn, "__name__", fn), exc_info=exc
                    )
            finally:
                done()

        task.signals.finished.connect(finished)
        task.signals.failed.connect(failed)
        self._pool.start(task)
