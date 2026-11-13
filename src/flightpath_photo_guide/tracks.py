"""KML ingestion, reproducible exclusions, and conservative segment selection."""
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
from pyproj import Geod

GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True)
class Fix:
    lon: float
    lat: float
    altitude: float
    time: float | None


@dataclass
class Track:
    name: str
    source: str
    fixes: list[Fix]
    altitude_mode: str | None


def timestamp(value):
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Track and exclusion timestamps must include a UTC offset or Z.")
    return dt.timestamp()


def children(node, name):
    return [n for n in node.iter() if n.tag.rsplit("}", 1)[-1] == name]


def load_kml(paths):
    tracks = []
    for path in paths:
        root = ET.parse(path).getroot()
        for pm in children(root, "Placemark"):
            names = [n.text for n in pm if n.tag.rsplit("}", 1)[-1] == "name"]
            name = (names[0] if names else None) or Path(path).stem
            for index, geom in enumerate(children(pm, "Track") + children(pm, "LineString")):
                modes = children(geom, "altitudeMode")
                mode = modes[0].text if modes else None
                # Some exporters put altitudeMode at Placemark level.
                if mode is None:
                    direct = [n for n in pm if n.tag.rsplit("}", 1)[-1] == "altitudeMode"]
                    mode = direct[0].text if direct else None
                times = [timestamp(n.text) for n in children(geom, "when")]
                if geom.tag.rsplit("}", 1)[-1] == "Track":
                    coords = [tuple(float(v) for v in (n.text or "").split()) for n in children(geom, "coord")]
                    if len(coords) != len(times):
                        raise ValueError(f"{path}: gx:Track coordinate/timestamp counts differ.")
                else:
                    coords = [tuple(float(v) for v in part.split(",")) for n in children(geom, "coordinates") for part in (n.text or "").split()]
                fixes = []
                for i, c in enumerate(coords):
                    if len(c) not in (2, 3) or not all(math.isfinite(v) for v in c):
                        raise ValueError(f"{path}: invalid KML coordinate {c}.")
                    if not (-180 <= c[0] <= 180 and -90 <= c[1] <= 90):
                        raise ValueError(f"{path}: longitude/latitude out of range.")
                    fixes.append(Fix(c[0], c[1], c[2] if len(c) == 3 else math.nan, times[i] if times else None))
                if times:
                    fixes = sorted(set(fixes), key=lambda f: (f.time, f.lon, f.lat, f.altitude))
                if fixes:
                    tracks.append(Track(name if index == 0 else f"{name}/{index+1}", str(path), fixes, mode))
    if not tracks:
        raise ValueError("No gx:Track or LineString geometry found in the supplied KML files.")
    return tracks


def selected_runs(track, filters, exclusions, proximity=False):
    """Return runs without ever connecting across a rejected report or long gap."""
    fixes = track.fixes
    valid = np.ones(len(fixes), dtype=bool)
    rejected = []

    def reject(index, reason):
        valid[index] = False
        f = fixes[index]
        rejected.append({"track": track.name, "source": track.source, "time": f.time,
                         "longitude": f.lon, "latitude": f.lat, "altitude_m": f.altitude if math.isfinite(f.altitude) else None,
                         "reason": reason})

    for rule in exclusions:
        if not rule.get("reason"):
            raise ValueError("Each exclusion needs a reason.")
        if rule.get("track") != track.name:
            continue
        low = timestamp(rule["start"])
        high = timestamp(rule.get("end", rule["start"]))
        for i, f in enumerate(fixes):
            if f.time is not None and low <= f.time <= high:
                reject(i, rule["reason"])
    if proximity:
        if track.altitude_mode not in (None, "absolute"):
            raise ValueError(f"{track.name}: proximity requires absolute geometric heights; KML mode is {track.altitude_mode}.")
        if any(f.time is None or not math.isfinite(f.altitude) for f in fixes):
            raise ValueError(f"{track.name}: proximity needs timestamped fixes with absolute altitudes.")
    max_speed = float(filters.get("max_speed_m_s", 400))
    max_vertical = float(filters.get("max_vertical_rate_m_s", 120))
    for i, (a, b) in enumerate(zip(fixes, fixes[1:])):
        if a.time is None or b.time is None:
            continue
        dt = b.time - a.time
        horizontal = GEOD.inv(a.lon, a.lat, b.lon, b.lat)[2]
        vertical = abs(b.altitude - a.altitude) if proximity else 0
        # Conflicting near-simultaneous reports must not generate a hotspot.
        if 0 <= dt <= .2 and (horizontal > 50 or vertical > 50):
            reject(i, "Conflicting near-simultaneous position/height reports")
            reject(i+1, "Conflicting near-simultaneous position/height reports")
        elif .2 < dt <= float(filters.get("speed_check_gap_s", 12)):
            if math.hypot(horizontal, vertical)/dt > max_speed or (proximity and vertical/dt > max_vertical):
                reject(i, "Configured speed or vertical-rate limit exceeded")
                reject(i+1, "Configured speed or vertical-rate limit exceeded")
    max_gap = float(filters.get("max_gap_s", 6))
    runs, run = [], []
    for i, f in enumerate(fixes):
        if valid[i] and run and f.time is not None and run[-1].time is not None and 0 <= f.time-run[-1].time <= .2:
            previous = run[-1]
            if GEOD.inv(previous.lon, previous.lat, f.lon, f.lat)[2] <= 5 and (not proximity or abs(previous.altitude-f.altitude) <= 5):
                continue
        gap = False
        if run and f.time is not None and run[-1].time is not None:
            gap = not .2 < f.time-run[-1].time <= max_gap
        if not valid[i] or gap:
            if len(run) >= 2:
                runs.append(run)
            run = []
        if valid[i]:
            run.append(f)
    if len(run) >= 2:
        runs.append(run)
    return runs, rejected


def geojson_features(runs_by_track):
    features = []
    for track, runs in runs_by_track:
        for run in runs:
            features.append({"type": "Feature", "properties": {"track": track.name, "source": Path(track.source).name,
                             "timestamps": [datetime.fromtimestamp(f.time, timezone.utc).isoformat() if f.time is not None else None for f in run]},
                             "geometry": {"type": "LineString", "coordinates": [[f.lon, f.lat] for f in run]}})
    return {"type": "FeatureCollection", "features": features}
