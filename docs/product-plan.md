# PetroGIS product plan: from zero to MVP to production

This plan takes PetroGIS from where it is now to a production 1.0. It is based on a survey of
the main general GIS products and the main petroleum subsurface tools. It sits above the other
planning documents:

- [phase2-plan.md](phase2-plan.md): detailed specs, security baseline and quality gates for the
  P2.x milestones. This plan reuses those milestones and schedules them into stages.
- [phase2.md](phase2.md): designs for the subsurface features.
- [ROADMAP.md](../ROADMAP.md): the checklist.

**Status: approved (2026-10-27).** This stage order replaces the earlier P2.x execution order.
Installers, a correlation panel and the full subsurface workflow move forward into the MVP.
Styling, editing, basemaps and export ship as "lite" versions in the MVP, and the rest moves to
Beta. ROADMAP.md and phase2-plan.md follow this order.

## 1. What the survey shows

### Products surveyed

| Category | Products | What they are best at |
|---|---|---|
| General desktop GIS | QGIS, ArcGIS Pro, gvSIG | Broad data support, styling, editing, geoprocessing, layouts. ArcGIS Pro adds linked 2D/3D views, ModelBuilder and ArcPy scripting. |
| Analysis-focused GIS | GRASS GIS, SAGA GIS | Raster and terrain analysis, hydrology, lidar. SAGA started as a terrain-analysis tool. |
| Terrain / data utility | Global Mapper | 300+ formats, terrain (contours, viewsheds, cut/fill), lidar, fast reprojection. |
| Well and log mapping | S&P Global Petra, Kingdom | Basin-scale well databases, guided tops picking, well-to-well cross-sections with flattening, log fills and cut-offs, production data, contour maps. |
| Subsurface platforms | SLB Petrel, Techlog | Integrated correlation canvas (logs, core, seismic, grids), geomodelling, simulation links. Techlog: full petrophysics (lithology, porosity, saturation, permeability), facies, high-angle wells. |
| Seismic | OpendTect | 2D/3D/4D seismic interpretation, horizon tracking, attributes, plugin architecture, GPL core. |

### The gap PetroGIS fills

- General GIS tools are strong on maps but weak on wells, logs, tops and depth. A QGIS user
  needs plugins and workarounds to pick a top or draw a log track.
- Petroleum tools are strong on wells but expensive, Windows-centric and heavy. Their mapping is
  basic compared with a real GIS.
- There is no lightweight, open, cross-platform tool that does both well for the people in
  between: students, consultants, small operators and national-company teams doing field studies.

**Positioning:** an open, fast, secure desktop GIS that carries a petroleum field study end to
end. That means maps, wells, logs, tops, correlation, structure maps, volumetrics and report
figures, with QGIS-grade mapping and Petra-style well workflows, and none of the 3D, seismic and
simulation weight of Petrel. The first region is the Caspian / Absheron, which means
Pulkovo 1942 datums, Azerbaijani locales (decimal commas) and the az/ru/en languages.

### Capability matrix

✅ strong · ◐ partial or basic · — absent. "Target" is the stage where PetroGIS gets it.

