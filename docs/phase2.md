# Phase 2 design notes

How each planned feature fits into the Phase 1 architecture. These notes are design intent, not
commitments. Each feature still gets its own plan before implementation. The rules in
[CLAUDE.md](../CLAUDE.md) apply throughout:

- Algorithms are pure functions in `core/` or `analysis/`, with no Qt.
- File formats live in `io/`.
- Rendering sits behind `render/`.
- Every long-running step goes through `ui.workers.TaskRunner`.
- Computed results are written to files (GeoTIFF or GeoPackage) and come back as ordinary layers,
  so `.pgis` projects keep storing references only.

## 0. Prerequisite: a layer-type registry

Phase 1 dispatches on layer type with `isinstance` in three places:

- `render/pg/canvas.py` (`add_layer`)
- `io/project_io.py` (`_layer_to_dict`, `_load_layer`)
- `ui/main_window.py` (`edit_layer_style`)

That is fine for three layer kinds, but the first new kind (trajectories, tops or contour sets)
should introduce a small registry instead. Each layer kind registers:

| Concern | Registered callable | Lives in |
|---|---|---|
| Serialise / load | `to_dict(layer, base) -> dict`, `load(entry, base, crs, warnings) -> Layer` | `io/` |
| Render | `LayerGraphics` factory | `render/pg/` |
| Style editor | `edit_style(style, layer, parent) -> Style \| None` | `ui/dialogs/` |

The plugin API (section 8) builds on the same registry.

## 1. Deviation surveys and trajectories (minimum curvature)

- **Model** (`core/trajectory.py`): `Survey(md, inc, azi)` arrays become a `Trajectory` with
  arrays `md, tvd, dx, dy` (offsets from the surface location) computed by the minimum-curvature
  method, with the ratio factor `RF = 2/β · tan(β/2)` and the `β → 0` limit handled. Add
  interpolation helpers `md_to_tvd`, `md_to_xy` and `tvd_to_md`. Test it against published
  worked examples, and against a vertical well (TVD = MD) and a constant-build arc (analytic).
- **Storage:** `WellLayer.trajectories: dict[name_key, Trajectory]`, keyed like logs. Wells with
  no survey stay vertical, which is the current behaviour.
- **IO** (`io/survey_io.py`): CSV/Excel surveys, one file per well or one file with a well column.
  Reuse the `wells_io` pattern: `read_*_table`, then alias-based column detection, then the
  mapping dialog only when detection fails. Matching uses `normalize_well_name`.
- **Map:** draw well paths as one NaN-separated `PlotCurveItem` per layer (constant item count,
  as with vectors), plus a bottom-hole symbol scatter.
- **Log viewer:** a depth-reference selector (MD / TVD / TVDSS = TVD − KB). LAS data stays in MD.
  Only the y values passed to the curves change.
- **Project file:** well entries gain `surveys: [{path, path_abs, import_options}]`. Bump
  `PROJECT_VERSION` to 2, and `read_project_file` must keep accepting version 1.

## 2. Formation tops

- **Model** (`core/tops.py`): `Top(name_key, surface, md, source)`. TVDSS and XY are derived
  through the trajectory when one exists. `TopSet` holds the surfaces in stratigraphic order
  with display colours.
- **IO:** CSV/Excel picks (well, surface, MD), using the same detect-then-map flow.
- **Log viewer:** horizontal lines across all tracks (`pg.InfiniteLine`, angle 0) labelled with
  the surface name.
- **Map:** a "tops of surface X" point layer at each pick's XY, with TVDSS as an attribute. This
  is the input to gridding.

## 3. Gridding, kriging and contours

- **Gridding** (`analysis/gridding.py`): `grid(points_xyz, extent, cell_size, method, **params)`
  returns a 2D array plus a transform. Start with inverse-distance weighting and minimum-curvature
  splines (numpy/scipy). Kriging needs a library decision (see open questions). It runs in a
  worker, writes a GeoTIFF via a new `io.raster_io.write_raster`, and loads back as a normal
  `RasterLayer`.
- **Contours** (`analysis/contours.py`): contour a raster at an interval into a GeoDataFrame of
  lines with a `value` attribute. It is written to GeoPackage and loads as a `VectorLayer`.
  Labelling contours along lines reuses the screen-space LOD idea from `render/labels.py`.
