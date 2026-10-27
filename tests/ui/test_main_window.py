from pytestqt.qtbot import QtBot

from petrogis.core.crs import DEFAULT_PROJECT_CRS
from petrogis.ui.main_window import MainWindow


def test_main_window_shell(qtbot: QtBot) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    menus = [action.text().replace("&", "") for action in window.menuBar().actions()]
    assert menus == ["File", "Layers", "View", "Help"]
    assert window.layer_dock.isVisible()
    assert window.crs_label.text() == DEFAULT_PROJECT_CRS
