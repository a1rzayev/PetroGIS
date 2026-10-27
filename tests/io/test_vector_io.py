import geopandas as gpd
import pytest
from shapely import LineString, Point

from petrogis.io.errors import DataReadError, MissingCRSError
from petrogis.io.vector_io import list_layers, load_vector_layer, read_vector

UTM = "EPSG:32639"


@pytest.fixture(scope="module")
def boundary_utm(sample_field):
    return gpd.read_file(sample_field.field_boundary).to_crs(UTM)


def test_geojson_is_reprojected(sample_field, boundary_utm):
    gdf = read_vector(sample_field.field_boundary, UTM)
    assert gdf.crs.to_epsg() == 32639
    assert gdf.geometry.iloc[0].equals_exact(boundary_utm.geometry.iloc[0], tolerance=0.01)


def test_shapefile(tmp_path, boundary_utm):
    path = tmp_path / "boundary.shp"
    boundary_utm[["geometry"]].to_file(path)
    layer = load_vector_layer(path, UTM)
    assert layer.name == "boundary"
    assert layer.feature_count == 1
    assert layer.paths.rings.shape[0] > 10


def test_geopackage_layers(tmp_path, boundary_utm):
    path = tmp_path / "field.gpkg"
    boundary_utm.to_file(path, layer="boundary", driver="GPKG")
    faults = gpd.GeoDataFrame(
        {"name": ["F1"]}, geometry=[LineString([(401000, 4474000), (408000, 4477000)])], crs=UTM
    )
    faults.to_file(path, layer="faults", driver="GPKG")

    assert sorted(list_layers(path)) == ["boundary", "faults"]
    layer = load_vector_layer(path, UTM, layer="faults")
    assert layer.name == "field - faults"
    assert layer.source_layer == "faults"
    assert layer.paths.lines.shape == (2, 2)


def test_kml_defaults_to_wgs84(tmp_path):
    path = tmp_path / "wells.kml"
    gdf = gpd.GeoDataFrame({"Name": ["W1"]}, geometry=[Point(49.87, 40.41)], crs="EPSG:4326")
    gdf.to_file(path, driver="KML")
    out = read_vector(path, UTM)
    x, y = out.geometry.iloc[0].x, out.geometry.iloc[0].y
    assert 380_000 < x < 420_000 and 4_460_000 < y < 4_490_000


def test_missing_crs(tmp_path):
    # GeoJSON always implies WGS 84, so use a shapefile without a .prj.
    shp = tmp_path / "nocrs.shp"
    with pytest.warns(UserWarning, match="'crs' was not provided"):
        gpd.GeoDataFrame(geometry=[Point(405000, 4475000)]).to_file(shp)
    with pytest.raises(MissingCRSError):
        read_vector(shp, UTM)
    gdf = read_vector(shp, UTM, assume_crs=UTM)
    assert gdf.geometry.iloc[0].x == 405000


def test_drops_null_geometries(tmp_path):
    path = tmp_path / "mixed.gpkg"
    gpd.GeoDataFrame({"a": [1, 2]}, geometry=[Point(1, 1), None], crs=UTM).to_file(path)
    assert len(read_vector(path, UTM)) == 1


def test_errors_are_user_facing(tmp_path):
    with pytest.raises(DataReadError, match="File not found"):
        read_vector(tmp_path / "nope.shp", UTM)
    bad = tmp_path / "bad.geojson"
    bad.write_text("this is not json")
    with pytest.raises(DataReadError, match=r"Cannot read bad\.geojson"):
        read_vector(bad, UTM)
    txt = tmp_path / "a.txt"
    txt.write_text("x")
    with pytest.raises(DataReadError, match="Unsupported"):
        read_vector(txt, UTM)
