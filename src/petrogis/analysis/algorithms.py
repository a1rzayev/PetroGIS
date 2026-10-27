"""Built-in processing algorithms for the MVP workflow. Imported once by the registry."""

from __future__ import annotations

from typing import Any

import numpy as np

from petrogis.analysis.processing import (
    Algorithm,
    AlgorithmResult,
    ChoiceParam,
    CrsParam,
    Feedback,
    LayerParam,
    OutputParam,
    Parameter,
    ParameterError,
    RunContext,
    register_algorithm,
)
from petrogis.core.layers import LayerKind, RasterLayer, WellLayer

FEATURE_KINDS = (LayerKind.VECTOR, LayerKind.DELIMITED, LayerKind.WELLS)
CLIP_CHUNK = 5_000
"""Features clipped per step, between progress updates and cancel checks."""


def _vector_output(gdf: Any, path: Any) -> Any:
    from petrogis.io.vector_io import write_geopackage

    return write_geopackage(gdf, path, layer=path.stem)


def _frame_for_output(layer: Any) -> Any:
    """The layer's features without internal columns."""
    return layer.gdf.drop(columns=[c for c in ("name_key",) if c in layer.gdf.columns])


class SampleRasterAtWells(Algorithm):
    id = "raster:sample_at_wells"
    name = "Sample raster at wells"
    group = "Raster"
    description = (
        "Reads the raster value at each well's surface location or bottom hole and writes the "
        "wells as points with a 'value' column, for example structure depth at every well."
    )

    @property
    def parameters(self) -> tuple[Parameter, ...]:
        return (
            LayerParam("wells", "Well layer", kinds=(LayerKind.WELLS,)),
            LayerParam("raster", "Raster", kinds=(LayerKind.RASTER,)),
            ChoiceParam(
                "location", "Sample at", choices=("Wellhead", "Bottom hole"), default="Wellhead"
            ),
            OutputParam("output", "Output GeoPackage", extension=".gpkg"),
        )

    def run(
        self, values: dict[str, Any], context: RunContext, feedback: Feedback
    ) -> AlgorithmResult:
        import geopandas as gpd

        from petrogis.io.raster_ops import sample_points

        wells: WellLayer = values["wells"]
        raster: RasterLayer = values["raster"]
        xy = wells.xy().copy()
        if values["location"] == "Bottom hole":
            for position, key in enumerate(wells.gdf["name_key"]):
                trajectory = wells.trajectories.get(str(key))
                if trajectory is not None:
                    xy[position] += (trajectory.dx[-1], trajectory.dy[-1])
        feedback.progress(0.2)
        sampled = sample_points(
            raster.source,
            context.project_crs,
            xy[:, 0],
            xy[:, 1],
            raster.band,
            raster.assumed_crs,
            context.transformation(raster.source_crs),
        )
        feedback.check()
        frame = _frame_for_output(wells).drop(columns="geometry")
        frame["x"], frame["y"] = xy[:, 0], xy[:, 1]
        frame["value"] = sampled
        out = gpd.GeoDataFrame(
            frame, geometry=gpd.points_from_xy(xy[:, 0], xy[:, 1]), crs=context.project_crs
        )
        path = _vector_output(out, values["output"])
        missing = int(np.isnan(sampled).sum())
        summary = f"Sampled {len(out) - missing} of {len(out)} wells"
        if missing:
            summary += f" ({missing} outside the raster or on nodata)"
        return AlgorithmResult({"output": path}, summary)


class ClipVector(Algorithm):
    id = "vector:clip"
    name = "Clip vector layer by polygons"
    group = "Vector"
    description = "Keeps the parts of the features that lie inside the polygons of a mask layer."

    @property
    def parameters(self) -> tuple[Parameter, ...]:
        return (
            LayerParam("input", "Input layer", kinds=FEATURE_KINDS),
            LayerParam("mask", "Clip polygons", kinds=FEATURE_KINDS, geometry="polygon"),
            OutputParam("output", "Output GeoPackage", extension=".gpkg"),
        )

    def check(self, values: dict[str, Any]) -> None:
        if values["input"] is values["mask"]:
            raise ParameterError("Choose a different layer to clip with")

    def run(
        self, values: dict[str, Any], context: RunContext, feedback: Feedback
    ) -> AlgorithmResult:
        import geopandas as gpd
        import shapely

        source = _frame_for_output(values["input"])
        mask = shapely.union_all(values["mask"].gdf.geometry.values)
        shapely.prepare(mask)
        hits = source.sindex.query(mask, predicate="intersects")
        candidates = source.iloc[np.sort(hits)]
        parts = []
        for start in range(0, len(candidates), CLIP_CHUNK):
            feedback.progress(start / max(len(candidates), 1))
            chunk = candidates.iloc[start : start + CLIP_CHUNK]
            parts.append(gpd.clip(chunk, mask, keep_geom_type=True))
        out = (
            gpd.GeoDataFrame(gpd.pd.concat(parts), crs=context.project_crs)
            if parts
            else gpd.GeoDataFrame(source.iloc[:0], crs=context.project_crs)
        )
        path = _vector_output(out, values["output"])
        return AlgorithmResult({"output": path}, f"Kept {len(out)} of {len(source)} features")