| Capability | QGIS | ArcGIS Pro | GRASS / SAGA | Global Mapper | Petra / Kingdom | Petrel / Techlog | PetroGIS now | Target |
|---|---|---|---|---|---|---|---|---|
| Vector / raster display, reprojection | ✅ | ✅ | ✅ | ✅ | ◐ | ◐ | ✅ | done |
| CRS tools, datum transformations | ✅ | ✅ | ✅ | ✅ | ◐ | ◐ | ◐ | MVP |
| Styling, labels, legend | ✅ | ✅ | ◐ | ◐ | ◐ | ◐ | ◐ | MVP (core), Beta (rule-based) |
| Attribute table, identify | ✅ | ✅ | ◐ | ✅ | ✅ | ◐ | ✅ | done |
| Editing / digitizing | ✅ | ✅ | ✅ | ✅ | ◐ | ◐ | — | MVP (basic), Beta (full) |
| Geoprocessing framework | ✅ | ✅ | ✅ | ◐ | — | ◐ | — | MVP (minimal), Beta (full) |
| Basemaps, OGC services | ✅ | ✅ | ◐ | ✅ | ◐ | — | — | MVP (XYZ), Beta (WMS/WFS) |
| Print / export layouts | ✅ | ✅ | ◐ | ◐ | ✅ | ✅ | — | MVP (figure export), Beta (layouts) |
| Well headers, symbols | ◐ (plugins) | ◐ | — | ◐ | ✅ | ✅ | ✅ | done |
| LAS logs, log display | — | — | — | ◐ | ✅ | ✅ | ✅ | done (MVP adds templates and fills) |
| Deviated wells (trajectories) | — | ◐ | — | ◐ | ✅ | ✅ | — | MVP |
| Formation tops, picking | — | — | — | — | ✅ | ✅ | — | MVP |
| Well-to-well correlation, flattening | — | — | — | — | ✅ | ✅ | — | MVP |
| Gridding and contour maps | ◐ | ✅ | ✅ | ✅ | ✅ | ✅ | — | MVP |
| Volumetrics (GRV, STOIIP, Monte Carlo) | — | — | — | ◐ (cut/fill) | ◐ | ✅ | — | MVP |
| Petrophysics quick-look (Vsh, φ, Sw) | — | — | — | — | ◐ | ✅ | — | Beta |
| Production data on maps | ◐ | ◐ | — | — | ✅ | ◐ | — | Beta |
| Cross-sections through surfaces | ◐ (profile) | ✅ | ◐ | ✅ | ✅ | ✅ | — | MVP |
| 3D view | ✅ | ✅ | ◐ | ✅ | ◐ | ✅ | — | after 1.0 |
| Seismic (SEG-Y) | — | ◐ | — | ◐ | ◐ | ✅ | — | after 1.0 |
| Scripting / plugins | ✅ | ✅ | ✅ | ◐ | ◐ | ✅ | — | 1.0 (with trust model) |
| Installers, auto-update | ✅ | ✅ | ◐ | ✅ | ✅ | ✅ | — | MVP (installers), 1.0 (update) |

Mesh, vector tiles, point clouds, 3D tiles, GRASS integration, GPS, a map server, atlases and a
model designer stay out of scope (see phase2-plan.md).

## 2. Architecture: the model–view–controller mapping

PetroGIS already separates model, view and controller. The plan keeps that separation and makes
it explicit for new features:

| Role | Where | Rule |
|---|---|---|
| **Model** | `core/` (project, layers, wells, logs, styles, expressions, algorithms in `analysis/`) | Pure Python, no Qt, fully unit-tested. The single source of truth. |
| **Data access** | `io/` | Readers and writers. They raise `DataReadError` and never execute code from files. |
| **View** | `render/` (map canvas behind a renderer protocol), `ui/` widgets and dialogs | They display model state and send user intent. They hold no business logic. |
| **Controller** | `ui/project_bridge.py`, `ui/map_controller.py`, `ui/workers.py`, per-feature controllers | Turn model events into view updates and user actions into model calls. They run work in the background and drop stale results. |

New features follow the same shape: a model in `core/`, a reader in `io/`, a view in `ui/` or
`render/`, and a small controller. Phase 2's layer-type registry (P2.1) is what lets new layer
kinds plug into all three without editing central `isinstance` chains.

## 3. Stages

Each stage has a goal, a scope, and measurable exit criteria. Every milestone inside a stage
still passes the quality gates and security baseline in phase2-plan.md.

### Stage 0: Foundation (Phase 1 done, P2.0 in progress)

**Goal:** a working, tested core that proves the architecture.

- **Done (Phase 1, M0–M9):** map canvas, vector/raster/well layers, LAS logs and log viewer,
  identify, attribute table, `.pgis` projects, definition-of-done test, about 0.35 s startup.
