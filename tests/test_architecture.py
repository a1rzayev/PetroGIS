"""Guards for the architecture and startup-performance rules in CLAUDE.md."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "petrogis"
QT_MODULES = ("PySide6", "PyQt5", "PyQt6", "pyqtgraph", "shiboken6")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize("package", ["core", "io"])
def test_package_does_not_import_qt(package: str) -> None:
    offenders = [
        f"{path.relative_to(SRC)}: {name}"
        for path in (SRC / package).rglob("*.py")
        for name in _imported_modules(path)
        if name.split(".")[0] in QT_MODULES
    ]
    assert offenders == []


def test_app_import_is_lightweight() -> None:
    """Importing the entry point must not pull in heavy data libraries (startup time)."""
    code = (
        "import sys, petrogis.app; "
        "heavy = [m for m in ('geopandas', 'rasterio', 'pandas', 'lasio', 'pyqtgraph') "
        "if m in sys.modules]; print(','.join(heavy))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == ""