- **Performance:** grid at the requested cell size, never the screen size. The display then uses
  the normal `read_raster_view` path.

## 4. Volumetrics: GRV, STOIIP and Monte Carlo

- **Pure functions** (`analysis/volumetrics.py`):
  - `grv(top, base, contact_depth, cell_area, mask)` sums
    `max(0, min(base, contact) − top) · cell_area` over the polygon mask. Depths are TVDSS,
    positive down.
  - `stoiip = GRV · NTG · φ · (1 − Sw) / Bo`, and GIIP likewise with `Bg`.
  - Test against analytic shapes: box, cone and paraboloid closures.
- **Monte Carlo:** vectorised draws (`numpy.random.Generator`) for NTG, φ, Sw, Bo and
  contact depth. The distributions are triangular, normal, lognormal and uniform, and correlations
  can be added later. A depth-dependent GRV is precomputed as a GRV(contact) curve and
  interpolated, so each draw costs O(1) and 100k draws stay interactive.
- **Reporting:** use the petroleum convention, where **P90 is the low case** (90% probability of
  exceeding). The results dialog shows a histogram and cumulative curve (pyqtgraph) plus a
  P90/P50/P10 table, exportable to CSV.
- **Inputs from the project:** top surface raster, optional base raster or constant thickness,
  contact depth, and boundary polygon (a vector layer).

## 5. Cross-sections

- **Interaction:** a new `MouseMode.DRAW_LINE` on the canvas collects a polyline.
- **Model** (`core/section.py`):
  - Distance along the line.
  - Raster profiles sampled along it (vectorised `rasterio` sampling at a fixed spacing).
  - Wells within a buffer projected onto the line (`shapely.line_locate_point`). With
    trajectories, their paths are projected too.
- **View:** a dock with distance on x and TVDSS on y (inverted). It shows surfaces as curves,
  wells as vertical or deviated paths, optional log curves drawn at each well's position, and
  tops as markers. It reuses the log-viewer track code for the log curves.

## 6. 3D view

This is a separate renderer that consumes the same core objects:

- Surface rasters as decimated meshes, with the decimation level chosen from the view.
- Trajectories as polylines.
- Tops as points.
- Optionally the boundary draped on a surface.

It is not built on top of the 2D canvas. The backend choice is an open question (below).

## 7. SEG-Y

- **IO** (`io/segy_io.py`): header scan for geometry, inline/crossline ranges and the survey
  outline. Traces are read lazily, never loading the whole cube.
- **Map:** the survey outline appears as a polygon layer, and a click picks an inline or
  crossline.
- **Section display:** amplitudes as an image in the cross-section view, read at screen resolution
  with the same rule as rasters.

## 8. Python plugin API

- **Discovery:** entry points in the `petrogis.plugins` group, each exposing
  `activate(api: PluginAPI)`.
- **`PluginAPI`:** a deliberately small, stable facade. It offers the project (read and
  modify through `Project` methods), `add_layer`, `submit_task` (the `TaskRunner`), `add_action`
  (menu or toolbar) and the layer-type registry from section 0. Plugins never receive widgets or
  internals directly.
- **Failure isolation:** a plugin that raises on activation is disabled and reported. The app
  keeps running.

## 9. Packaging

- PyInstaller or Briefcase. Either way, the GDAL and PROJ data files that ship inside the
  `rasterio`, `pyogrio` and `pyproj` wheels must be bundled. Verify with an installed build that
  opens the demo project with no network access.
- CI should build on Windows, macOS and Linux, and run the definition-of-done test against the
  frozen app.

## Cross-cutting items carried over from Phase 1

- **Changing the project CRS with layers loaded:** re-read every source in a worker (the readers
  already reproject on load) and swap the layers in atomically.
- **Raster sources with no CRS:** prompt for one, as vectors already do. This needs an
  `assume_crs` passed to `WarpedVRT(src_crs=...)`.

## Open questions (decide before building)

1. **Kriging library:** `gstools` (variogram fitting plus kriging, actively maintained) or
   `pykrige` (simpler API).
2. **3D backend:** `pyqtgraph.opengl` (no new dependency, basic) or `pyvista`/VTK (far more
   capable, heavier install).
3. **SEG-Y reader:** `segyio` (the standard choice), or something else?
4. **Contouring:** `contourpy` (small, fast, the engine behind matplotlib's contours) is the
   suggestion.
