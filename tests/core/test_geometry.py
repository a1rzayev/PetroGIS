import time

import numpy as np
import shapely
from shapely import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)

from petrogis.core.geometry import flatten_parts, nan_separated, to_paths


def _nan_rows(arr):
    return int(np.isnan(arr[:, 0]).sum())


def test_points_and_multipoints():
    paths = to_paths([Point(0, 0), MultiPoint([(1, 1), (2, 2)])])
    np.testing.assert_array_equal(paths.points, [[0, 0], [1, 1], [2, 2]])
    assert paths.lines.size == 0 and paths.rings.size == 0


def test_lines_are_nan_separated():
    paths = to_paths(
        [
            LineString([(0, 0), (1, 1)]),
            MultiLineString([[(2, 2), (3, 3)], [(4, 4), (5, 5), (6, 6)]]),
        ]
    )
    assert paths.lines.shape == (7 + 2, 2)
    assert _nan_rows(paths.lines) == 2
    np.testing.assert_array_equal(paths.lines[0], [0, 0])
    np.testing.assert_array_equal(paths.lines[-1], [6, 6])
    assert np.isnan(paths.lines[2]).all()


def test_polygon_rings_include_holes():
    shell = [(0, 0), (10, 0), (10, 10), (0, 10)]
    hole = [(2, 2), (4, 2), (4, 4), (2, 4)]
    paths = to_paths(
        [Polygon(shell, [hole]), MultiPolygon([Polygon([(20, 20), (21, 20), (21, 21)])])]
    )
    # 3 rings -> 2 separators; closed rings have n+1 coordinates.
    assert _nan_rows(paths.rings) == 2
    assert paths.rings.shape[0] == 5 + 5 + 4 + 2


def test_mixed_collection_none_and_empty():
    geoms = [
        None,
        Point(),
        GeometryCollection([Point(1, 2), LineString([(0, 0), (1, 0)]), MultiPoint([(3, 3)])]),
        LineString(),
    ]
    paths = to_paths(np.array(geoms, dtype=object))
    np.testing.assert_array_equal(paths.points, [[1, 2], [3, 3]])
    assert paths.lines.shape == (2, 2)
    assert to_paths([]).is_empty
    assert to_paths([None]).is_empty


def test_flatten_nested_collections():
    nested = GeometryCollection([GeometryCollection([MultiPoint([(0, 0), (1, 1)])])])
    assert len(flatten_parts([nested])) == 2


def test_nan_separated_single_part_has_no_separator():
    arr = nan_separated(np.array([LineString([(0, 0), (1, 1), (2, 2)])]))
    assert arr.shape == (3, 2) and _nan_rows(arr) == 0


def test_large_input_is_fast():
    rng = np.random.default_rng(0)
    coords = rng.uniform(0, 1e5, size=(100_000, 4, 2))
    lines = shapely.linestrings(coords.reshape(-1, 2), indices=np.repeat(np.arange(100_000), 4))
    start = time.perf_counter()
    paths = to_paths(lines)
    elapsed = time.perf_counter() - start
    assert paths.lines.shape == (500_000 - 1, 2)
    assert elapsed < 1.0, f"to_paths took {elapsed:.2f}s for 100k lines"
