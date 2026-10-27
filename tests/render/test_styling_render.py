"""Rendering of classified styles, polygon fills, labels and well status symbols."""

import time

import geopandas as gpd
import numpy as np
import pytest
from PySide6.QtCore import Qt
from shapely import LineString, Point, Polygon

from petrogis.core.extent import Extent
from petrogis.core.layers import VectorLayer, WellLayer
from petrogis.core.legend import LegendEntry
from petrogis.core.styles import Category, ClassBreak, LabelStyle, VectorStyle, WellStyle
from petrogis.render.pg.canvas import PgMapCanvas
from petrogis.render.pg.items import FillItem, VectorGraphics
from petrogis.render.pg.legend_icons import legend_icon
from petrogis.render.pg.symbols import marker_shape
from petrogis.render.pg.well_items import WellGraphics


@pytest.fixture
def canvas(qtbot):
    c = PgMapCanvas()
    c.resize(800, 600)
    qtbot.addWidget(c)
    c.show()
    return c


def _polygons():
    gdf = gpd.GeoDataFrame(
        {"zone": ["A", "B", "A"], "area_m": [1.0, 5.0, 9.0], "name": ["p1", "p2", "p3"]},
        geometry=[
            Polygon([(0, 0), (10, 0), (10, 10), (0, 10)], [[(2, 2), (2, 4), (4, 4), (4, 2)]]),
            Polygon([(20, 0), (30, 0), (30, 10)]),
            Polygon([(40, 0), (50, 0), (50, 10)]),
        ],
    )
    return VectorLayer(name="zones", source="z.gpkg", gdf=gdf)


def test_categorized_layer_draws_one_item_set_per_class(canvas):
    layer = _polygons()
    layer.style = VectorStyle(
        renderer="categorized",
        classify_by="zone",
        categories=[Category("A", "#ff0000"), Category("B", "#00ff00")],
        fill_opacity=0.5,
    )
    canvas.add_layer(layer)
    graphics = canvas.graphics(layer.id)
    assert isinstance(graphics, VectorGraphics)
    assert [c.color for c in graphics.classes] == ["#ff0000", "#00ff00"]
    assert len(graphics.fills) == 2 and len(graphics.items) == 4  # 2 fills + 2 outlines
    fill = graphics.fills[0]
    assert fill._brush.color().alphaF() == pytest.approx(0.5, abs=0.01)
    # Holes stay empty: the winding rule with oriented rings.
    assert fill._path.contains(pt(1, 1)) and not fill._path.contains(pt(3, 3))
    # Fills sit below outlines inside the layer, and the layer below the next one up.
    canvas.set_layer_order([layer.id])
    zs = [item.zValue() for item in graphics.items]
    assert zs == sorted(zs) and zs[-1] < 2.0


def pt(x, y):
    from PySide6.QtCore import QPointF

    return QPointF(x, y)


def test_restyle_rebuilds_and_keeps_z_and_visibility(canvas):
    layer = _polygons()
    canvas.add_layer(layer)
    canvas.set_layer_order([layer.id])
    canvas.set_layer_visible(layer.id, False)
    layer.visible = False
    layer.style = VectorStyle(
        renderer="graduated",
        classify_by='"area_m"',
        breaks=[
            ClassBreak(0, 3, "#000000"),
            ClassBreak(3, 6, "#444444"),
            ClassBreak(6, 9, "#888888"),
        ],
    )
    canvas.update_layer_style(layer)
    graphics = canvas.graphics(layer.id)
    assert len(graphics.classes) == 3
    assert graphics.z == 1.0 and not any(item.isVisible() for item in graphics.items)
    assert all(item.scene() is not None for item in graphics.items)


def test_bad_expression_falls_back_and_reports(canvas):
    layer = _polygons()
    layer.style = VectorStyle(
        renderer="categorized",
        classify_by='"gone"',
        categories=[Category("A", "#ff0000")],
        labels=LabelStyle(enabled=True, expression='"also_gone"'),
    )
    canvas.add_layer(layer)
    graphics = canvas.graphics(layer.id)
    assert len(graphics.classes) == 1 and graphics.labels is None
    assert "gone" in graphics.error and "Labels" in graphics.error


def test_labels_for_vector_layer(canvas):
    layer = _polygons()
    layer.style = VectorStyle(
        labels=LabelStyle(enabled=True, expression="name || ' (' || zone || ')'")
    )
    canvas.add_layer(layer)
    labels = canvas.graphics(layer.id).labels
    assert labels.texts == ["p1 (A)", "p2 (B)", "p3 (A)"]
    canvas.set_view_extent(Extent(-5, -20, 55, 30))
    canvas.grab()
    assert labels.placement is not None and labels.placement.size == 3


def test_empty_layer_still_renders(canvas):
    layer = VectorLayer(name="e", source="e", gdf=gpd.GeoDataFrame(geometry=[]))
    layer.style = VectorStyle(labels=LabelStyle(enabled=True, expression="1"))
    canvas.add_layer(layer)
    graphics = canvas.graphics(layer.id)
    assert len(graphics.items) == 4 and graphics.points is not None  # 3 empty + labels


