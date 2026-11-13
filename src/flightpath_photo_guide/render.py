"""Portable Pillow poster rendering; smoothing is display-only."""
import hashlib
import json
import math
from pathlib import Path
import urllib.parse
import urllib.request

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageChops
from pyproj import Transformer
from rasterio.transform import from_bounds
from rasterio.warp import reproject, Resampling

from .config import local_path

BG = "#FAF8F2"
NAVY = "#152E45"
MUTED = "#596A79"
COLORS = [(223, 99, 40, 165), (255, 193, 76, 142), (68, 157, 179, 100)]


class PosterDraw:
    """Keep default-font punctuation portable without vendoring a system font."""
    def __init__(self, image, default_font):
        self.draw = ImageDraw.Draw(image)
        self.default_font = default_font

    def text_value(self, value):
        if not self.default_font:
            return str(value)
        return str(value).translate(str.maketrans({"•": " / ", "↑": "^", "–": "-", "—": "-", "’": "'", "‘": "'", "“": '"', "”": '"', "…": "...", "×": "x"}))

    def text(self, xy, value, **kwargs):
        return self.draw.text(xy, self.text_value(value), **kwargs)

    def textlength(self, value, **kwargs):
        return self.draw.textlength(self.text_value(value), **kwargs)

    def __getattr__(self, name):
        return getattr(self.draw, name)


def basemap(cfg, bounds, size):
    settings = cfg.get("basemap", {})
    if settings.get("provider", "none") == "none":
        return Image.new("RGB", size, "#E9EEEC"), "Coordinate grid • no basemap"
    if settings.get("provider") != "esri":
        raise ValueError("basemap.provider must be none or esri.")
    service = settings.get("service_url", "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer")
    attribution = settings.get("attribution")
    if not attribution:
        raise ValueError("An explicit basemap.attribution is required for an Esri export; include the service's licensors.")
    if not service.startswith("https://"):
        raise ValueError("Basemap service URL must use HTTPS.")
    cache = local_path(cfg, settings.get("cache", "cache")); cache.mkdir(parents=True, exist_ok=True)
    width, height = size
    image = Image.new("RGB", size)
    left, bottom, right, top = bounds
    # Bounded static exports, not tile prefetch. Each request is <=4096 pixels.
    for y in range(0, height, 4096):
        for x in range(0, width, 4096):
            w, h = min(4096, width-x), min(4096, height-y)
            box = [left+(right-left)*x/width, top-(top-bottom)*(y+h)/height,
                   left+(right-left)*(x+w)/width, top-(top-bottom)*y/height]
            key = hashlib.sha256(json.dumps([service, box, w, h]).encode()).hexdigest()
            path = cache/f"{key}.png"
            if not path.exists():
                query = urllib.parse.urlencode({"bbox": ",".join(map(str, box)), "bboxSR": 3857, "imageSR": 3857,
                                               "size": f"{w},{h}", "format": "png24", "f": "json"})
                request = urllib.request.Request(service.rstrip("/")+"/export?"+query,
                                                headers={"User-Agent": "FlightpathPhotoGuide/0.1 (static photography map)"})
                metadata = json.load(urllib.request.urlopen(request, timeout=60))
                if "error" in metadata:
                    raise ValueError(f"Basemap export failed: {metadata['error']}")
                href = metadata["href"]
                if not href.startswith("https://"):
                    raise ValueError("Basemap export returned a non-HTTPS image URL.")
                path.write_bytes(urllib.request.urlopen(href, timeout=60).read())
            with Image.open(path) as part:
                if part.size != (w, h):
                    raise ValueError("Basemap service returned an unexpected image size.")
                image.paste(part.convert("RGB"), (x, y))
    image = ImageEnhance.Color(image).enhance(.25)
    return Image.blend(image, Image.new("RGB", size, BG), .2), attribution


def smooth(points):
    points = np.asarray(points)
    keep = np.r_[True, np.linalg.norm(np.diff(points, axis=0), axis=1) > .01]
    points = points[keep]
    if len(points) < 3:
        return points
    edges = np.diff(points, axis=0)
    lengths = np.linalg.norm(edges, axis=1)
    directions = edges/lengths[:, None]
    tangents = np.vstack((directions[0], directions[:-1]+directions[1:], directions[-1]))
    tangents /= np.maximum(np.linalg.norm(tangents, axis=1)[:, None], 1e-9)
    output = []
    for i, (a, b) in enumerate(zip(points, points[1:])):
        h0 = min(lengths[i], lengths[max(0, i-1)])/4
        h1 = min(lengths[i], lengths[min(len(lengths)-1, i+1)])/4
        c, d = a+tangents[i]*h0, b-tangents[i+1]*h1
        u = np.linspace(0, 1, max(4, math.ceil(lengths[i]/3)))[:, None]
        curve = (1-u)**3*a+3*(1-u)**2*u*c+3*(1-u)*u**2*d+u**3*b
        output.extend(curve[:-1])
    output.append(points[-1])
    return np.array(output)


