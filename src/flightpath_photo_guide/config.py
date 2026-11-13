from pathlib import Path
import glob
import math
import yaml


def load_config(path):
    path = Path(path).resolve()
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict):
        raise ValueError("Configuration must be a YAML mapping.")
    cfg["_root"] = path.parent
    cfg["_path"] = path
    bounds = cfg.get("region", {}).get("bounds")
    if not isinstance(bounds, list) or len(bounds) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in bounds):
        raise ValueError("region.bounds must be [west, south, east, north].")
    west, south, east, north = bounds
    if not (-180 <= west < east <= 180 and -80 <= south < north <= 84):
        raise ValueError("Invalid bounds; local regions must not cross the antimeridian or polar limits.")
    if east-west > 12 or north-south > 12:
        raise ValueError("Choose a local region at most 12 degrees across.")
    mode = cfg.get("mode", "paths")
    if mode not in ("paths", "proximity"):
        raise ValueError("mode must be paths or proximity.")
    cfg["mode"] = mode
    inputs = cfg.get("tracks", {}).get("files", [])
    if isinstance(inputs, str):
        inputs = [inputs]
    files = []
    for pattern in inputs:
        found = sorted(glob.glob(str(path.parent / pattern)))
        if not found:
            raise ValueError(f"No track files match {pattern!r} (relative to {path.parent}).")
        files.extend(found)
    if not files:
        raise ValueError("tracks.files must contain at least one KML path or glob.")
    cfg["_files"] = list(dict.fromkeys(files))
    filters = cfg.get("filters", {})
    for key in ["max_gap_s", "speed_check_gap_s", "max_speed_m_s", "max_vertical_rate_m_s"]:
        if key in filters and (not math.isfinite(float(filters[key])) or float(filters[key]) <= 0):
            raise ValueError(f"filters.{key} must be positive.")
    bands = cfg.get("proximity", {}).get("bands_m", [300, 600, 1000])
    if not bands or len(bands) > 3 or any(not math.isfinite(float(v)) or v <= 0 for v in bands) or sorted(set(bands)) != bands:
        raise ValueError("proximity.bands_m needs one to three increasing positive distances.")
    cfg.setdefault("proximity", {})["bands_m"] = bands
    for p in cfg.get("viewpoints", []):
        if not p.get("name") or not -180 <= p.get("longitude", 1000) <= 180 or not -90 <= p.get("latitude", 1000) <= 90:
            raise ValueError("Each viewpoint needs a name, longitude, and latitude.")
    return cfg


def local_path(cfg, value):
    return (cfg["_root"] / value).resolve()