def test_well_status_symbols(canvas):
    gdf = gpd.GeoDataFrame(
        {"name": ["A", "B", "C", "D"], "Status": ["OIL", "DRY", "Water injector", "??"]},
        geometry=[Point(i * 100, 0) for i in range(4)],
    )
    layer = WellLayer(name="wells", source="w.csv", gdf=gdf, style=WellStyle(color="#123456"))
    canvas.add_layer(layer)
    graphics = canvas.graphics(layer.id)
    assert isinstance(graphics, WellGraphics)
    spots = graphics.symbols.data
    assert len(spots) == 4
    assert spots["symbol"][0] == "o" and spots["brush"][0].color().name() == "#1a9641"
    assert spots["symbol"][1] is marker_shape("crossed").symbol
    assert spots["brush"][1].color().alpha() == 0  # dry hole: open circle with a cross
    assert spots["brush"][3].color().name() == "#123456"  # unknown status: layer colour

    layer.style = WellStyle(symbol_by_status=False, color="#ff0000", label_expression="name || '*'")
    canvas.update_layer_style(layer)
    assert {b.color().name() for b in graphics.symbols.data["brush"]} == {"#ff0000"}
    assert graphics.labels.texts == ["A*", "B*", "C*", "D*"]
    layer.style = WellStyle(label_expression='"nope"')
    canvas.update_layer_style(layer)
    assert graphics.labels.texts == ["A", "B", "C", "D"] and "nope" in graphics.error


@pytest.mark.parametrize(
    "shape", ["circle", "sun", "half", "injector", "crossed", "barred", "open", "diamond", "?"]
)
def test_marker_shapes_and_icons(shape, qtbot):
    marker = marker_shape(shape)
    assert marker.symbol
    icon = legend_icon(LegendEntry("x", "well", "#ff0000", shape))
    assert not icon.isNull()


@pytest.mark.parametrize(
    "entry",
    [
        LegendEntry("", "marker", "#00ff00", "s"),
        LegendEntry("", "line", "#00ff00"),
        LegendEntry("", "fill", "#00ff00", fill_opacity=0.4),
        LegendEntry("", "ramp", colormap="magma", invert=True),
    ],
)
def test_legend_icons(entry, qtbot):
    image = legend_icon(entry).pixmap(28, 16).toImage()
    painted = [image.pixelColor(x, y).alpha() for x in range(28) for y in range(16)]
    assert max(painted) > 0


def test_fill_item_bounds():
    rings = np.array(
        [[0, 0], [1, 0], [1, 1], [0, 0], [np.nan, np.nan], [5, 5], [6, 5], [6, 6], [5, 5]]
    )
    item = FillItem(rings)
    rect = item.boundingRect()
    assert (rect.left(), rect.bottom(), rect.right()) == (0, 6, 6)
    item.set_brush("#ff0000", 2.0)
    assert item._brush.color().alphaF() == 1.0
    assert item._path.fillRule() == Qt.FillRule.WindingFill


@pytest.mark.perf
def test_graduated_100k_points_render_within_budget(canvas):
    n = 100_000
    rng = np.random.default_rng(0)
    gdf = gpd.GeoDataFrame(
        {"value": rng.uniform(0, 100, n)},
        geometry=gpd.points_from_xy(rng.uniform(0, 1e5, n), rng.uniform(0, 1e5, n)),
    )
    layer = VectorLayer(name="many", source="many.gpkg", gdf=gdf)
    _ = layer.paths  # built on the loader thread in the app
    breaks = [ClassBreak(i * 20, (i + 1) * 20, c) for i, c in enumerate(
        ["#440154", "#3b518a", "#22908c", "#60c860", "#fde725"])]  # fmt: skip
    layer.style = VectorStyle(renderer="graduated", classify_by="value", breaks=breaks)
    start = time.perf_counter()
    canvas.add_layer(layer)
    canvas.set_view_extent(Extent(0, 0, 1e5, 1e5))
    canvas.grab()
    elapsed = time.perf_counter() - start
    assert len(canvas.graphics(layer.id).classes) == 5
    assert elapsed < 2.0, f"100k graduated points took {elapsed:.2f}s"


@pytest.mark.perf
def test_categorized_100k_polygons_render_within_budget(canvas):
    side = 316
    xs, ys = np.meshgrid(np.arange(side) * 10.0, np.arange(side) * 10.0)
    import shapely

    geoms = shapely.box(xs.ravel(), ys.ravel(), xs.ravel() + 8, ys.ravel() + 8)
    gdf = gpd.GeoDataFrame({"k": np.arange(geoms.size) % 7}, geometry=geoms)
    layer = VectorLayer(name="grid", source="g.gpkg", gdf=gdf)
    _ = layer.paths
    layer.style = VectorStyle(
        renderer="categorized",
        classify_by="k",
        categories=[Category(i, f"#{i * 30:02x}4080") for i in range(7)],
        fill_opacity=0.6,
    )
    start = time.perf_counter()
    canvas.add_layer(layer)
    canvas.set_view_extent(Extent(0, 0, side * 10.0, side * 10.0))
    canvas.grab()
    elapsed = time.perf_counter() - start
    assert elapsed < 3.0, f"100k categorized polygons took {elapsed:.2f}s"


def test_mixed_geometry_layer(canvas):
    gdf = gpd.GeoDataFrame(
        {"k": ["a", "b", "a"]},
        geometry=[Point(0, 0), LineString([(0, 0), (1, 1)]), Polygon([(0, 0), (1, 0), (1, 1)])],
    )
    layer = VectorLayer(name="m", source="m", gdf=gdf)
    layer.style = VectorStyle(
        renderer="categorized", classify_by="k",
        categories=[Category("a", "#ff0000"), Category("b", "#0000ff")], fill_opacity=0.2,
    )  # fmt: skip
    canvas.add_layer(layer)
    graphics = canvas.graphics(layer.id)
    # Class a: fill, ring, point. Class b: line.
    assert len(graphics.items) == 4
    assert graphics.classes[0].points.data.size == 1 and graphics.classes[1].lines is not None
