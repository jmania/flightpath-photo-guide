# Configuration

Copy a generated demo YAML and edit it. Paths are relative to the YAML file.

## Modes and coverage

`mode: paths` needs only KML. It does not claim low heights or ground proximity.
`gx:Track` needs matching `when` and `gx:coord` entries. Untimed KML `LineString`
geometry is supported only in paths mode. No CSV/GPX/GeoJSON flight import is
implemented in this release. Multiple KML geometries remain separate; overlapping
exports are not counted as separate aircraft or merged into a probability map.

`mode: proximity` also requires `terrain.path` (a georeferenced, metres-valued,
single-band GeoTIFF), `terrain.height_crs`, and `tracks.height_crs`. Local regions
are supported between 80°S and 84°N, up to 12° across. Antimeridian crossing is
not supported. Auto-selected UTM is intended for local photography areas.

## Heights

Use full geographic 3D/compound CRS identifiers with vertical units in metres.
Examples: `EPSG:9707` for WGS 84 + EGM96 heights, `EPSG:4979` for WGS 84
ellipsoidal heights, or `EPSG:6349` for NAD83(2011) + NAVD88 height. Declare the
actual source references, not merely a reference that makes the tool run.
Configuring the wrong reference can produce incorrect results even if the CRS
transformation succeeds. KML coordinates always contain longitude, latitude,
and (when present) metres of altitude.

When the DEM and aircraft heights differ, the best available non-ballpark PROJ
operation is required. Install the necessary transformation grids into your PROJ
data directory, or supply already aligned terrain. Grids are not downloaded
automatically. Barometric altitude is not supported for proximity. This tool
does not infer geometric height from weather or pressure settings.

## Filtering and explicit exclusions

`filters.max_gap_s` defaults to 6 seconds. Larger gaps split lines and never enter
proximity. `max_speed_m_s` defaults to 400, `max_vertical_rate_m_s` to 120, and
`speed_check_gap_s` to 12. These configurable thresholds conservatively reject
both ends of implausible short transitions; exceeding them is not proof of a
reporting error or an impossible fighter maneuver.

Each `exclude` entry requires a `track` name matching the KML Placemark, a `start`
timestamp, optional inclusive `end`, and a `reason`. Timestamps require `Z` or a
UTC offset. Rejecting an interior fix breaks the run instead of connecting over
it. For multiple geometries, later geometries are named `Placemark name/2`, etc.

## Analysis

`proximity.cell_size_m`: raster spacing (default 20 m).
`sample_step_m`: maximum 3D spacing along accepted segments (default 10 m).
`observer_height_m`: eye height above terrain (default 1.7 m).
`bands_m`: one to three increasing distance limits (default 300, 600, 1000).
Analysis is limited to four million cells. For larger regions, use a coarser grid.

No building source is downloaded automatically. Optional `terrain.buildings`
accepts WGS84 GeoJSON footprints with `roof_height_m`, an absolute roof elevation
already in the **flight height reference**, not a building height above ground.
Roofs screen visibility and are not treated as accessible standing locations.

Optional `terrain.land_mask` accepts a WGS84 GeoJSON FeatureCollection of polygons
where ground coloring is allowed. Without it, all finite DEM cells are potential
terrain, including water if the supplied DEM contains ocean values. The tool
does not infer shorelines or public access from elevation. Coastal users should
supply a land mask or a DEM whose water cells are NoData.

## Presentation

`title`, `subtitle`, `eyebrow`, `date`, `credit`: poster text. Titles support two
lines. `region.bounds` is `[west, south, east, north]` in degrees. The map fits the
poster without stretching geographic proportions. Up to 18 `viewpoints` can have
`name`, `longitude`, `latitude`, and `note`. An explicit `extra_height_m` models
an elevated observer, but does not establish access, windows, or visibility.

`labels`: optional region names with `name`, `longitude`, `latitude`.
`style.smooth_paths`: display-only curves (default true). They do not bridge gaps.
`style.font_path`: optional user-supplied font; the default needs no OS fonts.
`output.directory`, `width`, `height`: output settings. Posters need portrait
proportions (height at least 1.15× width), up to 50 million pixels.

## Background maps

`basemap.provider: none` creates an offline coordinate-grid background.
`provider: esri` requests bounded static map exports, with a local cache. Set
`basemap.attribution` to the service's required credits. `service_url` can point
to a compatible HTTPS MapServer; verify its terms. The built-in default is Esri
World Street Map. Network errors stop the build instead of silently substituting
a background. Never commit downloaded basemap caches as if they were MIT data.
