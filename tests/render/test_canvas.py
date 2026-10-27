import geopandas as gpd
import numpy as np
import pytest
from shapely import LineString, Point, Polygon

from petrogis.core.extent import Extent
from petrogis.core.layers import RasterInfo, RasterLayer, VectorLayer
from petrogis.core.styles import RasterStyle, VectorStyle
from petrogis.io.raster_io import RasterView
from petrogis.render.base import MouseMode
from petrogis.render.pg.canvas import PgMapCanvas
from petrogis.render.pg.items import RasterGraphics, VectorGraphics, colormap_lut


@pytest.fixture
def canvas(qtbot):
    c = PgMapCanvas()
    c.resize(800, 600)
    qtbot.addWidget(c)
    c.show()
    return c


def _vector():
    gdf = gpd.GeoDataFrame(
        geometry=[
            Point(10, 10),
            LineString([(0, 0), (100, 100)]),
            Polygon([(0, 0), (50, 0), (50, 50)]),
        ],
        crs="EPSG:32639",
    )
    return VectorLayer(name="v", source="v.gpkg", gdf=gdf)


def _raster():
    info = RasterInfo(
        crs="EPSG:32639", width=4, height=2, band_count=1, dtype="float32", nodata=None,
        extent=Extent(0, 0, 400, 200), native_resolution=(100, 100),
        stats_min=0, stats_max=7, p2=0.1, p98=6.9,
    )  # fmt: skip
    return RasterLayer(name="r", source="r.tif", info=info)


def test_vector_layer_uses_three_items(canvas):
    layer = _vector()
    canvas.add_layer(layer)
    graphics = canvas.graphics(layer.id)
    assert isinstance(graphics, VectorGraphics)
    assert len(graphics.items) == 3
    assert graphics.points.data.size == 1
    layer.style = VectorStyle(color="#ff0000", opacity=0.5)
    canvas.update_layer_style(layer)
    graphics = canvas.graphics(layer.id)  # vector graphics are rebuilt on restyle
    assert graphics.lines.opts["pen"].color().name() == "#ff0000"
    assert graphics.points.opacity() == pytest.approx(0.5)
    canvas.set_layer_visible(layer.id, False)
    assert not graphics.lines.isVisible()
    with pytest.raises(ValueError):
        canvas.add_layer(layer)
    canvas.remove_layer(layer.id)
    assert not canvas.has_layer(layer.id)


def test_layer_order_sets_z(canvas):
    a, b = _vector(), _raster()
    canvas.add_layer(a)
    canvas.add_layer(b)
    canvas.set_layer_order([a.id, b.id])
    za = canvas.graphics(a.id).items[0].zValue()
    zb = canvas.graphics(b.id).items[0].zValue()
    assert za > zb


def test_raster_view_flipped_and_placed(canvas):
    layer = _raster()
    canvas.add_layer(layer)
    data = np.ma.masked_invalid(np.array([[0, 1, 2, 3], [4, 5, 6, np.nan]], dtype=np.float32))
    canvas.set_raster_view(layer, RasterView(data=data, extent=Extent(0, 0, 400, 200)))
    image = canvas.graphics(layer.id)
    assert isinstance(image, RasterGraphics)
    shown = image.image.image
    np.testing.assert_array_equal(shown[1], [0, 1, 2, 3])  # north row drawn at the top (high y)
    assert np.isnan(shown[0, 3])
    rect = image.image.mapRectToParent(image.image.boundingRect())
    assert (rect.x(), rect.y(), rect.width(), rect.height()) == (0, 0, 400, 200)
    layer.style = RasterStyle(colormap="magma", vmin=1, vmax=5)
    canvas.update_layer_style(layer)
    assert tuple(image.image.levels) == (1, 5)


def test_view_extent_roundtrip_and_zoom(canvas, qtbot):
    canvas.set_view_extent(Extent(400_000, 4_470_000, 410_000, 4_480_000))
    e = canvas.view_extent()
    # Aspect lock may widen one axis; the requested extent must be fully visible.
    assert e.xmin <= 400_000 and e.xmax >= 410_000
    assert e.ymin <= 4_470_000 + 1 and e.ymax >= 4_480_000 - 1
    width = e.width
    canvas.zoom_by(2.0)
    assert canvas.view_extent().width == pytest.approx(width / 2, rel=0.01)
    with qtbot.waitSignal(canvas.view_changed, timeout=2000):
        canvas.zoom_by(0.5)
    canvas.set_mouse_mode(MouseMode.ZOOM_BOX)
    canvas.set_mouse_mode(MouseMode.PAN)
    w, h = canvas.viewport_size_px()
    assert w > 100 and h > 100


def test_colormap_lut():
    lut = colormap_lut("gray", False)
    assert lut.shape == (256, 4)
    assert tuple(lut[0][:3]) == (0, 0, 0)
    assert tuple(colormap_lut("gray", True)[0][:3]) == (255, 255, 255)


def test_clear_layers_keeps_overlay_in_scene(canvas):
    """Regression: pg.PlotWidget shadows ``clear`` with PlotItem.clear, which wiped the overlay."""
    from shapely import Point

    layer = _vector()
    canvas.add_layer(layer)
    canvas.clear_layers()
    assert not canvas.has_layer(layer.id)
    assert canvas.graphics.__self__ is canvas  # sanity: canvas API intact
    highlight = canvas._highlight
    assert all(item.scene() is not None for item in highlight.items)
    canvas.set_highlight([Point(1, 2)])
    assert highlight.points.scene() is not None and highlight.points.data.size == 1
