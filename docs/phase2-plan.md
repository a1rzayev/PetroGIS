# Phase 2 plan: QGIS-grade core, built safely

**Scope decision (2026-10-27): core parity. Stage order: see [product-plan.md](product-plan.md).** PetroGIS will offer the QGIS features that petroleum
engineers and geologists use every day, built without QGIS's known classes of bugs and
vulnerabilities, and interleaved with the subsurface features. It stays domain-focused, not a QGIS
clone. Everything in [Out of scope](#out-of-scope) stays out unless this document is changed.

The subsurface design notes (trajectories, tops, gridding, volumetrics, cross-sections, 3D, SEG-Y,
plugins, packaging) are in [phase2.md](phase2.md). This plan schedules them.

## How QGIS maps onto PetroGIS

Based on the QGIS 3.44 user manual.

| QGIS area (manual chapter) | PetroGIS after Phase 1 | Phase 2 target | Milestone |
|---|---|---|---|
| Project files, broken paths | Relative + absolute paths; missing sources become warnings | Autosave and `.pgis` backup; repair-broken-paths dialog | P2.0, P2.1 |
| Projections, datum transformations | Fixed project CRS; automatic transformation | Searchable CRS picker; change CRS with layers loaded; explicit datum transformation choice (Pulkovo 1942 ↔ WGS 84 is common in the Caspian); CRS prompt for rasters with none | P2.1 |
| 2D map view | Pan, zoom, coordinates | Scale bar, scale box, bookmarks, ellipsoidal measuring, overview map | P2.3 |
| Expressions | — | Safe expression engine for filters, labels, field calculator, rule-based styles | P2.2 |
| Style library, labels | Single colour; labels for wells only | Categorized, graduated and rule-based renderers; polygon fill; labels for any layer; legend; hillshade; style save/load; well symbols by status | P2.2 |
| Data sources | Read-only | Delimited text with XY columns; Save As / export with CRS; create and write GeoPackage; georeferencer (later) | P2.1, P2.4 |
| Vector editing, attribute table, joins | Read-only virtual table | Digitizing with snapping, undo/redo, field calculator, selection tools, table joins | P2.3, P2.4 |
| Raster analysis | View and sample | Histogram, raster calculator, clip, reproject | P2.5 |
| Processing framework | — | Algorithm registry, typed parameters, background runs with cancel, history, batch | P2.5 |
| Elevation profile, 3D view | — | Cross-sections (P2.6b); 3D later | P2.6b |
| Print layouts | — | Export to PNG/PDF/SVG with legend, scale bar, north arrow, title, grid | P2.8 |
| OGC services, authentication | — | XYZ/WMTS/WMS basemaps, WFS read-only; credentials in the OS keychain | P2.7 |
| Plugins, Python console | — | Plugin API with explicit trust (after Phase 2) | later |

### Out of scope

Mesh data, vector tiles, point clouds, 3D tiles, GRASS integration, GPS tracking, QGIS Server
(and with it the whole web-output XSS class), atlas and reports, the graphical model designer,
user profiles, and project-embedded Python of any kind.

## Security baseline

Untrusted inputs are project files, style files, data files (vector, raster, LAS, CSV/Excel),
network responses and, later, plugins. Each rule below exists because QGIS shipped the
corresponding problem.

| QGIS problem | PetroGIS rule |
|---|---|
| Project files can carry Python macros and custom expression functions, gated by an "Enable macros" setting whose prompt was historically skipped on some open paths | **Project and style files never contain executable code.** There is nothing to enable. Expressions go through our own whitelisted parser and evaluator, never Python `eval`/`exec`, with limits on input size, depth and evaluation steps. |
| Database passwords stored in plain text in `.qgs` data source strings | **Credentials live only in the OS keychain** (`keyring`). Project files and QSettings store an opaque ID. Secrets are redacted from logs and error dialogs. |
| QGIS Server stored XSS from unescaped attribute values in HTML output (CVE-2026-104140) | **Data-derived text is never interpreted as markup.** Use plain-text widgets, or `html.escape` where rich text is unavoidable. A test injects markup through every data path that reaches the UI. |
| Critical GitHub Actions workflow issue (CVE-2026-24480) | **CI hardening:** actions pinned by commit SHA, least-privilege `GITHUB_TOKEN`, no `pull_request_target` checkout of untrusted code, no secrets in fork builds. |
| Most scanner findings come from dependencies | **Supply chain:** `uv.lock` with hashes, `pip-audit` in CI, automated dependency updates, GDAL/PROJ versions recorded in the About dialog. |
| Projects can silently fetch network resources | **Network only when the user asks.** HTTPS by default with certificate verification, timeouts and response-size limits. GDAL `/vsicurl/`-style sources in a project file need confirmation before opening. No telemetry. |

Also carried over: project paths are resolved and checked, never interpreted. Readers fail with
`DataReadError` rather than crashing on malformed input.

## Quality gates (every milestone)

A milestone is done only when all of these hold:

1. ruff (with the `S` bandit rules from P2.0 on) and pyright strict on `core/` and `io/` are clean.
2. Tests cover the new code. Coverage of `core/` and `io/` stays at 90% or above.
3. New readers and parsers have property-based tests (hypothesis) on malformed input.
4. New hot paths have a timing test with an explicit budget. Startup stays at about 0.35 s or less.
5. The security checklist above has been walked through for the new features.
6. `tests/test_definition_of_done.py` passes, and the offscreen screenshot has been reviewed.
7. CI is green on Windows, macOS and Linux.
8. ROADMAP.md is updated, and the change is staged with a proposed commit message (the
   maintainer commits).

## Execution order

The order follows the stages in [product-plan.md](product-plan.md) (approved 2026-10-27), which
replace the earlier sequence. Several milestones are split: the "lite" part ships in the MVP and
the rest in Beta. The milestone sections below describe each milestone in full; the split is
recorded here.

| Order | Stage | Milestone | Size |
|---|---|---|---|
| 1 | Foundation | P2.0 Security and quality foundation | M |
| 2 | MVP | P2.1 Layer registry, CRS and data import robustness | M |
| 3 | MVP | P2.2 lite: expression engine, categorized and graduated styles, polygon fill, labels for any layer, legend, well status symbols | M |
| 4 | MVP | P2.6a Wells in depth: trajectories and tops | L |
| 5 | MVP | Correlation panel (new, see product-plan.md) | L |
| 6 | MVP | P2.5 lite: registry, background runs, history, the algorithms the MVP workflow needs | M |
| 7 | MVP | P2.6b Subsurface analysis: gridding, contours, volumetrics, cross-sections | XL |
| 8 | MVP | P2.4 lite: GeoPackage layers, digitizing with snapping, undo/redo | M |
| 9 | MVP | P2.7 lite: XYZ basemaps | S |
| 10 | MVP | P2.8 lite: single-map figure export | S |
| 11 | MVP | Packaging and user docs (new, see product-plan.md) | M |
| 12 | Beta | P2.2 rest: rule-based styles, hillshade, `.pgstyle` | M |
| 13 | Beta | P2.3 Navigation and selection | S |
| 14 | Beta | P2.4 rest: field calculator, joins, Save As / export | M |
| 15 | Beta | P2.5 rest: full algorithm library, batch, raster calculator | L |
| 16 | Beta | P2.7 rest: WMTS/WMS, WFS, keychain credentials | M |
| 17 | Beta | P2.8 rest: print layout editor | M |

Beta also adds petrophysics quick-look, production data, a well database, the georeferencer and
localization. Production 1.0 and later stages are defined in product-plan.md.

**Tooling note (P2.0):** pyright strict and pip-audit run in CI only, not in the local dev
group, because their download stalls locally.

## Milestones

### P2.0 Security and quality foundation

**Status:** all work done. A first CI run on GitHub, and fixing pyright's strict findings before
the `types` job becomes blocking, are still open. See ROADMAP.md.

- `SECURITY.md`: threat model, supported versions, how to report a vulnerability.
- Fix the log viewer header, which renders the LAS `WELL` name and file name as unescaped rich text.
  Add an injection test covering every UI path that displays data-derived text.
- Enable ruff `S` rules. Add pyright strict on `core/` and `io/`, and a coverage gate.
- Add hypothesis tests for the LAS, CSV/Excel well-header and `.pgis` readers.
- GitHub Actions CI: lint, type check, tests on Windows, macOS and Linux, and `pip-audit`, with
  actions pinned by SHA and least-privilege permissions.
- Autosave to a sidecar file, and keep the previous `.pgis` as a backup on save. Offer recovery
  after a crash.
- **Done when:** every gate above is enforced in CI, and the injection test fails on the old code
  and passes on the new.

### P2.1 Layer registry, CRS and data import robustness

- The layer-type registry from [phase2.md §0](phase2.md#0-prerequisite-a-layer-type-registry),
  replacing the `isinstance` dispatch in the canvas, project IO and style dialogs.
- Searchable CRS picker over the PROJ database. Change the project CRS with layers loaded (re-read
  sources in a worker, swap atomically). Explicit datum transformation choice, with a warning when
  the best transformation needs a grid that isn't installed.
- CRS prompt for rasters with none (`WarpedVRT(src_crs=...)`), saved in the project.
- Delimited-text layers with XY columns. Decimal-comma and `;`-separated CSV (common in Azerbaijani
  locales) for both delimited text and well headers. Honour shapefile `.cpg` encodings.
- Repair-broken-paths dialog when opening a project. Place new rasters below vector and well layers.
- **Done when:** a project can be switched from UTM 39N to Pulkovo 1942 / Gauss-Krüger and back
  with identical geometry within tolerance, and decimal-comma files import correctly.

### P2.2 Expressions and styling

- Expression engine (Qt-free, in `core/expressions/`): tokenizer, parser to an AST, evaluator over
  pandas columns (vectorised where possible), and a whitelisted function library (math, string,
  conditional, geometry area/length). Limits on size, depth and steps. No Python evaluation.
- Renderers: single, categorized, graduated (equal interval, quantile, natural breaks) and
  rule-based (expression filters). Polygon fill with opacity.
- Labels for any vector layer (field or expression), reusing the screen-space level-of-detail
  placement from `render/labels.py`.
- Legend panel. Raster hillshade and contrast options. Well symbols by status.
- Styles saved as JSON (`.pgstyle`), versioned, containing no code.
- **Done when:** expression tests cover the grammar with fuzzing, and a 100k-feature graduated
  layer renders within budget.

### P2.6a Wells in depth: trajectories and tops

Design in [phase2.md §1–2](phase2.md#1-deviation-surveys-and-trajectories-minimum-curvature).

- Minimum-curvature trajectories, with survey import using the detect-then-map flow.
- Well paths on the map, and an MD / TVD / TVDSS selector in the log viewer.
- Formation tops: import, display in log tracks, and a tops point layer on the map.
- Log viewer backlog: per-track scale editor, GR sand shading, neutron–density crossover shading,
  decimation for very long logs, and optional ft↔m conversion.
- `.pgis` version 2, still reading version 1.

### P2.3 Navigation and selection

- Scale bar, scale box (set and lock scale), spatial bookmarks, ellipsoidal distance and area
  measuring, and an overview map.
- Selection by rectangle, polygon or expression, kept in sync with the attribute table and the
  canvas overlay. Help → Load Demo Data.

### P2.4 Editing and writing data

- Create GeoPackage layers. Digitize points, lines and polygons with vertex editing and snapping.
  Undo/redo through a command stack.
- Field calculator using the expression engine. Attribute editing in the table.
- Table joins, for example a production CSV joined to wells.
- Save As / export to GeoPackage, Shapefile, GeoJSON and CSV with a CRS choice. Edits are written
  atomically, and a failed write never corrupts the source.

### P2.5 Processing framework

- `analysis/` algorithm registry with typed parameters and generated parameter dialogs. Runs in the
  background with progress and cancel. History panel and batch mode.
- Core algorithms: buffer, clip, intersect, dissolve, reproject, spatial join, points in polygon,
  sample rasters at wells, zonal statistics, raster calculator, raster clip and reproject, and a
  histogram view.
- Results are written to files and loaded back as layers, so projects keep storing references only.

### P2.6b Subsurface analysis

Design in [phase2.md §3–5](phase2.md#3-gridding-kriging-and-contours).

- Gridding (IDW, minimum-curvature splines, kriging) and contours as processing algorithms.
- Volumetrics: GRV, STOIIP and GIIP, with Monte Carlo reported in the P90-is-low convention.
- Cross-sections, with a drawn line, surface profiles, projected wells, logs and tops.

### P2.7 Basemaps and OGC services

- XYZ, WMTS and WMS basemaps, and WFS read-only, with a connection manager.
- Credentials in the OS keychain, HTTPS with certificate verification, timeouts, response-size
  limits and a tile cache with a size cap.
- Respect each provider's terms, including attribution on the map and in exports, and no bulk
  download.

### P2.8 Print and export layouts

- Layout dialog: map frame, legend, scale bar, north arrow, title, coordinate grid and text, with
  export to PNG, PDF and SVG at a chosen DPI.
- Layouts are saved in the project as data, never code.

## Phase 1 backlog: where each item goes

| Backlog item | Milestone |
|---|---|
| Raster sources with no CRS | P2.1 |
| Change the project CRS with layers loaded | P2.1 |
| New layers always go on top | P2.1 |
| Log viewer scales, shading and decimation | P2.6a |
| Depth unit conversion | P2.6a |
| Well symbols by status | P2.2 |
| Polygon fill | P2.2 |
| Help → Load Demo Data | P2.3 |

## Decisions still needed

Decide each one before the milestone that needs it.

1. **Expression syntax (P2.2):** a QGIS-compatible subset (familiar to QGIS users, and existing
   expressions copy over) or a simpler Python-like syntax. Recommendation: a QGIS-compatible
   subset.
2. **Type checker (P2.0):** pyright (fast, strict mode) or mypy. Recommendation: pyright.
3. **Kriging library (P2.6b):** gstools or pykrige.
4. **3D backend (later):** pyqtgraph.opengl or pyvista/VTK.
5. **SEG-Y reader (later):** segyio.
6. **Contouring (P2.6b):** contourpy (recommended).
7. **Basemap providers (P2.7):** which providers to ship as presets. Each must allow use in a
   desktop app and needs attribution handled.

## Sources

- [QGIS User Guide 3.44, contents](https://docs.qgis.org/latest/en/docs/user_manual/index.html)
- [QGIS configuration, project macros setting](https://docs.qgis.org/3.44/en/docs/user_manual/introduction/qgis_configuration.html)
- [QGIS security page](https://qgis.org/resources/support/security)
- [QGIS bug #9523](https://issues.qgis.org/issues/9523) and [#6406](https://issues.qgis.org/issues/6406): macro prompt behaviour
- [QGIS feature request #8180](https://issues.qgis.org/issues/8180): plain-text passwords in project files
- [QGIS authentication system overview](https://docs.qgis.org/4.2/es/docs/user_manual/auth_system/auth_overview.html)
- [CVE-2026-104140 (Debian security tracker)](https://security-tracker.debian.org/tracker/CVE-2026-104140)
- [QGIS CVE list](https://cve.imfht.com/vendor/QGIS?lang=en)
