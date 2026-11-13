# What the estimates mean

For each terrain cell, place an observer at terrain height plus the configured
eye height. Estimate the shortest 3D Euclidean distance to accepted straight
flight segments, sampled at no more than `sample_step_m` metres. Local horizontal
coordinates use an automatically chosen WGS84 UTM zone. This is a local planar
approximation; it does not model Earth curvature over long distances.

Aircraft altitudes and terrain must share a known vertical reference. Explicit
CRS transformations align height values; the transformation's numerical accuracy
does not establish aircraft measurement accuracy. No pressure-altitude correction
or independent aircraft height verification is performed.

Short telemetry gaps are permitted only within the configured time threshold.
Configured manual exclusions, near-simultaneous conflicting reports, speed/rate
outliers, missing terrain, and below-surface segments interrupt the usable path.
Isolated reports are excluded from proximity to avoid single-reading circular
hotspots. Rejected intermediate reports are never bridged over.

The proximity raster uses a sampled nearest-neighbor query. Its numerical
sampling error is bounded by half the maximum spacing along a straight segment;
this does not bound flight measurement or terrain error. Named viewpoints use
analytic closest points to segments. Up to the nearest 100 candidates are
screened against terrain and optional building roofs with a sampled line-of-sight
test. The first 10 m near the observer are excluded to reduce DEM discretization
artifacts. A missing visible result is not proof no view exists. Trees, smaller
buildings, fences, crowds, glass, weather, and live closures are not modeled.

Map colors are geometric proximity, not a probability, aircraft count, guarantee
of public access, or prediction of a future route. Unshaded cells may be outside
DEM coverage or masks, inside modeled buildings, or outside distance bands; they
must not automatically be read as a confirmed absence of nearby flights.

Smooth display curves are generated separately in the renderer. Neither the
smooth curves nor their fading ends affect the distance analysis. GeoJSON exports
contain accepted observed straight segments, not smoothed geometry.

## The historical Blue Angels example

The README screenshot came from the original, hand-tuned SF analysis. It included
special modeling of a Golden Gate Bridge sidewalk and curated annotations.
Those are not automatically reproduced by the generic tool. Its published output
also included a departure-side East Bay track outside the proximity analysis
extent, clearly labeled as route context. A runway takeoff was not recorded.

The source workspace's original altitude review removed two isolated low reports
and an ambiguous SoMa branch. The reusable SF YAML preserves their timestamps
and reasons. Its new default filtering (including isolated-fix rejection and a
vertical-rate limit) is more conservative, so its results need not exactly match
the historical map. The bundled demos are synthetic and cannot support real
world viewing recommendations.
