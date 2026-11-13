"""Metric distances to accepted, short observed segments, never display curves."""
from dataclasses import dataclass
import json
import math

import numpy as np
from pyproj import CRS, Transformer
from pyproj.transformer import TransformerGroup, AreaOfInterest
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.warp import reproject, Resampling, transform_geom, transform_bounds
from scipy.ndimage import map_coordinates
from scipy.spatial import cKDTree

from .config import local_path


def local_metric_crs(bounds):
    west, south, east, north = bounds
    lat, lon = (north+south)/2, (west+east)/2
    zone = min(60, max(1, math.floor((lon+180)/6)+1))
    return CRS.from_epsg((32600 if lat >= 0 else 32700)+zone)


def height_crs(value, name):
    if not value:
        raise ValueError(f"{name} is required for proximity. Unknown or barometric heights cannot be compared to terrain.")
    crs = CRS.from_user_input(value)
    if len(crs.axis_info) < 3 or crs.axis_info[2].unit_name.lower() not in ("metre", "meter"):
        raise ValueError(f"{name} must be a 3D/compound height CRS with vertical units in metres.")
    return crs


def align_heights(values, lon, lat, source, target, bounds):
    if source.equals(target):
        return values, "Same explicitly declared vertical reference; no height conversion."
    group = TransformerGroup(source, target, always_xy=True, allow_ballpark=False,
                             area_of_interest=AreaOfInterest(*bounds))
    if not group.best_available or not group.transformers:
        raise ValueError("Required vertical transformation grids are unavailable. Install the appropriate PROJ grids or supply terrain already aligned with flight heights. No ballpark fallback is used.")
    transform = group.transformers[0]
    sx, sy = Transformer.from_crs(4326, source.to_2d(), always_xy=True).transform(lon, lat)
    valid = np.isfinite(values)
    result = np.full(values.shape, np.nan)
    if np.any(valid):
        _, _, z = transform.transform(sx[valid], sy[valid], values[valid], errcheck=True)
        result[valid] = z
    return result, transform.description


@dataclass
class Proximity:
    crs: CRS
    transform: object
    terrain: np.ndarray
    surface: np.ndarray
    distance: np.ndarray
    starts: np.ndarray
    ends: np.ndarray
    runs: list
    vertical_operation: str
    rejected_segments: int

    def sample(self, array, x, y):
        cols = (np.atleast_1d(x)-self.transform.c)/self.transform.a-.5
        rows = (np.atleast_1d(y)-self.transform.f)/self.transform.e-.5
        return map_coordinates(array, [rows, cols], order=1, mode="constant", cval=np.nan)


def closest_to_segments(observers, starts, ends):
    """Analytic nearest points, used for the relatively few named viewpoints."""
    observers = np.atleast_2d(observers)
    delta = ends-starts
    square = np.maximum(np.sum(delta*delta, axis=1), 1e-12)
    fraction = np.clip(np.sum((observers[:, None, :]-starts)*delta, axis=2)/square, 0, 1)
    targets = starts+fraction[:, :, None]*delta
    distances = np.linalg.norm(targets-observers[:, None, :], axis=2)
    return distances, targets


