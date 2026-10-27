import time

import numpy as np
import pytest

from petrogis.render.labels import MAX_VISIBLE_ANCHORS, place_labels

VIEW = (0.0, 0.0, 800.0, 600.0)


def test_far_apart_labels_all_placed():
    px = np.array([100.0, 400.0, 700.0])
    py = np.array([100.0, 300.0, 500.0])
    placed = place_labels(px, py, np.full(3, 40.0), 12.0, VIEW, gap=6.0)
    assert placed.indices.tolist() == [0, 1, 2]
    # Default position is to the right of the anchor, vertically centred.
    assert placed.x.tolist() == [106.0, 406.0, 706.0]
    assert placed.y.tolist() == [94.0, 294.0, 494.0]


def test_overlapping_labels_try_other_sides_then_cull():
    # Anchors on top of each other: the right and left slots are used, the rest is cut.
    px = np.full(4, 200.0)
    py = np.full(4, 200.0)
    placed = place_labels(px, py, np.full(4, 50.0), 12.0, VIEW)
    assert placed.indices.tolist() == [0, 1]
    assert placed.x[1] < 200 < placed.x[0]


def test_label_goes_above_when_both_sides_are_blocked():
    px = np.array([200.0, 230.0, 170.0])
    py = np.array([200.0, 200.0, 200.0])
    placed = place_labels(px, py, np.full(3, 50.0), 12.0, VIEW, symbol_size=5.0)
    assert placed.indices[0] == 0
    assert placed.y[0] + 12.0 < 200.0  # above its anchor


def test_offscreen_anchors_skipped():
    px = np.array([-50.0, 400.0, 900.0])
    py = np.array([10.0, 300.0, 10.0])
    assert place_labels(px, py, np.full(3, 30.0), 12.0, VIEW).indices.tolist() == [1]


def test_too_many_visible_hides_all():
    n = MAX_VISIBLE_ANCHORS + 1
    px = np.linspace(1, 799, n)
    py = np.full(n, 300.0)
    assert place_labels(px, py, np.full(n, 30.0), 12.0, VIEW).size == 0


@pytest.mark.perf
def test_dense_field_is_fast():
    rng = np.random.default_rng(0)
    n = MAX_VISIBLE_ANCHORS
    px, py = rng.uniform(0, 1600, n), rng.uniform(0, 1000, n)
    start = time.perf_counter()
    placed = place_labels(px, py, np.full(n, 45.0), 14.0, (0, 0, 1600, 1000))
    elapsed = time.perf_counter() - start
    assert 0 < placed.size < n
    assert elapsed < 0.1, f"placing {n} labels took {elapsed * 1000:.0f} ms"


def test_symbols_push_labels_to_another_side():
    # Anchor 0's right-hand label would run over anchor 1's symbol, so it moves left.
    px = np.array([100.0, 130.0])
    py = np.array([100.0, 100.0])
    widths = np.array([60.0, 20.0])
    placed = place_labels(px, py, widths, 12.0, VIEW, gap=7.0, symbol_size=9.0)
    assert placed.indices.tolist() == [0, 1]
    assert placed.x[0] + 60.0 < 100.0  # left of anchor 0
    assert placed.x[1] > 130.0  # right of anchor 1
