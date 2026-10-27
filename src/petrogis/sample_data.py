"""Synthetic demo field near the Absheron peninsula (UTM 39N), used for demos and tests.

The field is an elongated WNW-ESE anticline (the typical Absheron structural trend). It produces:

- ``structure_top.tif``: depth to top reservoir, TVDSS in metres (positive down), EPSG:32639
- ``field_boundary.geojson``: closing contour of the structure, written in EPSG:4326 (RFC 7946)
  so that loading it exercises reprojection to the project CRS
- ``wells.csv`` / ``wells.xlsx``: the same well headers under different column names, so that
  column auto-detection gets exercised
- ``surveys.csv``: deviation surveys for the two deviated wells (AB-04 and AB-06), the others
  being vertical
- ``tops.csv``: formation tops (MD) for every well, consistent with the logs and the structure
- ``las/*.las``: one LAS 2.0 file per well. Curve mnemonics vary per well (GR/GR_EDTC/SGR, ...) and
  WELL names are written inconsistently (``AB-01``, ``AB 02``, ``ab_03``, ``AB-4``). There is also
  one offset well (``OFFSET-1``) with no matching header, to test unmatched-LAS reporting.

Everything is deterministic for a given seed. This module does not import Qt.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from petrogis.core.trajectory import Trajectory

CRS_EPSG = 32639
CENTER_X = 405_000.0
CENTER_Y = 4_475_000.0
CREST_TVDSS = 1450.0
SPILL_TVDSS = 1700.0
OWC_TVDSS = 1600.0
RESERVOIR_THICKNESS = 60.0
AXIS_AZIMUTH_FROM_EAST_DEG = 150.0  # long axis points WNW
# Curvature: depth rises by 400 m at 5 km along the axis and at 2 km across it.
K_ALONG = 400.0 / 5000.0**2
K_ACROSS = 400.0 / 2000.0**2

RASTER_WIDTH_M = 14_000.0
RASTER_HEIGHT_M = 10_000.0
RASTER_PIXEL_M = 25.0
RASTER_NODATA = -9999.0

N_WELLS = 10
LAS_STEP_M = 0.2
LAS_NULL = -999.25

# Mnemonic variants per curve family, cycled per well so the alias table gets exercised.
MNEMONIC_VARIANTS: dict[str, list[tuple[str, str]]] = {
    "GR": [("GR", "GAPI"), ("GR_EDTC", "GAPI"), ("SGR", "GAPI")],
    "RT": [("RT", "OHMM"), ("ILD", "OHMM"), ("LLD", "OHMM"), ("RDEP", "OHMM")],
    "NPHI": [("NPHI", "V/V"), ("TNPH", "V/V"), ("NPOR", "V/V")],
    "RHOB": [("RHOB", "G/CC"), ("RHOZ", "G/CC"), ("DEN", "G/CC")],
}
WELL_NAME_STYLES = ("AB-{:02d}", "AB {:02d}", "ab_{:02d}", "AB-{}")
SURVEY_STEP_M = 30.0
# Deviated wells by index: kick-off MD, build rate (°/30 m), hold inclination, and azimuth
# relative to the bearing towards the crest (both are steered towards the structure).
DEVIATED = {3: (700.0, 2.5, 35.0, 20.0), 5: (600.0, 3.0, 50.0, -15.0)}
MARKER_ABOVE_TOP_M = 120.0
"""The "Shale Marker" top lies this far (TVD) above top reservoir."""


@dataclass(frozen=True)
class WellHeader:
    name: str
    uwi: str
    x: float
    y: float
    kb: float
    td_md: float
    top_tvdss: float
    """Top reservoir TVDSS where the well penetrates it."""
    status: str
    top_md: float = float("nan")
    survey: tuple[tuple[float, float, float], ...] = ()
    """(MD, inclination, azimuth) stations for deviated wells; empty for vertical ones."""

    def trajectory(self) -> Trajectory:
        if not self.survey:
            return Trajectory.vertical(self.td_md)
        md, inc, azi = (np.array(v) for v in zip(*self.survey, strict=True))
        return Trajectory.from_survey(md, inc, azi)


@dataclass
class SampleField:
    out_dir: Path
    structure_top: Path
    field_boundary: Path
    wells_csv: Path
    wells_xlsx: Path
    las_dir: Path
    surveys_csv: Path
    tops_csv: Path
    las_files: list[Path] = field(default_factory=list)
    wells: list[WellHeader] = field(default_factory=list)


def _axis_coords(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Rotate map coordinates into (along-axis, across-axis) offsets from the crest."""
    theta = np.deg2rad(AXIS_AZIMUTH_FROM_EAST_DEG)
    dx = x - CENTER_X
    dy = y - CENTER_Y
    along = dx * np.cos(theta) + dy * np.sin(theta)
    across = -dx * np.sin(theta) + dy * np.cos(theta)
    return along, across


