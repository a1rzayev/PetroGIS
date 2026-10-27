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

## Phase 2 — QGIS-grade core, built safely, interleaved with subsurface work

Scope: core QGIS parity (decided 2026-10-27). The full plan, with the QGIS feature mapping,
security baseline and per-milestone quality gates, is in [docs/phase2-plan.md](docs/phase2-plan.md).
Subsurface designs are in [docs/phase2.md](docs/phase2.md). Milestones are listed in execution order.

- [ ] P2.0 — Security and quality foundation: SECURITY.md threat model, fix rich-text injection in
      the log viewer header, ruff `S` rules, pyright strict on core/io, coverage gate, hypothesis
      tests for readers, CI on Windows/macOS/Linux (SHA-pinned actions, pip-audit), autosave and backup
- [ ] P2.1 — Layer registry; CRS picker, change CRS with layers loaded, datum transformation choice,
      raster CRS prompt; delimited text with XY columns, decimal-comma CSV, `.cpg` encodings;
      broken-path repair; rasters placed below vector layers
- [ ] P2.2 — Safe expression engine (no Python eval); categorized, graduated and rule-based
      styles; polygon fill; labels for any layer; legend; hillshade; `.pgstyle`; well status symbols
- [ ] P2.6a — Wells in depth: minimum-curvature trajectories, MD/TVD/TVDSS in the log viewer,
      formation tops, log viewer scales, shading and decimation, depth unit conversion, `.pgis` v2
- [ ] P2.3 — Navigation and selection: scale bar, scale box, bookmarks, measuring, overview map,
      selection tools; Help → Load Demo Data
- [ ] P2.4 — Editing and writing data: GeoPackage layers, digitizing with snapping, undo/redo,
      field calculator, joins, Save As / export
- [ ] P2.5 — Processing framework: algorithm registry, background runs with cancel, history,
      batch; core vector and raster algorithms
- [ ] P2.6b — Subsurface analysis: gridding (IDW, splines, kriging), contours, volumetrics with
      Monte Carlo, cross-sections
- [ ] P2.7 — Basemaps and OGC services: XYZ/WMTS/WMS, WFS read; keychain credentials, safe
      network defaults
- [ ] P2.8 — Print and export layouts: PNG/PDF/SVG with legend, scale bar, north arrow, grid

The Phase 1 backlog is folded into these milestones (see the table in docs/phase2-plan.md).

## Later

- [ ] Python plugin API with explicit trust
- [ ] Georeferencer for scanned field maps
- [ ] 3D view
- [ ] SEG-Y
- [ ] Packaging (PyInstaller or Briefcase) for Windows/macOS/Linux

Out of scope: mesh data, vector tiles, point clouds, 3D tiles, GRASS, GPS, QGIS Server, atlas and
reports, model designer, project-embedded Python.
