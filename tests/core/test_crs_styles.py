import pytest

from petrogis.core.crs import CRSError, crs_equal, crs_label, normalize_crs
from petrogis.core.styles import RasterStyle, VectorStyle


def test_normalize_crs_variants():
    assert normalize_crs("EPSG:32639") == "EPSG:32639"
    assert normalize_crs(32639) == "EPSG:32639"
    assert normalize_crs("epsg:4326") == "EPSG:4326"
    assert crs_equal("EPSG:32639", 32639)
    assert "UTM zone 39N" in crs_label("EPSG:32639")
    with pytest.raises(CRSError):
        normalize_crs("not a crs")


def test_vector_style_roundtrip_and_validation():
    style = VectorStyle(color="#ff0000", line_width=2.0)
    assert VectorStyle.from_dict(style.to_dict()) == style
    assert VectorStyle.from_dict({"color": "#00ff00", "future_key": 1}).color == "#00ff00"
    with pytest.raises(ValueError):
        VectorStyle(color="red")
    with pytest.raises(ValueError):
        VectorStyle(point_symbol="?")


def test_raster_style_validation():
    assert RasterStyle.from_dict(RasterStyle(vmin=1, vmax=2).to_dict()) == RasterStyle(
        vmin=1, vmax=2
    )
    with pytest.raises(ValueError):
        RasterStyle(vmin=5, vmax=1)
    with pytest.raises(ValueError):
        RasterStyle(colormap="rainbow-unicorn")
