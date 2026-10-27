"""Application entry point.

Heavy modules (geopandas, rasterio, the main window) are imported only after the
QApplication exists, so the process shows a window as early as possible.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QApplication

APP_NAME = "PetroGIS"
ORG_NAME = "PetroGIS"


def log_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    return Path(base) / "logs"


def setup_logging() -> Path:
    """Log to stderr and a rotating file in the per-user data directory; return the file path."""
    path = log_dir()
    path.mkdir(parents=True, exist_ok=True)
    log_file = path / "petrogis.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3)
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    # lasio logs two INFO lines per file about encoding detection, which drowns out our messages.
    logging.getLogger("lasio").setLevel(logging.WARNING)
    return log_file


def create_app(argv: list[str] | None = None) -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    return app


def main() -> int:
    app = create_app()
    log_file = setup_logging()
    logging.getLogger(__name__).info("Starting PetroGIS, logging to %s", log_file)

    from petrogis.ui.errors import install_excepthook
    from petrogis.ui.main_window import MainWindow

    install_excepthook()
    window = MainWindow()
    window.show()
    project_args = [arg for arg in sys.argv[1:] if arg.lower().endswith(".pgis")]
    if project_args:
        window.open_project(project_args[0])
    else:
        window.offer_untitled_recovery()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
