# Roadmap

## Phase 1 — Core desktop GIS with wells and logs ✅

- [x] M0 — Scaffold: uv/pyproject, package skeleton, app shell window, logging + error dialogs,
      offscreen screenshot script, architecture/startup tests, CLAUDE.md, ROADMAP.md
- [x] M1 — Sample data generator: field boundary (GeoJSON), ~10 wells (CSV/XLSX), synthetic LAS,
      anticline structure-top GeoTIFF, all in UTM 39N
- [x] M2 — Core + IO: Project, layers, styles, CRS reprojection, NaN-path geometry, vector readers
      (SHP/GeoJSON/GPKG/KML), downsampled raster reads
- [x] M3 — Map canvas: pan, wheel/box zoom, zoom to layer/extent, cursor coords; layer panel
      (visibility, reorder, rename, remove); vector + raster rendering and style dialogs; background loading
- [x] M4 — Wells: CSV/Excel header import, column auto-detection, mapping dialog, symbols + LOD labels
- [x] M5 — LAS logs: batch import, name matching, mnemonic aliases, log viewer dock
      (GR | resistivity log-scale | NPHI+RHOB, shared inverted depth)
- [x] M6 — Identify tool + attribute table dock
- [x] M7 — Project save/open (.pgis) + recent projects
- [x] M8 — Hardening: error paths, clean-checkout test run, definition-of-done walkthrough
- [x] M9 — Docs polish, Phase 2 notes

## Stages after Phase 1

The stage order was approved on 2026-10-27. Scope, exit criteria and the capability matrix are in
[docs/product-plan.md](docs/product-plan.md). Milestone specs, the security baseline and the
quality gates are in [docs/phase2-plan.md](docs/phase2-plan.md), and subsurface designs in
[docs/phase2.md](docs/phase2.md). Items are listed in execution order. "Lite" means the MVP part
of a milestone; the rest of it is listed under Beta.

### Stage 0 — Foundation (finishing)

- [ ] P2.0 — Security and quality foundation (all work done; waiting on a first CI run on GitHub)
  - [x] Rich-text injection fixed everywhere data text reaches the UI, with an injection test
  - [x] Autosave, `.pgis.bak` backup on save, crash recovery
  - [x] Reader hardening with hypothesis tests (found and fixed a ragged-CSV crash)
  - [x] ruff `S` rules, coverage gate (≥90% on core/io; currently 96%), SECURITY.md
  - [x] CI workflow: lint, tests on Windows/macOS/Linux, speed budgets, pip-audit; SHA-pinned
        actions, read-only token
  - [ ] First green CI run on GitHub
  - [ ] Fix pyright strict findings on core/io, then make the `types` job blocking

### Stage 1 — MVP: a complete field study, installable

Exit: the 12-step MVP workflow test passes in CI and against installed builds on all three
platforms; 5 target users complete the tutorial; budgets hold at 500 wells, a 1 GB raster and
100k features.

- [x] P2.1 — Layer registry; CRS picker, change CRS with layers loaded, datum transformation choice,
      raster CRS prompt; delimited text with XY columns, decimal-comma CSV, `.cpg` encodings;
      broken-path repair; rasters placed below vector layers
  - Follow-ups: encoding override has no UI yet (project file/API only); explicit-transformation
    rasters use nearest-neighbour sampling; Soviet/Russian GK tables (X = northing) need X/Y
    swapped by hand in the mapping dialog
- [x] P2.2 lite — Safe expression engine (no Python eval); categorized and graduated styles;
      polygon fill; labels for any layer; legend; well status symbols
  - Follow-ups: per-class visibility toggles in the legend; graduated symbol size; `$area` and
    `$length` are planar in the project CRS (QGIS defaults to ellipsoidal); no date functions or
    regular expressions in expressions yet (regexes need a ReDoS-safe engine first)
