import numpy as np
import pytest
from PySide6.QtCore import Qt

from petrogis.core.layers import RasterLayer, VectorLayer, WellLayer
from petrogis.render.base import MouseMode
from petrogis.ui.main_window import MainWindow


@pytest.fixture
def window(qtbot, sample_field):
    w = MainWindow()
    w.resize(1400, 900)
    qtbot.addWidget(w)
    w.show()
    w.load_raster(sample_field.structure_top)
    w.load_vector(sample_field.field_boundary)
    w.import_wells(sample_field.wells_csv)
    qtbot.waitUntil(lambda: len(w.project) == 3 and w.tasks.pending == 0, timeout=10000)
    return w


def _layer(window, cls):
    return next(layer for layer in window.project.layers if isinstance(layer, cls))


def test_identify_well_boundary_and_raster(window, qtbot):
    wells = _layer(window, WellLayer)
    x, y = wells.xy()[wells.find("AB-02")]
    window.identify_action.trigger()
    assert window.canvas.mouse_mode is MouseMode.IDENTIFY
    window.canvas.clicked.emit(float(x), float(y))

    dialog = window.identify_dialog
    assert dialog.isVisible()
    tree = dialog.tree
    top_names = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
    assert top_names[:2] == ["wells", "field_boundary"]
    well_item = tree.topLevelItem(0).child(0)
    assert well_item.text(0) == "AB-02"
    fields = {
        well_item.child(i).text(0): well_item.child(i).text(1)
        for i in range(well_item.childCount())
    }
    assert fields["uwi"] == "AZ-ABS-0002" and fields["Status"] == "OIL"
    # The selected hit is highlighted on the map.
    assert window.canvas._highlight.points.data.size == 1

    raster = _layer(window, RasterLayer)
    qtbot.waitUntil(lambda: raster.id in dialog._raster_items, timeout=5000)
    value = float(dialog._raster_items[raster.id].text(1))
    assert 1400 < value < 1700


def test_identify_empty_spot_then_raster_value_replaces_placeholder(window, qtbot):
    raster = _layer(window, RasterLayer)
    e = raster.extent
    window.canvas.set_mouse_mode(MouseMode.IDENTIFY)
    window.canvas.clicked.emit(e.xmax - 500, e.ymin + 3000)  # inside raster, away from features
    tree = window.identify_dialog.tree
    assert tree.topLevelItem(0).text(0) == "No features here"
    qtbot.waitUntil(lambda: window.tasks.pending == 0, timeout=5000)
    assert tree.topLevelItemCount() == 1 and tree.topLevelItem(0).text(0) == raster.name


def test_pan_mode_click_still_opens_logs_not_identify(window):
    wells = _layer(window, WellLayer)
    x, y = wells.xy()[0]
    window.canvas.clicked.emit(float(x), float(y))
    assert not window.identify_dialog.isVisible()
    assert window.log_dock.isVisible()


def test_attribute_table_sort_filter_select_zoom(window, qtbot):
    wells = _layer(window, WellLayer)
    window.open_attribute_table(wells.id)
    table = window.attribute_table
    assert window.attribute_dock.isVisible()
    assert "wells" in window.attribute_dock.windowTitle()
    model = table.model
    assert model.rowCount() == 10
    assert model.columns[:4] == ["name", "uwi", "kb", "td"] and "name_key" not in model.columns
    assert model.data(model.index(0, 0)) == "AB-01"

    model.sort(model.columns.index("kb"), Qt.SortOrder.DescendingOrder)
    kbs = [float(model.data(model.index(r, model.columns.index("kb")))) for r in range(10)]
    assert kbs == sorted(kbs, reverse=True)

    table.filter_edit.setText("dry")
    table.apply_filter()
    assert model.rowCount() == 4
    assert "4 of 10" in table.count_label.text()

    table.view.selectRow(0)
    position = int(table.selected_positions()[0])
    assert wells.gdf["Status"].iloc[position] == "DRY"
    assert window.canvas._highlight.points.data.size == 1
    before = window.canvas.view_extent()
    table.zoom_requested.emit(table.selected_positions())
    after = window.canvas.view_extent()
    assert after.width < before.width

    table.select_position(position)
    assert table.selected_positions().tolist() == [position]

    window.project.rename_layer(wells.id, "Absheron wells")
    assert "Absheron wells" in window.attribute_dock.windowTitle()
    window.project.remove_layer(wells.id)
    assert table.layer_id is None and model.rowCount() == 0
    assert window.canvas._highlight.points.data.size == 0


def test_attribute_table_for_polygons_and_rejects_rasters(window):
    boundary = _layer(window, VectorLayer)
    window.open_attribute_table(boundary.id)
    assert window.attribute_table.model.rowCount() == 1
    raster = _layer(window, RasterLayer)
    window.open_attribute_table(raster.id)
    assert window.attribute_table.layer_id == boundary.id  # unchanged


def test_large_table_model_is_virtual(qtbot):
    import geopandas as gpd
    from shapely import points

    from petrogis.ui.attribute_table import AttributeTable

    n = 100_000
    rng = np.random.default_rng(1)
    gdf = gpd.GeoDataFrame(
        {"name": [f"F{i}" for i in range(n)], "value": rng.random(n)},
        geometry=points(rng.random((n, 2)) * 1000),
        crs="EPSG:32639",
    )
    table = AttributeTable()
    qtbot.addWidget(table)
    table.set_layer(VectorLayer(name="big", source="big.gpkg", gdf=gdf))
    assert table.model.rowCount() == n
    table.model.sort(1)
    assert table.model.rowCount() == n
