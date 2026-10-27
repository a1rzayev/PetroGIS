"""Data-derived text must never be interpreted as HTML (security baseline, docs/phase2-plan.md).

Every widget path that shows text from data files gets a markup payload. The payload must come
out as literal text: escaped inside rich text, or shown in a plain-text widget.
"""

import geopandas as gpd
import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMessageBox
from shapely import Point

from petrogis.core.extent import Extent
from petrogis.core.layers import RasterInfo, VectorLayer
from petrogis.core.logs import LogCurve, WellLog
from petrogis.core.project import Project
from petrogis.core.styles import RasterStyle
from petrogis.io.wells_io import read_well_table
from petrogis.ui import errors
from petrogis.ui.dialogs.column_mapping import ColumnMappingDialog
from petrogis.ui.dialogs.layer_picker import LayerPickerDialog
from petrogis.ui.dialogs.style_raster import RasterStyleDialog
from petrogis.ui.layer_panel import LayerPanel
from petrogis.ui.log_viewer import LogViewer
from petrogis.ui.project_bridge import ProjectBridge
from petrogis.ui.text import as_rich_text, plain_label

PAYLOAD = "<b>X</b><img src='x.png'>"


def assert_escaped(rich: str) -> None:
    assert "<img" not in rich and "<b>X</b>" not in rich
    assert "&lt;img" in rich


def labels_showing(widget, text):
    return [lbl for lbl in widget.findChildren(QLabel) if text in lbl.text()]


def test_helpers(qtbot):
    assert as_rich_text("a<b>\nc") == "<p>a&lt;b&gt;<br>c</p>"
    label = plain_label(PAYLOAD)
    assert label.textFormat() == Qt.TextFormat.PlainText and label.text() == PAYLOAD


def test_log_viewer_header_and_axis_labels(qtbot):
    viewer = LogViewer()
    qtbot.addWidget(viewer)
    depth = np.arange(1000.0, 1010.0, 1.0)
    log = WellLog(
        well_name=PAYLOAD,
        uwi=PAYLOAD,
        source=f"/data/{PAYLOAD}.las",
        depth=depth,
        depth_unit=PAYLOAD,
        curves={"GR": LogCurve("GR", PAYLOAD, "", np.full(depth.shape, 50.0))},
    )
    viewer.show_well(PAYLOAD, [log], uwi=PAYLOAD)
    assert_escaped(viewer.header.text())
    assert_escaped(viewer.gr_plot.getAxis("top").labelText)
    assert_escaped(viewer.gr_plot.getAxis("left").labelText)

    viewer.show_well(PAYLOAD, [])
    assert_escaped(viewer.header.text())


@pytest.mark.parametrize("show", ["error", "warning"])
def test_message_boxes_are_plain_text(monkeypatch, show):
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self) or 0)
    if show == "error":
        errors.show_error(None, "Cannot load layer", PAYLOAD, ValueError(PAYLOAD))
    else:
        errors.show_warning(None, "LAS import", PAYLOAD)
    box = shown[0]
    assert box.textFormat() == Qt.TextFormat.PlainText
    assert box.text() == PAYLOAD


def test_layer_panel_tooltip(qtbot):
    project = Project()
    panel = LayerPanel(project, ProjectBridge(project))
    qtbot.addWidget(panel)
    gdf = gpd.GeoDataFrame(geometry=[Point(0, 0)], crs="EPSG:32639")
    project.add_layer(VectorLayer(name=PAYLOAD, source=f"/data/{PAYLOAD}.gpkg", gdf=gdf))
    assert panel.item(0).text() == PAYLOAD  # list items are always plain text
    assert_escaped(panel.item(0).toolTip())


def test_raster_style_dialog_description(qtbot):
    info = RasterInfo(
        crs="EPSG:32639", width=1, height=1, band_count=1, dtype="float32", nodata=None,
        extent=Extent(0, 0, 1, 1), native_resolution=(1, 1),
        stats_min=0, stats_max=1, p2=0.1, p98=0.9, description=PAYLOAD,
    )  # fmt: skip
    dialog = RasterStyleDialog(RasterStyle(), info, PAYLOAD)
    qtbot.addWidget(dialog)
    found = labels_showing(dialog, PAYLOAD)
    assert found and all(lbl.textFormat() == Qt.TextFormat.PlainText for lbl in found)


def test_layer_picker_source_name(qtbot):
    dialog = LayerPickerDialog(PAYLOAD, ["a", "b"])
    qtbot.addWidget(dialog)
    found = labels_showing(dialog, PAYLOAD)
    assert found and all(lbl.textFormat() == Qt.TextFormat.PlainText for lbl in found)


def test_column_mapping_crs_label(qtbot, tmp_path):
    path = tmp_path / "w.csv"
    path.write_text("Well,X,Y\nA,1,2\n")
    dialog = ColumnMappingDialog(read_well_table(path), "EPSG:32639")
    qtbot.addWidget(dialog)
    assert dialog.crs_info.textFormat() == Qt.TextFormat.PlainText
