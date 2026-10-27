# PetroGIS

Cross-platform desktop GIS for petroleum engineering and reservoir geology. It focuses on wells, logs and
subsurface data. It is not a QGIS clone.

## Commands

```bash
uv sync                                   # install (creates .venv, Python pinned in .python-version)
uv run petrogis                           # run the app
uv run pytest                             # tests (Qt tests run offscreen via tests/conftest.py)
uv run ruff check . && uv run ruff format --check .   # lint + format check
uv run ruff format .                      # format
uv run python scripts/make_sample_data.py # generate the synthetic demo field into sample_data/
uv run python scripts/screenshot.py       # offscreen screenshot -> screenshots/main_window.png
uv run python scripts/screenshot.py out.png --demo   # same, with the sample field loaded
```

## Stack

Python 3.11+ (dev pinned to 3.12), PySide6, pyqtgraph, geopandas, shapely 2, pyproj, pyogrio, rasterio,
lasio, numpy, pandas, openpyxl, pyarrow (fast Arrow vector reads). Tests: pytest + pytest-qt. Lint/format: ruff. Deps: uv + pyproject.toml.

## Architecture rules

- src layout: `src/petrogis/{core,io,render,ui,analysis}`, plus `tests/`, `scripts/`, `sample_data/`.
- `core/` and `io/` must NOT import Qt (or pyqtgraph). They are pure Python and fully unit-tested.
  `tests/test_architecture.py` enforces this.
- `ui/` talks to core only through the `Project` object and Qt signals (via a QObject bridge).
- The map renderer sits behind the `render/base.py` interface. `render/pg/` is the pyqtgraph
  implementation, so a QGraphicsView or OpenGL renderer can replace it later.
- Every layer is reprojected to the project CRS on load. The default CRS is EPSG:32639 (WGS 84 / UTM 39N).
- Large rasters are read downsampled with rasterio `out_shape`. Never load a full-resolution raster for display.
- Project files are JSON (`.pgis`) and store layer sources, styles, order and CRS. Data stays in the original files
  or a GeoPackage. Paths are stored relative to the project file when possible.
- `Project.layers[0]` is the topmost layer, matching the layer panel order. Project changes go out as
  `ProjectEvent`s through `Project.subscribe` (plain callbacks). The UI bridges them to Qt signals.
- The project CRS can only change while the project is empty. Reprojecting loaded layers is a
  planned extension.
- Readers raise `io.errors.DataReadError`, whose message is shown to the user as-is.
  `MissingCRSError` means the UI should ask the user for a CRS. KML is always read as EPSG:4326.
- Raster reads are stateless: each call opens and closes the file, so they are safe from worker
  threads. `read_raster_view` never upsamples beyond native resolution, and warps with a
  `WarpedVRT` when the raster CRS differs from the project CRS.
- UI wiring: `ui/project_bridge.py` turns Project events into Qt signals. `ui/map_controller.py`
  keeps the canvas in sync with the project and schedules raster re-reads on (debounced)
  `view_changed`. Superseded raster results are dropped by a per-layer token. `ui/layer_panel.py`
  writes user edits (visibility, rename, drag order) to the project and redraws from events.
- Background work goes through `ui/workers.TaskRunner.submit(fn, *args, on_result=, on_error=)`.
  Callbacks run on the UI thread. Load functions in io/ must be pure and thread-safe.
- Map canvas: each vector layer is drawn with 3 pyqtgraph items (polygon rings, lines, points),
  whatever its feature count. Rasters use one ImageItem fed by `read_raster_view`. In-layer z order
  is rings < lines < points.
- Wells: `core.wells.normalize_well_name` is the single matching key ("AB-01", "ab_1" and "AB 1"
  all give "AB1"). `WellLayer` stores it as `name_key` and looks wells up with `find()`. Header
  import is two-step (`read_well_table`, then `build_well_layer`), so the UI can show the
  column-mapping dialog only when auto-detection is incomplete. Without the dialog, the source
  CRS is the project CRS, or EPSG:4326 when headers or values look like lon/lat. Rows with bad
  data and duplicate names become user-visible warnings, never silent drops.
