"""Render the main window offscreen and save a PNG, to check rendering without a display.

Usage: uv run python scripts/screenshot.py [output.png] [--demo]

``--demo`` loads the synthetic sample field (generating it into sample_data/ if needed) before
taking the screenshot.
"""

import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from petrogis.app import create_app
from petrogis.ui.main_window import MainWindow

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "sample_data"


def settle(app: QApplication, window: MainWindow, timeout: float = 20.0) -> None:
    """Process events until background tasks finish and debounced view updates have run."""
    deadline = time.monotonic() + timeout
    quiet_since: float | None = None
    while time.monotonic() < deadline:
        QThreadPool.globalInstance().waitForDone(50)
        app.processEvents()
        if window.tasks.pending:
            quiet_since = None
        elif quiet_since is None:
            quiet_since = time.monotonic()
        elif time.monotonic() - quiet_since > 0.4:
            return
    raise TimeoutError("background tasks did not finish")


def load_demo(window: MainWindow) -> None:
    if not (SAMPLE_DIR / "structure_top.tif").exists():
        from petrogis.sample_data import generate

        generate(SAMPLE_DIR)
    window.load_raster(SAMPLE_DIR / "structure_top.tif")
    window.load_vector(SAMPLE_DIR / "field_boundary.geojson")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", default="screenshots/main_window.png", type=Path)
    parser.add_argument("--demo", action="store_true", help="load the sample field first")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    app = create_app([])
    window = MainWindow()
    window.show()
    app.processEvents()
    if args.demo:
        load_demo(window)
        settle(app, window)
        window.zoom_to_full_extent()
        settle(app, window)
    else:
        app.processEvents()
    if not window.grab().save(str(args.output)):
        print(f"Failed to save screenshot to {args.output}", file=sys.stderr)
        return 1
    print(f"Saved {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
