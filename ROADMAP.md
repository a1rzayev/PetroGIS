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

## Phase 1 backlog (known limitations)

- [ ] Raster sources with no CRS: prompt for one, as vectors already do (currently an error dialog)
- [ ] Change the project CRS with layers loaded (re-read and reproject every source)
- [ ] New layers always go on top. Consider placing rasters below vector and well layers
- [ ] Log viewer: per-track scale editor, GR sand shading and neutron–density crossover shading,
      decimation for very long logs
- [ ] Depths in feet are shown in feet. Add an optional unit conversion
- [ ] Well symbols by status (oil / gas / water / dry)
- [ ] Polygon fill (Phase 1 draws outlines only, as specified)
- [ ] Help → Load Demo Data (generate and load the synthetic field in one click)

## Phase 2+ — Planned (design notes in docs/phase2.md)

- [ ] Layer-type registry (prerequisite for new layer kinds and the plugin API)
- [ ] Deviation surveys and trajectories (minimum curvature)
- [ ] Formation tops
- [ ] Gridding / kriging, contours
- [ ] Volumetrics: GRV, STOIIP, Monte Carlo
- [ ] Cross-sections
- [ ] 3D view
- [ ] SEG-Y
- [ ] Python plugin API
- [ ] Packaging (PyInstaller or Briefcase) for Windows/macOS/Linux