- **In progress (P2.0):** security and quality foundation.
  - Done: rich-text injection fixes with tests; autosave, `.bak` backups and crash recovery.
  - Remaining: reader hardening with hypothesis tests, ruff `S` rules, SECURITY.md, CI.
  - Pyright and pip-audit are deferred: their local download stalls, and removing them from the
    dev group is waiting on a `uv sync` still running in your terminal.

**Exit criteria:** P2.0 gates enforced in CI, and the injection test fails on the old code and
passes on the new.

### Stage 1: MVP (a complete field study, installable)

**Goal:** a petroleum engineering student or junior geologist can install PetroGIS and carry a
small field study from raw files to a report figure without leaving the app.

**The MVP workflow** (this becomes the new end-to-end acceptance test, replacing the Phase 1
definition-of-done test):

1. Install from a native installer and open the app.
2. Create a project in the right CRS, with Pulkovo 1942 ↔ WGS 84 handled explicitly.
3. Load the field boundary, a structure grid and a basemap.
4. Import well headers, deviation surveys and LAS logs, including decimal-comma CSV files.
5. QC the logs in templated tracks with GR and density–neutron fills.
6. Pick formation tops in a well-to-well correlation panel, flattened on a marker.
7. Grid a top surface from the picks and draw contours.
8. Digitize a contact or fault polygon if needed.
9. Compute GRV and STOIIP with a Monte Carlo P90/P50/P10.
10. Draw a cross-section through the wells and surfaces.
11. Export a structure map figure with legend, scale bar and north arrow.
12. Save, reopen and get identical state.

**Scope:** existing P2.x milestones, re-sequenced, plus three additions marked ★.

| Order | Milestone | MVP scope |
|---|---|---|
| 1 | P2.0 Security and quality foundation | Full (finish) |
| 2 | P2.1 Layer registry, CRS, import robustness | Full |
| 3 | P2.2 Expressions and styling | **Core only:** expression engine, categorized and graduated styles, polygon fill, labels for any layer, legend. Rule-based styles and `.pgstyle` move to Beta. |
| 4 | P2.6a Wells in depth | Full: trajectories, MD/TVD/TVDSS, tops, log track templates, fills and shading |
| 5 | ★ Correlation panel | Well-to-well section with picking, flattening on a top, track templates, well selection from the map or attribute table (the core of Petra's and Kingdom's correlation modules) |
| 6 | P2.5 Processing framework | **Minimal:** registry, background runs with cancel, history; algorithms needed by the workflow (sample raster at wells, clip, reproject, zonal statistics) |
| 7 | P2.6b Subsurface analysis | Full: gridding, contours, volumetrics with Monte Carlo, cross-sections |
| 8 | P2.4 Editing (lite) | Create GeoPackage layers; digitize polygons and lines with snapping; undo/redo. Joins, field calculator and full export move to Beta. |
| 9 | P2.7 Basemaps (lite) | XYZ tile basemaps only, with the network safety rules; WMS/WMTS/WFS move to Beta |
| 10 | P2.8 Export (lite) | Single-map figure export (PNG/PDF/SVG) with legend, scale bar, north arrow and title. Full layout editor moves to Beta. |
| 11 | ★ Packaging | Installers for Windows, macOS and Linux (PyInstaller or Briefcase, decision needed), bundling GDAL/PROJ data; tested offline with the demo project |
| 12 | ★ User documentation | Getting-started guide and an MVP-workflow tutorial using the demo field |

**Exit criteria:**

- The MVP workflow test passes in CI on all three platforms, and against the installed builds.
- Five target users (students or junior geologists) complete the workflow from the tutorial,
  with no blocker bugs.
- Performance budgets hold on a realistic dataset: 500 wells with logs, a 1 GB raster, and
  100k vector features. The UI never freezes for more than 100 ms during loads.
- No open high-severity security findings. Every gate in phase2-plan.md is green.

