"""Per-user processing history: one JSON object per line, newest last, capped in size.

The file lives in the user's data directory and is only ever parsed as JSON data. Malformed or
oversized lines are skipped, so a damaged file never stops the app; parameters read back from it
are validated by the algorithm again before any re-run.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAX_ENTRIES = 500
MAX_LINE_BYTES = 64_000
STATUSES = ("ok", "failed", "canceled")


@dataclass(frozen=True)
class HistoryEntry:
    algorithm_id: str
    algorithm_name: str
    started: str
    """ISO 8601 time the run started (local time with offset)."""
    duration: float
    """Seconds."""
    status: str
    message: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    """JSON-safe parameter values (layers as ``{"id", "name"}``, outputs as paths)."""

    @classmethod
    def from_json(cls, data: Any) -> HistoryEntry | None:
        """An entry from parsed JSON, or ``None`` if it is not a valid entry."""
        if not isinstance(data, dict):
            return None
        try:
            entry = cls(
                str(data["algorithm_id"]),
                str(data["algorithm_name"]),
                str(data["started"]),
                float(data["duration"]),
                str(data["status"]),
                str(data.get("message", "")),
                data.get("parameters") or {},
            )
        except (KeyError, TypeError, ValueError):
            return None
        if entry.status not in STATUSES or not isinstance(entry.parameters, dict):
            return None
        return entry


class ProcessingHistory:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def entries(self) -> list[HistoryEntry]:
        """Valid entries, newest first."""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return []
        except OSError as exc:
            log.warning("Cannot read processing history %s: %s", self.path, exc)
            return []
        out = []
        for line in raw.splitlines()[-MAX_ENTRIES:]:
            if not line.strip() or len(line) > MAX_LINE_BYTES:
                continue
            try:
                entry = HistoryEntry.from_json(json.loads(line))
            except (json.JSONDecodeError, UnicodeDecodeError):
                entry = None
            if entry is not None:
                out.append(entry)
        out.reverse()
        return out

    def append(self, entry: HistoryEntry) -> None:
        """Add ``entry``, keeping at most ``MAX_ENTRIES``. Failures are logged, not raised:
        losing a history line must never fail a successful run."""
        line = json.dumps(asdict(entry), ensure_ascii=False, default=str)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            if self.path.stat().st_size > MAX_ENTRIES * 2 * 1000:
                self._trim()
        except OSError as exc:
            log.warning("Cannot write processing history %s: %s", self.path, exc)

    def _trim(self) -> None:
        lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text("\n".join(lines[-MAX_ENTRIES:]) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Cannot clear processing history %s: %s", self.path, exc)
