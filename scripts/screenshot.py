"""Render the main window offscreen and save a PNG, to check rendering without a display.

Usage: uv run python scripts/screenshot.py [output.png]
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from petrogis.app import create_app
from petrogis.ui.main_window import MainWindow


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("screenshots/main_window.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    app = create_app([])
    window = MainWindow()
    window.show()
    app.processEvents()
    if not window.grab().save(str(out)):
        print(f"Failed to save screenshot to {out}", file=sys.stderr)
        return 1
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
