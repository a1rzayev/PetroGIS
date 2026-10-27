# PetroGIS

Cross-platform desktop GIS for petroleum engineering and reservoir geology. It focuses on wells,
logs and subsurface data. It is not a QGIS clone. Phase 1 is complete (see ROADMAP.md).

Work after Phase 1 follows the stages in `docs/product-plan.md` (Foundation, MVP, Beta, Production
1.0, after 1.0). Milestone specs and the quality gates are in `docs/phase2-plan.md`, and subsurface
designs in `docs/phase2.md`. Every milestone must pass phase2-plan.md's quality gates and security baseline.
In particular, project and style files never contain executable code, credentials live only in
the OS keychain, and data-derived text is never rendered as markup.

## Commands

```bash
uv sync                                   # install (creates .venv, Python pinned in .python-version)
uv run petrogis                           # run the app
uv run petrogis path/to/project.pgis      # run and open a project
uv run pytest                             # tests (Qt tests run offscreen via tests/conftest.py)
uv run pytest -m "not perf" --cov=petrogis.core --cov=petrogis.io --cov-fail-under=90  # coverage gate
uv run pytest -m perf                     # speed budgets only (run without coverage)
uv run ruff check . && uv run ruff format --check .   # lint + format check
uv run ruff format .                      # format
uv run python scripts/make_sample_data.py # generate the synthetic demo field into sample_data/
uv run python scripts/screenshot.py       # offscreen screenshot -> screenshots/main_window.png
uv run python scripts/screenshot.py out.png --demo   # same, with the sample field loaded
```

## Stack

Python 3.11+ (dev pinned to 3.12), PySide6, pyqtgraph, geopandas, shapely 2, pyproj, pyogrio
(+ pyarrow for fast Arrow reads), rasterio, lasio, numpy, pandas, openpyxl. Tests: pytest +
pytest-qt. Lint/format: ruff. Dependencies: uv + pyproject.toml (+ uv.lock).

## Architecture rules

- src layout: `src/petrogis/{core,io,render,ui,analysis}`, plus `tests/`, `scripts/`,
  `sample_data/` (generated, git-ignored) and `docs/`.
- `core/` and `io/` must NOT import Qt or pyqtgraph. They are pure Python and fully unit-tested.
  `tests/test_architecture.py` enforces this. `render/labels.py` and `sample_data.py` are Qt-free too.
- `ui/` talks to core only through the `Project` object and Qt signals (via `ui/project_bridge.py`).
- The map renderer sits behind `render/base.py` (`MapRenderer` protocol + `MouseMode`).
  `render/pg/` is the pyqtgraph implementation, which a QGraphicsView or OpenGL renderer can replace.
- Every layer is reprojected to the project CRS on load. The default is EPSG:32639 (WGS 84 / UTM 39N).
  The project CRS can only change while the project is empty.
- Large rasters are read downsampled with rasterio `out_shape`. Never load full resolution for display.
- Project files are JSON (`.pgis`) and store references only. Data stays in the original files or
  a GeoPackage. Computed results (Phase 2) are written to files and loaded back as layers.
- `analysis/` (Phase 2+) takes and returns core objects and never imports Qt.

## Where things live

| Area | Modules | Notes |
|---|---|---|
| Project model | `core/project.py`, `core/layers.py`, `core/styles.py` | `layers[0]` is topmost. Changes are emitted as `ProjectEvent`s via `subscribe()`. `LAYER_CHANGED.field` is one of name / visible / style / logs. |
| Geometry | `core/geometry.py`, `core/extent.py` | `to_paths()` builds NaN-separated arrays, vectorised. |
| Vectors / rasters IO | `io/vector_io.py`, `io/raster_io.py` | Readers raise `DataReadError` (message shown as-is). `MissingCRSError` means ask the user. KML is always EPSG:4326. Raster reads are stateless (open/close per call), so they are thread-safe. They never upsample, and they warp with `WarpedVRT`. |
| Wells | `core/wells.py`, `io/wells_io.py` | `normalize_well_name` is the single matching key ("AB-01", "ab_1" and "AB 1" all give "AB1"). Import is two-step: `read_well_table`, then `build_well_layer`. The mapping dialog appears only when detection is incomplete. Bad rows and duplicates become warnings. |
| Logs | `core/mnemonics.py`, `core/logs.py`, `io/las_io.py` | Add vendor mnemonics to the alias table only. Matching tries WELL name, then UWI, then file name. Logs attach via `Project.attach_logs`. Depth is MD in the file's unit, and wells are vertical in Phase 1. |
| Attributes / identify | `core/attributes.py` | Formatting, filter and sort over row-position arrays, and `identify()`. |
| Project files | `io/project_io.py` | See "Project file format" below. |
| Rendering | `render/pg/canvas.py`, `items.py`, `well_items.py`, `render/labels.py` | Constant item count per layer. `set_highlight()` draws the selection overlay. |
| App shell | `ui/main_window.py` | Menus, docks, loading flows, project files, identify, map clicks. |
| UI parts | `ui/layer_panel.py`, `log_viewer.py`, `attribute_table.py`, `identify.py`, `dialogs/` | |
| Async | `ui/workers.py`, `ui/map_controller.py` | See "Background work" below. |
| Demo data | `sample_data.py`, `scripts/make_sample_data.py` | Boundary GeoJSON in EPSG:4326 (exercises reprojection), everything else EPSG:32639. LAS names and mnemonics vary on purpose, and `OFFSET-1` is a deliberate orphan. |

