import geopandas as gpd
import pytest
from PySide6.QtCore import Qt
from shapely import Point

from petrogis.core.layers import VectorLayer
from petrogis.core.project import Project
from petrogis.ui.layer_panel import LayerPanel
from petrogis.ui.project_bridge import ProjectBridge


def _layer(name):
    gdf = gpd.GeoDataFrame(geometry=[Point(0, 0)], crs="EPSG:32639")
    return VectorLayer(name=name, source=f"{name}.shp", gdf=gdf)


@pytest.fixture
def setup(qtbot):
    project = Project()
    bridge = ProjectBridge(project)
    panel = LayerPanel(project, bridge)
    qtbot.addWidget(panel)
    layers = [_layer(n) for n in ("a", "b", "c")]
    for layer in layers:
        project.add_layer(layer)
    return project, panel, layers


def _names(panel):
    return [panel.item(i).text() for i in range(panel.count())]


def test_panel_mirrors_project_order(setup):
    project, panel, (a, _b, c) = setup
    assert _names(panel) == ["c", "b", "a"]
    assert panel.current_layer_id() == a.id or panel.current_layer_id() == c.id
    project.move_layer(a.id, 0)
    assert _names(panel) == ["a", "c", "b"]
    project.remove_layer(c.id)
    assert _names(panel) == ["a", "b"]


def test_checkbox_and_rename_update_project(setup):
    project, panel, (a, _b, c) = setup
    item = panel.item(0)  # c
    item.setCheckState(Qt.CheckState.Unchecked)
    assert c.visible is False
    item.setText("Contours")
    assert c.name == "Contours"
    item.setText("   ")
    assert c.name == "Contours" and item.text() == "Contours"
    project.set_visible(a.id, False)
    assert panel.item(2).checkState() == Qt.CheckState.Unchecked


def test_drag_reorder_is_pushed_to_project(setup):
    project, panel, _layers = setup
    # Simulate a completed drag: move the bottom item to the top of the panel.
    item = panel.takeItem(2)
    panel.insertItem(0, item)
    panel.apply_panel_order()
    assert [layer.name for layer in project.layers] == ["a", "c", "b"]
    assert _names(panel) == ["a", "c", "b"]


def test_remove_request_signal(setup, qtbot):
    _project, panel, (_a, _b, c) = setup
    panel.setCurrentRow(0)
    with qtbot.waitSignal(panel.remove_requested) as blocker:
        panel._remove_current()
    assert blocker.args == [c.id]
