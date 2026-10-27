# Roadmap

## Phase 1 — Core desktop GIS with wells and logs

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
- [ ] M7 — Project save/open (.pgis) + recent projects
- [ ] M8 — Hardening: error paths, clean-checkout test run, definition-of-done walkthrough
- [ ] M9 — Docs polish, Phase 2 notes

## Phase 2+ — Planned (design for, not built yet)

- [ ] Deviation surveys and trajectories (minimum curvature)
- [ ] Formation tops
- [ ] Gridding / kriging, contours
- [ ] Volumetrics: GRV, STOIIP, Monte Carlo
- [ ] Cross-sections
- [ ] 3D view
- [ ] SEG-Y
- [ ] Python plugin API
- [ ] Packaging (PyInstaller or Briefcase) for Windows/macOS/Linux