## Background work

- Use `ui/workers.TaskRunner.submit(fn, *args, on_result=, on_error=)`. Callbacks run on the UI
  thread. Functions in io/ must be pure and thread-safe.
- Results can arrive late. Wrap main-window load callbacks in `self._current(...)`, which drops
  results once the project has been replaced (New/Open bump `_generation`). Callbacks that look up a
  layer by id must handle `KeyError`, because the user may have removed it.
- `TaskRunner.shutdown()` runs on window close, so results never reach destroyed widgets.
- Raster view reads are scheduled by `MapController` on the debounced `view_changed` signal.
  Superseded results are dropped by a per-layer token. Failures are shown in the status bar.

## Project file format

JSON with `format`, `version`, `crs`, `view` and `layers` (top-first). Each layer stores `source`
(relative to the .pgis) and `source_abs` (fallback), `id`, `name`, `visible` and `style`, plus
kind-specific fields: vector `source_layer` and `assume_crs`, raster `band`, wells
`import_options` (column mapping, source CRS, sheet) and `logs` (paths). Saving is atomic (temp
file plus replace). Loading re-reads every source in a worker, and a broken layer or missing file
becomes a warning, never a failed open. To change the schema, bump `PROJECT_VERSION` and keep
reading older versions. Round-trip identity is tested by comparing `project_to_dict` before and after.

## Performance rules

The app must stay responsive with real field data.

- Keep startup lazy: `petrogis.app` must not import geopandas/rasterio/pandas/lasio/pyqtgraph at
  module level (a test enforces this). Startup to a visible window is about 0.35 s. Keep it there.
- Never block the UI thread on file IO. Use `TaskRunner`.
- Vector rendering uses one NaN-separated path per geometry type per layer, never one item per
  feature. Arrays are built once, vectorised with shapely 2, and cached on the layer (built on the
  loader thread).
- Raster display reads at screen resolution for the current view. Re-reads are debounced on pan
  and zoom.
- Use a spatial index (geopandas `sindex`) for identify and picking. Never scan every feature.
- Labels use level of detail: greedy screen-space placement that avoids collisions, and they are
  hidden above `MAX_VISIBLE_ANCHORS`.
- Tables are virtual. Format only the visible cells, and sort or filter row-position arrays.
- Use numpy/pandas vectorised ops in io/core. No per-row Python loops over large tables.
- Add a timing test when you touch a hot path. Profile before optimising anything non-obvious.

## Testing

- Tests cover everything in core/ and io/. They use the sample-data generator through the
  session fixture `sample_field` (and `sample_dir`).
- `tests/conftest.py` has an autouse fixture that answers "discard" to the unsaved-changes prompt
  and points `RecentProjects` at a temporary QSettings file. Any new modal prompt must be a
  module-level function that can be monkeypatched the same way, or tests will hang.
- UI tests stub `show_error` / `show_warning` via `monkeypatch` on `petrogis.ui.main_window`.
- `tests/test_definition_of_done.py` runs the Phase 1 definition of done through the real
  `petrogis.app.main`. Keep it passing.
- After each milestone, run ruff, pytest and the offscreen screenshot (`--demo`), and look at the
  image.
- Timing tests carry `@pytest.mark.perf`. Coverage tracing slows code, so CI runs them separately
  from the coverage run. Mark any new timing test the same way.
- Malformed-input tests (`tests/io/test_malformed_inputs.py`, hypothesis) cover every reader of
  untrusted files. A new reader gets a property-based test there.
- Data-derived text in widgets: use `ui/text.py` (`plain_label`, `as_rich_text`) or `html.escape`;
  `tests/ui/test_text_safety.py` must cover any new display path.
- CI (`.github/workflows/ci.yml`) pins actions to commit SHAs. When updating an action, look up the
  new commit SHA with `git ls-remote` and keep the tag in a comment. pyright and pip-audit run only
  in CI (`uv run --with pyright==…`, `uvx pip-audit==…`), not in the local dev group.

## Pitfalls

- `pg.PlotWidget.__init__` sets instance attributes (`clear`, `addItem`, `removeItem`,
  `setRange`, `setXRange`, `setYRange`, `autoRange`, `viewRect`, …) that forward to the PlotItem.
  They silently shadow subclass methods with those names. Don't override them. The canvas uses
  `clear_layers()` for this reason.
- Disable auto SI prefixes on pyqtgraph axes that show domain units (log tracks, map axes).
  Otherwise labels like "×0.001" appear.
- pyqtgraph downsampling and clip-to-view assume x is the independent variable. Log curves plot
  depth on y, so don't enable them there.
- `QSettings` returns a bare string for a one-item list. `RecentProjects.paths()` handles this.

## Conventions

- Type hints everywhere. Prefer small, readable modules over clever abstractions.
- No silent exception swallowing. In the UI, show errors with `ui.errors.show_error` (logs with
  traceback and opens a dialog) or `show_warning`. Broad `except Exception` is allowed only where
  the error is re-raised as `DataReadError` (comment which library errors it covers) or shown to
  the user (mark it `# noqa: BLE001 - reason`).
- Logs go to the per-user data dir (macOS:
  `~/Library/Application Support/PetroGIS/PetroGIS/logs/petrogis.log`). lasio is set to WARNING.
- Git: stage each logical change and propose a commit message. The maintainer commits and pushes.
- If a library choice is unclear or conflicts with these rules, ask. Don't guess.