def structure_depth(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Top reservoir depth, TVDSS in metres (positive down), at map coordinates (x, y)."""
    along, across = _axis_coords(np.asarray(x, dtype=float), np.asarray(y, dtype=float))
    smooth = K_ALONG * along**2 + K_ACROSS * across**2
    # Gentle deterministic undulation so contours aren't perfect ellipses.
    ripple = 6.0 * np.sin(along / 900.0) * np.cos(across / 700.0)
    return CREST_TVDSS + smooth + ripple


def _closing_contour(n_vertices: int = 96) -> np.ndarray:
    """Return the (x, y) vertices of the smooth-surface contour at the spill depth (closed ring)."""
    relief = SPILL_TVDSS - CREST_TVDSS
    a = np.sqrt(relief / K_ALONG)
    b = np.sqrt(relief / K_ACROSS)
    t = np.linspace(0.0, 2.0 * np.pi, n_vertices, endpoint=False)
    along = a * np.cos(t)
    across = b * np.sin(t)
    theta = np.deg2rad(AXIS_AZIMUTH_FROM_EAST_DEG)
    x = CENTER_X + along * np.cos(theta) - across * np.sin(theta)
    y = CENTER_Y + along * np.sin(theta) + across * np.cos(theta)
    ring = np.column_stack([x, y])
    return np.vstack([ring, ring[:1]])


def write_structure_raster(path: Path) -> None:
    import rasterio
    from rasterio.transform import from_origin

    width = int(RASTER_WIDTH_M / RASTER_PIXEL_M)
    height = int(RASTER_HEIGHT_M / RASTER_PIXEL_M)
    xmin = CENTER_X - RASTER_WIDTH_M / 2
    ymax = CENTER_Y + RASTER_HEIGHT_M / 2
    half = RASTER_PIXEL_M / 2
    xs = xmin + half + RASTER_PIXEL_M * np.arange(width)
    ys = ymax - half - RASTER_PIXEL_M * np.arange(height)
    grid_x, grid_y = np.meshgrid(xs, ys)
    depth = structure_depth(grid_x, grid_y).astype(np.float32)
    # Blank the far corners so the nodata path is exercised.
    corner = (np.abs(grid_x - CENTER_X) > 6000) & (np.abs(grid_y - CENTER_Y) > 4200)
    depth[corner] = RASTER_NODATA

    profile = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "count": 1,
        "dtype": "float32",
        "crs": f"EPSG:{CRS_EPSG}",
        "transform": from_origin(xmin, ymax, RASTER_PIXEL_M, RASTER_PIXEL_M),
        "nodata": RASTER_NODATA,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "compress": "deflate",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(depth, 1)
        dst.set_band_description(1, "Top Reservoir TVDSS (m)")
        # Overviews let display code read downsampled data cheaply.
        dst.build_overviews([2, 4, 8], rasterio.enums.Resampling.average)
        dst.update_tags(ns="rio_overview", resampling="average")


def write_field_boundary(path: Path) -> None:
    import geopandas as gpd
    from shapely import Polygon

    ring = _closing_contour()
    gdf = gpd.GeoDataFrame(
        {"name": ["Absheron Demo Field"], "spill_tvdss_m": [SPILL_TVDSS]},
        geometry=[Polygon(ring)],
        crs=f"EPSG:{CRS_EPSG}",
    ).to_crs("EPSG:4326")
    gdf.to_file(path, driver="GeoJSON")


def make_well_headers(rng: np.random.Generator) -> list[WellHeader]:
    """Seven wells on the structure and three flank wells outside the closure."""
    relief = SPILL_TVDSS - CREST_TVDSS
    a = np.sqrt(relief / K_ALONG)
    b = np.sqrt(relief / K_ACROSS)
    n_inside = 7
    along = np.concatenate(
        [rng.uniform(-0.75, 0.75, n_inside) * a, np.array([1.25, -1.2, 0.3]) * a]
    )
    across = np.concatenate([rng.uniform(-0.6, 0.6, n_inside) * b, np.array([0.2, -0.4, 1.6]) * b])
    theta = np.deg2rad(AXIS_AZIMUTH_FROM_EAST_DEG)
    xs = CENTER_X + along * np.cos(theta) - across * np.sin(theta)
    ys = CENTER_Y + along * np.sin(theta) + across * np.cos(theta)
    kbs = rng.uniform(15.0, 45.0, N_WELLS)
    tops = structure_depth(xs, ys)
    wells = []
    for i in range(N_WELLS):
        kb = round(float(kbs[i]), 1)
        top = float(tops[i])
        survey: tuple[tuple[float, float, float], ...] = ()
        if i in DEVIATED:
            kop, build, hold, offset = DEVIATED[i]
            to_crest = np.degrees(np.arctan2(CENTER_X - xs[i], CENTER_Y - ys[i]))
            azimuth = round(float((to_crest + offset) % 360.0), 1)
            survey = _build_hold_survey(kop, build, hold, azimuth, total_md=4000.0)
            path = Trajectory.from_survey(*(np.array(v) for v in zip(*survey, strict=True)))
            # Iterate to where the path actually meets the structure.
            for _ in range(6):
                md_top = float(path.tvd_to_md(top + kb)[0])
                dx, dy = path.md_to_xy(md_top)
                top = float(structure_depth(xs[i] + dx, ys[i] + dy)[0])
            td_md = round(float(path.tvd_to_md(top + kb + RESERVOIR_THICKNESS + 150.0)[0]), 1)
            survey = tuple(st for st in survey if st[0] <= td_md + SURVEY_STEP_M)
            top_md = round(float(path.tvd_to_md(top + kb)[0]), 2)
        else:
            td_md = round(top + RESERVOIR_THICKNESS + 150.0 + kb, 1)
            top_md = round(top + kb, 2)
        status = "OIL" if top < OWC_TVDSS else "DRY"
        wells.append(
            WellHeader(
                name=f"AB-{i + 1:02d}",
                uwi=f"AZ-ABS-{i + 1:04d}",
                x=round(float(xs[i]), 2),
                y=round(float(ys[i]), 2),
                kb=kb,
                td_md=td_md,
                top_tvdss=round(top, 2),
                status=status,
                top_md=top_md,
                survey=survey,
            )
        )
    return wells


def _build_hold_survey(
    kop: float, build_per_30m: float, hold_inc: float, azimuth: float, total_md: float
) -> tuple[tuple[float, float, float], ...]:
    """Vertical to the kick-off point, a constant build, then a tangent section."""
    md = np.arange(0.0, total_md + SURVEY_STEP_M / 2, SURVEY_STEP_M)
    inc = np.clip((md - kop) / 30.0 * build_per_30m, 0.0, hold_inc)
    azi = np.where(md > kop, azimuth, 0.0)
    return tuple(
        (float(m), round(float(i), 3), float(a)) for m, i, a in zip(md, inc, azi, strict=True)
    )


def write_surveys(wells: list[WellHeader], path: Path) -> None:
    import pandas as pd

    rows = [(w.name, md, inc, azi) for w in wells for md, inc, azi in w.survey]
    pd.DataFrame(rows, columns=["Well", "MD (m)", "Inclination", "Azimuth"]).to_csv(
        path, index=False
    )


def write_tops(wells: list[WellHeader], path: Path) -> None:
    """Long-format tops (well, surface, MD) for every well, derived through each trajectory."""
    import pandas as pd

    rows = []
    for well in wells:
        trajectory = well.trajectory()
        for surface, tvdss in (
            ("Shale Marker", well.top_tvdss - MARKER_ABOVE_TOP_M),
            ("Top Reservoir", well.top_tvdss),
            ("Base Reservoir", well.top_tvdss + RESERVOIR_THICKNESS),
        ):
            md = float(trajectory.tvd_to_md(tvdss + well.kb)[0])
            rows.append((well.name, surface, round(md, 2)))
    pd.DataFrame(rows, columns=["Well", "Surface", "MD"]).to_csv(path, index=False)


def write_well_tables(wells: list[WellHeader], csv_path: Path, xlsx_path: Path) -> None:
    import pandas as pd

    csv_df = pd.DataFrame(
        {
            "Well": [w.name for w in wells],
            "UWI": [w.uwi for w in wells],
            "Easting": [w.x for w in wells],
            "Northing": [w.y for w in wells],
            "KB": [w.kb for w in wells],
            "TD": [w.td_md for w in wells],
            "Status": [w.status for w in wells],
        }
    )
    csv_df.to_csv(csv_path, index=False)
    xlsx_df = csv_df.rename(
        columns={
            "Well": "Well Name",
            "Easting": "X",
            "Northing": "Y",
            "KB": "Elevation",
            "TD": "Total Depth",
        }
    ).drop(columns=["UWI"])
    xlsx_df.to_excel(xlsx_path, index=False, sheet_name="Wells")


def _smooth(values: np.ndarray, window: int) -> np.ndarray:
    kernel = np.ones(window) / window
    padded = np.pad(values, window // 2, mode="edge")
    return np.convolve(padded, kernel, mode="valid")[: values.size]


def synthetic_logs(
    rng: np.random.Generator,
    top_tvdss: float,
    kb: float,
    start_md: float,
    stop_md: float,
    trajectory: Trajectory | None = None,
) -> dict[str, np.ndarray]:
    """Vectorised synthetic GR/RT/NPHI/RHOB along a well (vertical unless ``trajectory``)."""
    md = np.round(np.arange(start_md, stop_md + LAS_STEP_M / 2, LAS_STEP_M), 4)
    tvdss = (trajectory.md_to_tvd(md) if trajectory is not None else md) - kb
    n = md.size

    # Sand fraction: random thin sands in the overburden and underburden, plus the reservoir
    # package with two shale breaks.
    sand = np.zeros(n)
    n_thin = rng.integers(6, 12)
    for centre, thick in zip(
        rng.uniform(start_md, stop_md, n_thin), rng.uniform(1.0, 6.0, n_thin), strict=True
    ):
        sand[np.abs(md - centre) < thick / 2] = rng.uniform(0.4, 0.8)
    reservoir = (tvdss >= top_tvdss) & (tvdss < top_tvdss + RESERVOIR_THICKNESS)
    sand[reservoir] = 0.9
    for brk in (0.35, 0.7):
        sand[np.abs(tvdss - (top_tvdss + brk * RESERVOIR_THICKNESS)) < 1.5] = 0.1
    sand = np.clip(_smooth(sand, 5), 0.0, 1.0)

    hydrocarbon = (sand > 0.5) & reservoir & (tvdss < OWC_TVDSS)
    noise = rng.normal(size=(4, n))
    gr = 115.0 - 80.0 * sand + 4.0 * noise[0]
    rt_water = np.where(sand > 0.5, 0.8, 2.5)
    rt = np.where(hydrocarbon, rng.uniform(30.0, 80.0), rt_water) * np.exp(0.08 * noise[1])
    nphi = 0.36 - 0.14 * sand - np.where(hydrocarbon, 0.04, 0.0) + 0.01 * noise[2]
    rhob = 2.50 - 0.27 * sand - np.where(hydrocarbon, 0.05, 0.0) + 0.015 * noise[3]
    return {
        "DEPT": md,
        "GR": _smooth(gr, 3),
        "RT": _smooth(rt, 3),
        "NPHI": np.clip(_smooth(nphi, 3), 0.0, 0.6),
        "RHOB": _smooth(rhob, 3),
    }


def write_las(
    path: Path, well_name: str, uwi: str, logs: dict[str, np.ndarray], variant: int
) -> None:
    import lasio

    las = lasio.LASFile()
    las.well["WELL"].value = well_name
    las.well["UWI"].value = uwi
    las.well["COMP"].value = "PetroGIS Synthetic"
    las.well["FLD"].value = "Absheron Demo Field"
    las.well["NULL"].value = LAS_NULL
    las.append_curve("DEPT", logs["DEPT"], unit="M", descr="Measured depth")
    descr = {
        "GR": "Gamma ray",
        "RT": "Deep resistivity",
        "NPHI": "Neutron porosity",
        "RHOB": "Bulk density",
    }
    for family in ("GR", "RT", "NPHI", "RHOB"):
        variants = MNEMONIC_VARIANTS[family]
        mnemonic, unit = variants[variant % len(variants)]
        las.append_curve(mnemonic, logs[family], unit=unit, descr=descr[family])
    with path.open("w", encoding="utf-8") as fh:
        las.write(fh, version=2.0, fmt="%.4f")


def generate(out_dir: Path, seed: int = 42) -> SampleField:
    """Write the full synthetic field into ``out_dir`` and return the paths and well headers."""
    out_dir = Path(out_dir)
    las_dir = out_dir / "las"
    las_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    result = SampleField(
        out_dir=out_dir,
        structure_top=out_dir / "structure_top.tif",
        field_boundary=out_dir / "field_boundary.geojson",
        wells_csv=out_dir / "wells.csv",
        wells_xlsx=out_dir / "wells.xlsx",
        las_dir=las_dir,
        surveys_csv=out_dir / "surveys.csv",
        tops_csv=out_dir / "tops.csv",
    )
    write_structure_raster(result.structure_top)
    write_field_boundary(result.field_boundary)
    result.wells = make_well_headers(rng)
    write_well_tables(result.wells, result.wells_csv, result.wells_xlsx)
    write_surveys(result.wells, result.surveys_csv)
    write_tops(result.wells, result.tops_csv)

    for i, well in enumerate(result.wells):
        number = i + 1
        las_name = WELL_NAME_STYLES[i % len(WELL_NAME_STYLES)].format(number)
        top_md = well.top_md
        logs = synthetic_logs(
            rng,
            well.top_tvdss,
            well.kb,
            start_md=top_md - 300.0,
            stop_md=well.td_md,
            trajectory=well.trajectory() if well.survey else None,
        )
        if i in (2, 6):
            # A density gap (pad/tool failure) so null handling gets exercised.
            gap = (logs["DEPT"] > top_md - 120.0) & (logs["DEPT"] < top_md - 110.0)
            logs["RHOB"] = np.where(gap, np.nan, logs["RHOB"])
        path = las_dir / f"{las_name.replace(' ', '_').replace('-', '_').upper()}.las"
        write_las(path, las_name, well.uwi, logs, variant=i)
        result.las_files.append(path)

    offset_logs = synthetic_logs(rng, 1875.0, 25.0, start_md=1600.0, stop_md=2100.0)
    offset_path = las_dir / "OFFSET_1.las"
    write_las(offset_path, "OFFSET-1", "AZ-OFF-0001", offset_logs, variant=0)
    result.las_files.append(offset_path)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("sample_data"), help="output directory")
    parser.add_argument("--seed", type=int, default=42, help="random seed")
    args = parser.parse_args(argv)
    result = generate(args.out, args.seed)
    print(f"Wrote synthetic field to {result.out_dir.resolve()}")
    for path in (
        result.structure_top,
        result.field_boundary,
        result.wells_csv,
        result.wells_xlsx,
        result.surveys_csv,
        result.tops_csv,
    ):
        print(f"  {path.name}")
    print(f"  las/ ({len(result.las_files)} files)")
    return 0