def render(cfg, runs_by_track, model, estimates, output):
    settings = cfg.get("output", {})
    width, height = int(settings.get("width", 1400)), int(settings.get("height", 1750))
    if not 700 <= width <= 7000 or not 900 <= height <= 9000 or width*height > 50_000_000:
        raise ValueError("Choose poster dimensions between 700×900 and 7000×9000, with at most 50 million pixels.")
    if height/width < 1.15:
        raise ValueError("Poster height must be at least 1.15 times its width.")
    scale = width/1400
    def n(value): return max(1, round(value*scale))
    font_path = cfg.get("style", {}).get("font_path")
    def font(size):
        return ImageFont.truetype(str(local_path(cfg, font_path)), n(size)) if font_path else ImageFont.load_default(size=n(size))
    image = Image.new("RGB", (width, height), BG)
    d = PosterDraw(image, not font_path)
    margin = n(64)
    d.text((margin, n(37)), cfg.get("eyebrow", "PHOTOGRAPHER’S FIELD NOTES"), font=font(23), fill=NAVY)
    title = cfg.get("title", "Where did these flights go?")
    lines, line = [], ""
    for word in title.split():
        candidate = f"{line} {word}".strip()
        if d.textlength(candidate, font=font(61)) > width-2*margin and line:
            lines.append(line); line = word
        else:
            line = candidate
    if line: lines.append(line)
    if len(lines) > 2:
        raise ValueError("Choose a shorter title (at most two lines).")
    for i, text in enumerate(lines):
        d.text((margin, n(86+77*i)), text, font=font(61), fill=NAVY)
    d.text((margin, n(248)), cfg.get("subtitle", "Recorded paths • routes can change"), font=font(23), fill=MUTED)
    d.text((margin, n(284)), str(cfg.get("date", "")), font=font(21), fill=NAVY)
    west, south, east, north = cfg["region"]["bounds"]
    WEB = Transformer.from_crs(4326, 3857, always_xy=True)
    left, bottom = WEB.transform(west, south); right, top = WEB.transform(east, north)
    available_w, available_h = width-2*margin, height-n(320+470)
    if available_h < n(180):
        raise ValueError("Poster is too short for the map, legend, and viewpoint key.")
    fit = min(available_w/(right-left), available_h/(top-bottom))
    mw, mh = max(1, round((right-left)*fit)), max(1, round((top-bottom)*fit))
    mx, my = margin+(available_w-mw)//2, n(320)+(available_h-mh)//2
    def xy(lon, lat):
        x, y = WEB.transform(lon, lat)
        return np.array([mx+(x-left)/(right-left)*mw, my+(top-y)/(top-bottom)*mh])
    background, attribution = basemap(cfg, (left, bottom, right, top), (mw, mh))
    bm = background.convert("RGBA")
    map_tf = from_bounds(left, bottom, right, top, mw, mh)
    colors = np.zeros((mh, mw, 4), np.uint8)
    bands = cfg["proximity"]["bands_m"]
    if model is not None:
        distances = np.full((mh, mw), np.nan, np.float32)
        reproject(model.distance.astype("float32"), distances, src_transform=model.transform, src_crs=model.crs,
                  dst_transform=map_tf, dst_crs="EPSG:3857", src_nodata=np.nan, dst_nodata=np.nan, resampling=Resampling.bilinear)
        lower = 0
        for upper, color in zip(bands, COLORS):
            colors[(distances >= lower) & (distances < upper)] = color
            lower = upper
        bm = Image.alpha_composite(bm, Image.fromarray(colors))
    if cfg.get("basemap", {}).get("provider", "none") == "none":
        grid = ImageDraw.Draw(bm)
        for lon in np.linspace(west, east, 6):
            a, b = xy(lon, south)-[mx, my], xy(lon, north)-[mx, my]
            grid.line([tuple(a), tuple(b)], fill="#CDD7D5", width=1)
        for lat in np.linspace(south, north, 6):
            a, b = xy(west, lat)-[mx, my], xy(east, lat)-[mx, my]
            grid.line([tuple(a), tuple(b)], fill="#CDD7D5", width=1)
    # Maximum opacity at crossings keeps overlapping loops from forming knots.
    alpha = Image.new("L", (mw, mh))
    for track, runs in runs_by_track:
        for run in runs:
            points = np.array([xy(f.lon, f.lat)-[mx, my] for f in run])
            if cfg.get("style", {}).get("smooth_paths", True): points = smooth(points)
            if len(points) < 2: continue
            mask = Image.new("L", (mw, mh)); md = ImageDraw.Draw(mask)
            distance = np.r_[0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
            taper = min(n(65), distance[-1]*.35)
            strength = np.clip(np.minimum(distance, distance[-1]-distance)/max(taper, .01), 0, 1)
            for i, (a, b) in enumerate(zip(points, points[1:])):
                power = float((strength[i]+strength[i+1])/2)
                md.line([tuple(a), tuple(b)], fill=round(125*power), width=max(1, round(n(2)*power)))
            alpha = ImageChops.lighter(alpha, mask)
    a = np.asarray(alpha).astype(float)
    a *= np.where(colors[:, :, 3] > 0, .45, 1)
    paths = np.zeros((mh, mw, 4), np.uint8); paths[:, :, :3] = (38, 79, 106); paths[:, :, 3] = a.astype("uint8")
    bm = Image.alpha_composite(bm, Image.fromarray(paths))
    image.paste(bm.convert("RGB"), (mx, my)); d = PosterDraw(image, not font_path)
    for lon, lat, name in [(p["longitude"], p["latitude"], p["name"]) for p in cfg.get("labels", [])]:
        d.text(tuple(xy(lon, lat)), name, font=font(21), fill=NAVY, anchor="mm", stroke_width=n(2), stroke_fill=BG)
    places = cfg.get("viewpoints", [])
    if len(places) > 18: raise ValueError("This poster layout supports at most 18 viewpoints.")
    for i, place in enumerate(places, 1):
        x, y = xy(place["longitude"], place["latitude"])
        if not (mx <= x <= mx+mw and my <= y <= my+mh): continue
        radius = n(14)
        d.ellipse((x-radius, y-radius, x+radius, y+radius), fill=NAVY, outline=BG, width=n(2))
        d.text((x, y), str(i), font=font(18), fill="white", anchor="mm")
    d.text((mx+n(10), my+n(10)), "N ↑", font=font(18), fill=NAVY, stroke_width=n(2), stroke_fill=BG)
    # A true-ground scale at the map's centre latitude.
    metres_per_pixel = (right-left)/mw*math.cos(math.radians((south+north)/2))
    desired = mw*.12*metres_per_pixel
    exponent = 10**math.floor(math.log10(max(desired, 1)))
    metres = max(v*exponent for v in (1, 2, 5, 10) if v*exponent <= max(desired, exponent))
    length = metres/metres_per_pixel
    d.line([(mx+n(15), my+mh-n(32)), (mx+n(15)+length, my+mh-n(32))], fill=NAVY, width=n(2))
    d.text((mx+n(15), my+mh-n(57)), f"{metres/1000:g} km" if metres >= 1000 else f"{metres:g} m", font=font(16), fill=NAVY)
    af = font(11)
    while d.textlength(attribution, font=af) > mw-n(20) and af.size > n(7):
        af = ImageFont.load_default(size=af.size-1)
    d.text((mx+mw-n(10), my+mh-n(8)), attribution, font=af, fill=MUTED, anchor="rs", stroke_width=n(1), stroke_fill=BG)
    y = height-n(440)
    legend_title = "ESTIMATED DISTANCE FROM A STANDING OBSERVER" if model is not None else "RECORDED FLIGHT PATHS • DISPLAY SMOOTHING ONLY"
    d.text((margin, y), legend_title, font=font(22), fill=NAVY)
    if model is not None:
        lower = 0
        for i, (upper, color) in enumerate(zip(bands, COLORS)):
            x = margin+i*(width-2*margin)/3
            d.rounded_rectangle((x, y+n(38), x+n(23), y+n(61)), radius=n(3), fill=color[:3])
            text = f"Under {upper:g} m" if lower == 0 else f"{lower:g}–{upper:g} m"
            d.text((x+n(34), y+n(38)), text, font=font(20), fill=NAVY); lower = upper
    else:
        d.text((margin, y+n(38)), "No altitude-based proximity estimates in this mode.", font=font(20), fill=MUTED)
    d.text((margin, y+n(83)), "NUMBERED PLACES • Access and visibility need local checking", font=font(20), fill=NAVY)
    for i, place in enumerate(places):
        col, row = i//6, i%6
        x = margin+col*(width-2*margin)/3; py = y+n(126+row*39)
        d.text((x, py), f"{i+1}  {place['name']}", font=font(20), fill=NAVY)
        note = place.get("note", "")
        if model is not None:
            estimate = estimates[i]["closest_visible_m"]
            note = (f"~{round(estimate/100)*100:,} m • " if estimate is not None else "Visibility unconfirmed • ")+note
        max_width = (width-2*margin)/3-n(18)
        while note and d.textlength(note, font=font(16)) > max_width: note = note[:-2]
        d.text((x, py+n(23)), note, font=font(16), fill=MUTED)
    d.text((margin, height-n(71)), "Historical observations, incomplete coverage, and reported heights are estimates. Future routes may differ.", font=font(17), fill=MUTED)
    d.text((margin, height-n(42)), "Display curves are illustrative. Proximity uses accepted straight segments; access is not inferred from color.", font=font(15), fill=MUTED)
    d.text((width-margin, height-n(17)), cfg.get("credit", ""), font=font(19), fill=NAVY, anchor="rs")
    image.save(output/"map.png", optimize=True)
    image.save(output/"map.jpg", quality=95, subsampling=0, progressive=True)
    return {"width": width, "height": height, "basemap_attribution": attribution}