### Stage 2: Beta (feature-complete core parity)

**Goal:** daily-driver quality for field studies, so users no longer need QGIS or Petra for the
common tasks.

| Area | Scope |
|---|---|
| Rest of P2.2, P2.3, P2.4, P2.5, P2.7, P2.8 | Rule-based styles and `.pgstyle`. Navigation and selection tools. Full editing with joins, field calculator and export. Full processing library and batch runs. WMS/WMTS/WFS with keychain credentials. Full print layout editor. |
| ★ Petrophysics quick-look | Vsh from GR, porosity from density/neutron, Archie Sw, net pay with cut-offs. These are processing algorithms that write new LAS curves, with results shown in the log viewer and summarised per zone (the essentials of Techlog Quanti). |
| ★ Production data | Import monthly production tables, join them to wells, bubble maps and time sliders on the map |
| ★ Data management | A project well database (GeoPackage) holding headers, surveys, tops and log references, so 5,000+ wells stay fast and queryable |
| Georeferencer | For scanned legacy field maps, which are common in old Caspian fields |
| Localization | Interface in English, Azerbaijani and Russian |

**Exit criteria:**

- Every QGIS-parity row in the capability matrix marked for Beta is done.
- A 4-week beta with at least 20 users and a public issue tracker. Crash-free sessions reach
  99.5% or better, measured by opt-in crash reports.
- Performance budgets hold at 5,000 wells.

### Stage 3: Production 1.0 (dependable, supportable, safe to ship)

**Goal:** a release people can rely on for real work, with a process that keeps it that way.

| Area | Scope |
|---|---|
| Release engineering | Semantic versioning, changelog, reproducible builds from `uv.lock`, release branches, a 1.0.x patch policy |
| Distribution | Code-signed Windows installer, notarized macOS app, Linux AppImage or Flatpak |
| Secure auto-update | Signed update manifests checked before install; opt-in update checks; no other network calls without consent |
| Security process | SECURITY.md disclosure policy, supported versions, an SBOM per release, dependency audit gate (pip-audit or `uv` audit, once the download works), a security advisory process |
| Reliability | Opt-in crash reporting with local redaction; long-running soak tests; file-format compatibility tests for every past `.pgis` version |
| Plugin API | Entry-point plugins against a small stable facade with explicit user trust; no code from project files (phase2.md §8) |
| Documentation | User manual, tutorials, an API reference for plugins, a troubleshooting guide |
| Accessibility | Keyboard navigation for every action, screen-reader labels, colour-blind-safe default palettes |
| Licensing and compliance | Pick the project license (decision needed). Meet the LGPL obligations for bundled Qt/PySide6. Ship third-party notices. Respect basemap provider terms. |

**Exit criteria:**

- Two consecutive release candidates with no high-severity bugs.
- Installers are signed and verified on clean machines.
- Every past `.pgis` version opens.
- Documentation covers every user-facing feature.
- The security and dependency gates are green.

### Stage 4: After 1.0

- **3D view:** surfaces, trajectories and tops (backend decision needed: pyqtgraph.opengl or
  pyvista/VTK).
- **Seismic:** SEG-Y loading, survey outlines on the map, inline and crossline sections (segyio).
  OpendTect shows the scope that's possible.
- **Multi-user:** a PostGIS-backed shared project store with roles, for teams.
- **Integrations:** OSDU or other industry data platforms, and Petrel/Techlog exchange formats.

## 4. Cross-cutting rules (all stages)

- **Quality and security gates:** phase2-plan.md §§ "Security baseline" and "Quality gates" apply
  to every milestone in every stage.
- **One end-to-end acceptance test per stage:** the definition-of-done test (Phase 1), then the
  MVP workflow test, then the Beta and 1.0 suites. Each runs in CI against source and against
  the installed builds.
- **Performance budgets grow with the stages:** 10 wells (Phase 1), 500 (MVP), 5,000 (Beta and
  1.0). Every hot path has a timing test.