- [x] P2.6a — Wells in depth: minimum-curvature trajectories, MD/TVD/TVDSS, formation tops, log
      track templates, fills and shading, decimation, depth unit conversion, `.pgis` v2
  - Follow-ups: log track templates (user-defined tracks and curves) move to the correlation
    panel milestone; survey azimuths are taken as grid north (no convergence or magnetic
    declination correction); MD↔TVD is interpolated linearly between stations; surveys can't be
    imported from LAS or Excel workbooks with several sheets yet
- [x] Correlation panel — well-to-well section with tops picking, flattening on a top, track
      templates, well selection from map or table
  - Follow-ups: saving templates to their own files for reuse across projects; horizontal
    spacing by distance between wells; picking on a log track's curve (snap to inflection);
    sections across several well layers
- [x] P2.5 lite — Processing registry, background runs with cancel, history; sample raster at
      wells, clip, reproject, zonal statistics
  - Follow-ups: batch mode and the rest of the algorithm library (P2.5 rest); raster sampling
    is nearest-cell (no interpolation); zonal statistics reads each zone's window at full
    resolution, so very large zones on huge rasters are slow; vector algorithms cancel only
    between steps (clip checks every 5,000 features)
- [ ] P2.6b — Gridding (IDW, splines, kriging), contours, volumetrics with Monte Carlo,
      cross-sections
- [ ] P2.4 lite — GeoPackage layers, digitizing lines and polygons with snapping, undo/redo
- [ ] P2.7 lite — XYZ basemaps with safe network defaults
- [ ] P2.8 lite — Single-map figure export (PNG/PDF/SVG) with legend, scale bar, north arrow, title
- [ ] Packaging — installers for Windows, macOS and Linux with GDAL/PROJ data, tested offline
- [ ] User docs — getting-started guide and MVP-workflow tutorial on the demo field

### Stage 2 — Beta: feature-complete core parity

Exit: Beta rows of the capability matrix done; 4-week beta with 20+ users; 99.5% crash-free
sessions; budgets hold at 5,000 wells.

- [ ] P2.2 rest — Rule-based styles, hillshade, `.pgstyle`
- [ ] P2.3 — Scale bar, scale box, bookmarks, measuring, overview map, selection tools;
      Help → Load Demo Data
- [ ] P2.4 rest — Field calculator, joins, Save As / export
- [ ] P2.5 rest — Full vector and raster algorithm library, batch runs, raster calculator
- [ ] P2.7 rest — WMTS/WMS, WFS read, keychain credentials
- [ ] P2.8 rest — Full print layout editor
- [ ] Petrophysics quick-look — Vsh, porosity, Archie Sw, net pay with cut-offs
- [ ] Production data — import, join to wells, bubble maps, time slider
- [ ] Well database — GeoPackage project store for headers, surveys, tops, log references
- [ ] Georeferencer for scanned field maps
- [ ] Localization — English, Azerbaijani, Russian

### Stage 3 — Production 1.0

Exit: two clean release candidates; signed installers verified on clean machines; every past
`.pgis` version opens; docs cover every feature; security and dependency gates green.

- [ ] Release engineering — semver, changelog, reproducible builds, patch policy
- [ ] Signed Windows installer, notarized macOS app, Linux AppImage or Flatpak
- [ ] Secure, opt-in auto-update with signed manifests
- [ ] Security process — disclosure policy, SBOM per release, dependency audit gate, advisories
- [ ] Opt-in crash reporting with redaction; soak tests; `.pgis` compatibility suite
- [ ] Python plugin API with explicit trust
- [ ] User manual, tutorials, plugin API reference, troubleshooting guide
- [ ] Accessibility — keyboard access, screen-reader labels, colour-blind-safe palettes
- [ ] License chosen; LGPL (Qt/PySide6) compliance; third-party notices

### Stage 4 — After 1.0

- [ ] 3D view
- [ ] SEG-Y and seismic sections
- [ ] Multi-user PostGIS project store
- [ ] Industry data integrations (OSDU, Petrel/Techlog exchange)

The Phase 1 backlog is folded into these stages (see the table in docs/phase2-plan.md).

Out of scope: mesh data, vector tiles, point clouds, 3D tiles, GRASS, GPS, QGIS Server, atlas and
reports, model designer, project-embedded Python.