class ClipRaster(Algorithm):
    id = "raster:clip"
    name = "Clip raster by polygons"
    group = "Raster"
    description = "Crops a raster to the polygons of a mask layer; cells outside become nodata."

    @property
    def parameters(self) -> tuple[Parameter, ...]:
        return (
            LayerParam("raster", "Raster", kinds=(LayerKind.RASTER,)),
            LayerParam("mask", "Clip polygons", kinds=FEATURE_KINDS, geometry="polygon"),
            OutputParam("output", "Output GeoTIFF", extension=".tif"),
        )

    def run(
        self, values: dict[str, Any], context: RunContext, feedback: Feedback
    ) -> AlgorithmResult:
        from petrogis.io.raster_ops import clip_raster

        raster: RasterLayer = values["raster"]
        path = clip_raster(
            raster.source,
            values["mask"].gdf.geometry.values,
            context.project_crs,
            values["output"],
            raster.assumed_crs,
            context.transformation(raster.source_crs),
            check=feedback.check,
            progress=feedback.progress,
        )
        return AlgorithmResult({"output": path}, f"Clipped {raster.name}")


class ReprojectVector(Algorithm):
    id = "vector:reproject"
    name = "Reproject vector layer"
    group = "Vector"
    description = (
        "Writes the layer in another coordinate reference system, for exchange with other "
        "software. Loaded back, it is shown in the project CRS as usual."
    )

    @property
    def parameters(self) -> tuple[Parameter, ...]:
        return (
            LayerParam("input", "Input layer", kinds=FEATURE_KINDS),
            CrsParam("crs", "Target CRS"),
            OutputParam("output", "Output GeoPackage", extension=".gpkg"),
        )

    def run(
        self, values: dict[str, Any], context: RunContext, feedback: Feedback
    ) -> AlgorithmResult:
        from petrogis.io.reproject import reproject_frame

        frame = _frame_for_output(values["input"]).set_crs(context.project_crs, allow_override=True)
        feedback.progress(0.1)
        out = reproject_frame(frame, values["crs"])
        feedback.check()
        path = _vector_output(out, values["output"])
        return AlgorithmResult(
            {"output": path}, f"Reprojected {len(out)} features to {values['crs']}"
        )


class ReprojectRaster(Algorithm):
    id = "raster:reproject"
    name = "Reproject raster"
    group = "Raster"
    description = "Warps a raster to another coordinate reference system, block by block."

    @property
    def parameters(self) -> tuple[Parameter, ...]:
        return (
            LayerParam("raster", "Raster", kinds=(LayerKind.RASTER,)),
            CrsParam("crs", "Target CRS"),
            ChoiceParam(
                "resampling",
                "Resampling",
                choices=("bilinear", "nearest", "cubic"),
                default="bilinear",
            ),
            OutputParam("output", "Output GeoTIFF", extension=".tif"),
        )

    def run(
        self, values: dict[str, Any], context: RunContext, feedback: Feedback
    ) -> AlgorithmResult:
        from petrogis.io.raster_ops import reproject_raster

        raster: RasterLayer = values["raster"]
        path = reproject_raster(
            raster.source,
            values["crs"],
            values["output"],
            values["resampling"],
            raster.assumed_crs,
            check=feedback.check,
            progress=feedback.progress,
        )
        return AlgorithmResult({"output": path}, f"Reprojected {raster.name} to {values['crs']}")


class ZonalStatistics(Algorithm):
    id = "raster:zonal_statistics"
    name = "Zonal statistics"
    group = "Raster"
    description = (
        "For each polygon, the count, minimum, maximum, mean, standard deviation and sum of the "
        "raster cells inside it, written as new columns (zs_count, zs_min, …)."
    )

    @property
    def parameters(self) -> tuple[Parameter, ...]:
        return (
            LayerParam("polygons", "Zones (polygons)", kinds=FEATURE_KINDS, geometry="polygon"),
            LayerParam("raster", "Raster", kinds=(LayerKind.RASTER,)),
            OutputParam("output", "Output GeoPackage", extension=".gpkg"),
        )

    def run(
        self, values: dict[str, Any], context: RunContext, feedback: Feedback
    ) -> AlgorithmResult:
        from petrogis.io.raster_ops import zonal_statistics

        raster: RasterLayer = values["raster"]
        zones = _frame_for_output(values["polygons"]).copy()
        stats = zonal_statistics(
            raster.source,
            zones.geometry.values,
            context.project_crs,
            raster.band,
            raster.assumed_crs,
            context.transformation(raster.source_crs),
            check=feedback.check,
            progress=feedback.progress,
        )
        for name, column in stats.items():
            zones[f"zs_{name}"] = column
        path = _vector_output(
            zones.set_crs(context.project_crs, allow_override=True), values["output"]
        )
        empty = int((stats["count"] == 0).sum())
        summary = f"Statistics for {len(zones)} zone(s)"
        if empty:
            summary += f"; {empty} cover no raster cells"
        return AlgorithmResult({"output": path}, summary)


for _algorithm in (
    SampleRasterAtWells(),
    ClipVector(),
    ClipRaster(),
    ReprojectVector(),
    ReprojectRaster(),
    ZonalStatistics(),
):
    register_algorithm(_algorithm)