- **Backwards compatibility:** from the MVP on, every `.pgis` version must keep opening. Format
  changes bump `PROJECT_VERSION` and add a migration test.

## 5. Risks

| Risk | Mitigation |
|---|---|
| Scope creep toward "all of QGIS" or "all of Petrel" | The capability matrix fixes the scope per stage. Anything else needs a change to this document. |
| Solo-developer bandwidth | The MVP is defined by one workflow, not by a feature count. Beta items can slip without blocking the MVP. |
| Packaging GDAL/PROJ/Qt across three platforms | Start packaging in the MVP rather than at the end, and test the installed builds in CI. |
| Slow or blocked package downloads (seen now with pyright) | Commit `uv.lock`, mirror wheels in CI caches, and keep optional tools out of the default dev group. |
| Geostatistics correctness (kriging, Monte Carlo) | Test against analytic cases and published examples, and cross-check with gstools/pykrige results. |
| User trust for real reserves work | Clear documentation of methods and assumptions, reproducible results, and exportable audit trails of parameters. |

## 6. Decisions needed

| # | Decision | Needed by | Recommendation |
|---|---|---|---|
| 1 | Approve this stage order (it re-sequences the approved P2.x order) | now | Approve |
| 2 | Packaging tool: PyInstaller or Briefcase | MVP, packaging | PyInstaller (mature with GDAL/Qt; many reference spec files) |
| 3 | Project license | before the first public build | GPL-3.0 or LGPL-3.0 to stay compatible with the open GIS ecosystem; MIT if commercial embedding matters more |
| 4 | Kriging library | P2.6b | gstools |
| 5 | Where pyright and pip-audit run, since their local download stalls (removal from the dev group is pending) | P2.0 / CI | Run them in CI only (they download there), not in the local dev group |
| 6 | Beta user group | before Beta | University petroleum engineering departments in Baku, plus a few consultants |

## Sources

- [ArcGIS Pro help](https://doc.esri.com/en/arcgis-pro/latest/help/main/welcome-to-the-arcgis-pro-app-help.html) and [editing overview](https://doc.esri.com/en/arcgis-pro/latest/help/editing/overview-of-desktop-editing.html)
- [QGIS User Guide 3.44](https://docs.qgis.org/latest/en/docs/user_manual/index.html)
- [Petra log correlation module (brochure)](https://cdn.ihsmarkit.com/www/pdf/Brochure-Petra-2016-Log-Correlation-Module-Upstream-English.pdf) and [Petra brochure](https://cdn.ihsmarkit.com/www/pdf/Petra-Brochure.pdf)
- [Kingdom geology brochure](https://www.spglobal.com/dam/spglobal/ci/en/documents/products/pdf/CI_1024_Kingdom-Geology-Upstream-Brochure.pdf)
- [Petrel well correlation](https://software.slb.com/products/petrel/petrel-geology-and-modeling/well-correlation)
- [Techlog Petrophysics](https://www.slb.com/products-and-services/delivering-digital-at-scale/software/techlog-wellbore-software/techlog/techlog-petrophysics) and [Techlog Quanti](https://www.slb.com/zh-cn/products-and-services/delivering-digital-at-scale/software/techlog-wellbore-software/techlog/techlog-petrophysics/techlog-quanti)
- [Global Mapper](https://try.bluemarblegeo.com/global-mapper) and [terrain analysis](https://bluemarblegeo.com/knowledgebase/global-mapper-21/Terrain_Analysis.htm)
- [SAGA GIS (Geoscientific Model Development)](https://www.geosci-model-dev.net/8/1991/2015/)
- [OpenTopography tool registry (GRASS, SAGA)](https://portal.opentopography.org/tools/listTools?search=GIS)
- [OpendTect (dGB Earth Sciences)](https://dgbes.com/software/free) and [source](https://github.com/OpendTect/OpendTect)

Vendor feature lists come from vendor material, not independent tests.