- Labels: `render/labels.place_labels` (Qt-free) lays out labels greedily in screen space.
  Each label tries right, left, above and below, avoids other labels and well symbols, and
  labels are hidden entirely above `MAX_VISIBLE_ANCHORS`. `WellLabelItem` paints all labels in
  one `paint()` call.
- Logs: `core/mnemonics.py` holds the curve alias table (family to mnemonics, in priority order).
  Add vendor mnemonics there and nowhere else. `WellLog.curve_for(family)` resolves through it.
  LAS files attach to a `WellLayer` via `Project.attach_logs` (keyed by `name_key`, which emits
  LAYER_CHANGED "logs"). `match_logs` matches on the LAS WELL name, then UWI, then file name.
  Unmatched files and read failures are reported to the user. Depth is MD in the file's unit.
  Phase 1 assumes vertical wells.
- Log viewer: one pyqtgraph track per family, with Y linked and inverted. Value scales are fixed
  (GR 0-150, deep resistivity 0.2-2000 log, NPHI 0.45 to -0.15 with RHOB 1.95-2.95 on a second
  ViewBox). Disable auto SI prefixes on track axes.
- Identify and attributes: `core/attributes.py` (Qt-free) does value formatting, filtering,
  sorting and `identify()`. The attribute table model is virtual. It formats visible cells only
  and sorts and filters an array of row positions, never copying the frame. Map clicks open the
  identify popup in Identify mode (I) and well logs otherwise. Raster values at the click are
  sampled in the background with `io.raster_io.sample_raster`. The canvas selection overlay is
  `canvas.set_highlight(geoms)`; Esc clears it.
- `analysis/` holds Phase 2+ algorithms. They take and return core objects and never import Qt.
- The synthetic demo field generator lives in `petrogis/sample_data.py` (Qt-free).
  `scripts/make_sample_data.py` is a thin CLI around it, and tests use the session fixture `sample_field`
  in `tests/conftest.py`. The field boundary GeoJSON is written in EPSG:4326 (per RFC 7946), so loading
  it exercises reprojection. Everything else is EPSG:32639. LAS WELL names and curve mnemonics vary
  on purpose, and `OFFSET-1` has no matching well header.

## Performance rules

The app must stay responsive with real field data.

- Keep startup lazy: `petrogis.app` must not import geopandas/rasterio/pandas/lasio/pyqtgraph at module
  level (a test enforces this). Import heavy libraries inside the functions or windows that use them.
- Never block the UI thread on file IO. Load layers, LAS batches and raster reads in a `QThreadPool` worker
  and hand the results back through signals. Core/io functions stay synchronous and pure, and ui/ decides
  which thread runs them.
- Vector rendering uses one NaN-separated path per geometry type per layer, not one item per feature.
  Build the arrays once, vectorised with shapely 2, and cache them on the layer.
- Raster display reads at screen resolution for the current view. Re-reads are debounced on pan/zoom and
  cached by (window, out_shape).
- Use a spatial index (shapely `STRtree` / geopandas `sindex`) for identify and picking. Never scan every feature.
- Well labels use level of detail: hide or thin them when they would overlap at the current zoom.
- Use numpy/pandas vectorised ops in io/core. No per-row Python loops over large tables.
- Add a timing test or benchmark when you touch a hot path. Profile before optimising anything non-obvious.

## Conventions

- Type hints everywhere. Prefer small, readable modules over clever abstractions.
- No silent exception swallowing. In the UI, show errors with `ui.errors.show_error` (it logs with traceback
  and opens a dialog). Logs go to the per-user data dir (`.../PetroGIS/logs/petrogis.log`).
- Tests cover everything in core/ and io/. Tests use the sample-data generator for fixtures.
- After each milestone, run ruff, pytest and the offscreen screenshot.
- Git: stage each logical change and propose a commit message. The maintainer commits and pushes.
- If a library choice is unclear or conflicts with these rules, ask. Don't guess.
