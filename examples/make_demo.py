"""Generate entirely synthetic tracks and terrain; no network or real aircraft."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import math

import numpy as np
import rasterio
from rasterio.transform import from_origin
from pyproj import Transformer
import yaml

ROOT = Path(__file__).resolve().parent


def make_demo(name, lon, lat, crs):
    xy = Transformer.from_crs(4326, crs, always_xy=True)
    ll = Transformer.from_crs(crs, 4326, always_xy=True)
    cx, cy = xy.transform(lon, lat)
    west, south = ll.transform(cx-3000, cy-2500)
    east, north = ll.transform(cx+3000, cy+2500)
    angle = np.linspace(0, 2*math.pi, 101)
    xs, ys = cx+2200*np.sin(angle), cy+1200*np.sin(2*angle)
    lons, lats = ll.transform(xs, ys)
    first = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    entries = []
    for i, (x, y) in enumerate(zip(lons, lats)):
        entries.append(f'<when>{(first+timedelta(seconds=i*5)).isoformat()}</when><gx:coord>{x} {y} 650</gx:coord>')
    (ROOT/"data"/f"{name}.kml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2"><Document><Placemark><name>Synthetic figure eight</name><gx:Track><altitudeMode>absolute</altitudeMode>'+''.join(entries)+'</gx:Track></Placemark></Document></kml>', encoding="utf-8")
    size = 350
    tf = from_origin(cx-3500, cy+3500, 20, 20)
    rows, cols = np.indices((size, size))
    terrain = (40+230*np.exp(-((cols-180)**2+(rows-160)**2)/2300)).astype("float32")
    with rasterio.open(ROOT/"data"/f"{name}.tif", "w", driver="GTiff", width=size, height=size, count=1,
                       dtype="float32", crs=crs, transform=tf, nodata=-9999, compress="deflate") as ds:
        ds.write(terrain, 1)
    cfg = {"title": "Explore the flight paths", "eyebrow": "SYNTHETIC DEMO • NOT REAL FLIGHT DATA",
           "subtitle": "An imaginary figure eight above an imaginary hill", "credit": "Made with Flightpath Photo Guide",
           "date": "Synthetic example • 1 January 2026", "mode": "paths",
           "region": {"bounds": [west, south, east, north]},
           "tracks": {"files": [f"data/{name}.kml"], "height_crs": "EPSG:9707"},
           "terrain": {"path": f"data/{name}.tif", "height_crs": "EPSG:9707"},
           "filters": {"max_gap_s": 6, "max_speed_m_s": 400, "max_vertical_rate_m_s": 120},
           "proximity": {"cell_size_m": 30, "sample_step_m": 10, "observer_height_m": 1.7, "bands_m": [300, 600, 1000]},
           "basemap": {"provider": "none"}, "style": {"smooth_paths": True},
           "viewpoints": [{"name": "Imaginary hilltop", "longitude": lon, "latitude": lat, "note": "Synthetic terrain; not a real spot"}],
           "output": {"directory": f"../output/{name}-paths", "width": 1400, "height": 1750}}
    (ROOT/f"{name}-paths.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    cfg["mode"] = "proximity"
    cfg["title"] = "Where did the flights come closest?"
    cfg["output"]["directory"] = f"../output/{name}-proximity"
    (ROOT/f"{name}-proximity.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")


if __name__ == "__main__":
    (ROOT/"data").mkdir(exist_ok=True)
    make_demo("demo-sf", -122.43, 37.80, "EPSG:32610")
    make_demo("demo-london", -.12, 51.50, "EPSG:32630")
    print("Generated synthetic SF and London examples, plus matching GeoTIFF terrain.")