def calculate(cfg, runs_by_track, out):
    options = cfg.get("proximity", {})
    terrain_cfg = cfg.get("terrain", {})
    if not terrain_cfg.get("path"):
        raise ValueError("terrain.path must point to a georeferenced DEM for proximity mode.")
    flight_crs = height_crs(cfg.get("tracks", {}).get("height_crs"), "tracks.height_crs")
    terrain_crs = height_crs(terrain_cfg.get("height_crs"), "terrain.height_crs")
    bounds = cfg["region"]["bounds"]
    crs = local_metric_crs(bounds)
    left, bottom, right, top = transform_bounds(4326, crs, *bounds, densify_pts=21)
    cell = float(options.get("cell_size_m", 20))
    step = float(options.get("sample_step_m", 10))
    if not math.isfinite(cell) or not math.isfinite(step) or cell <= 0 or step <= 0:
        raise ValueError("Cell size and segment sample spacing must be positive.")
    width, height = math.ceil((right-left)/cell), math.ceil((top-bottom)/cell)
    if width*height > 4_000_000:
        raise ValueError("Analysis exceeds four million cells; increase proximity.cell_size_m or choose a smaller region.")
    tf = from_origin(left, top, cell, cell)
    terrain = np.full((height, width), np.nan, np.float32)
    with rasterio.open(local_path(cfg, terrain_cfg["path"])) as ds:
        if ds.crs is None:
            raise ValueError("Terrain GeoTIFF needs a horizontal CRS.")
        reproject(rasterio.band(ds, 1), terrain, src_transform=ds.transform, src_crs=ds.crs,
                  src_nodata=ds.nodata, dst_transform=tf, dst_crs=crs, dst_nodata=np.nan,
                  resampling=Resampling.bilinear)
    rows, cols = np.indices(terrain.shape)
    xx = left+(cols+.5)*cell
    yy = top-(rows+.5)*cell
    LL = Transformer.from_crs(crs, 4326, always_xy=True)
    lon, lat = LL.transform(xx, yy)
    terrain, operation = align_heights(terrain, lon, lat, terrain_crs, flight_crs, bounds)
    surface = terrain.copy()
    model = Proximity(crs, tf, terrain, surface, None, None, None, [], operation, 0)
    if terrain_cfg.get("buildings"):
        features = json.loads(local_path(cfg, terrain_cfg["buildings"]).read_text(encoding="utf-8"))["features"]
        shapes = []
        for feature in features:
            geom = transform_geom("EPSG:4326", crs, feature["geometry"])
            # Input roof elevations must already use the flight-height reference.
            roof = feature.get("properties", {}).get("roof_height_m")
            if roof is None or not math.isfinite(float(roof)):
                raise ValueError("Each building needs roof_height_m: absolute metres in the flight-height datum.")
            shapes.append((geom, float(roof)))
        if shapes:
            roofs = rasterize(sorted(shapes, key=lambda item: item[1]), out_shape=terrain.shape,
                              transform=tf, fill=-np.inf, dtype="float64", all_touched=True)
            surface[:] = np.maximum(terrain, roofs)
    XY = Transformer.from_crs(4326, crs, always_xy=True)
    starts, ends, cloud, accepted = [], [], [], []
    for track, runs in runs_by_track:
        accepted_runs = []
        for run in runs:
            current = []
            for a, b in zip(run, run[1:]):
                start = np.array((*XY.transform(a.lon, a.lat), a.altitude))
                end = np.array((*XY.transform(b.lon, b.lat), b.altitude))
                fraction = np.linspace(0, 1, max(2, math.ceil(np.linalg.norm(end-start)/step)+1))[:, None]
                points = start+(end-start)*fraction
                ground = model.sample(surface, points[:, 0], points[:, 1])
                if np.any(~np.isfinite(ground)) or np.any(points[:, 2] < ground):
                    model.rejected_segments += 1
                    if len(current) >= 2:
                        accepted_runs.append(current)
                    current = []
                    continue
                starts.append(start); ends.append(end); cloud.append(points)
                if not current:
                    current.append(a)
                current.append(b)
            if len(current) >= 2:
                accepted_runs.append(current)
        accepted.append((track, accepted_runs))
    if not cloud:
        raise ValueError("No continuous segments remain inside terrain coverage and above the modeled surface.")
    model.starts = np.array(starts); model.ends = np.array(ends); model.runs = accepted
    observer_height = float(options.get("observer_height_m", 1.7))
    if not math.isfinite(observer_height) or observer_height < 0:
        raise ValueError("observer_height_m must be nonnegative.")
    observers = np.column_stack((xx.ravel(), yy.ravel(), (terrain+observer_height).ravel()))
    valid = np.isfinite(terrain).ravel()
    distance = np.full(terrain.size, np.nan)
    distance[valid] = cKDTree(np.vstack(cloud)).query(observers[valid], workers=1)[0]
    model.distance = distance.reshape(terrain.shape)
    # Building roofs are obstructions, not automatically accessible standing areas.
    model.distance[surface > terrain+.1] = np.nan
    if terrain_cfg.get("land_mask"):
        mask_json = json.loads(local_path(cfg, terrain_cfg["land_mask"]).read_text(encoding="utf-8"))
        shapes = [(transform_geom("EPSG:4326", crs, f["geometry"]), 1) for f in mask_json["features"]]
        mask = rasterize(shapes, out_shape=terrain.shape, transform=tf, fill=0)
        model.distance[mask == 0] = np.nan
    with rasterio.open(out/"proximity.tif", "w", driver="GTiff", width=width, height=height, count=1,
                       dtype="float32", crs=crs, transform=tf, nodata=np.nan, compress="deflate") as ds:
        ds.write(model.distance.astype("float32"), 1)
        ds.update_tags(measurement="minimum sampled 3D distance to accepted short segments, metres",
                       flight_height_crs=flight_crs.to_string(), terrain_height_crs=terrain_crs.to_string())
    return model


def viewpoint_estimates(cfg, model):
    XY = Transformer.from_crs(4326, model.crs, always_xy=True)
    output = []
    for p in cfg.get("viewpoints", []):
        x, y = XY.transform(p["longitude"], p["latitude"])
        z = float(model.sample(model.terrain, x, y)[0])
        result = {"name": p["name"], "longitude": p["longitude"], "latitude": p["latitude"],
                  "note": p.get("note", ""), "geometric_closest_m": None, "closest_visible_m": None}
        if math.isfinite(z):
            eye = z+float(cfg["proximity"].get("observer_height_m", 1.7))+float(p.get("extra_height_m", 0))
            observer = np.array([x, y, eye])
            distances, targets = closest_to_segments(observer, model.starts, model.ends)
            result["geometric_closest_m"] = float(distances.min())
            for i in np.argsort(distances[0])[:100]:
                target = targets[0, i]
                length = np.linalg.norm((target-observer)[:2])
                u = np.linspace(min(10/max(length, 10), .5), 1, max(3, min(2000, math.ceil(length/10))))[:, None]
                ray = observer+(target-observer)*u
                ground = model.sample(model.surface, ray[:, 0], ray[:, 1])
                if np.all(np.isfinite(ground)) and np.all(ray[:, 2] >= ground):
                    result["closest_visible_m"] = float(distances[0, i])
                    break
        output.append(result)
    return output
