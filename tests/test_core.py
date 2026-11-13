from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

import numpy as np
from pyproj import Transformer
import rasterio
from rasterio.transform import from_origin

from flightpath_photo_guide.proximity import calculate, closest_to_segments, height_crs, local_metric_crs, viewpoint_estimates
from flightpath_photo_guide.tracks import Fix, Track, load_kml, selected_runs, timestamp


class TrackTests(unittest.TestCase):
    def track(self, altitudes=(650, 650, 650, 650), times=(0, 5, 10, 15)):
        return Track("test", "fixture.kml", [Fix(-.12+i*.0001, 51.5, z, t) for i, (z, t) in enumerate(zip(altitudes, times))], "absolute")

    def test_long_gap_splits_instead_of_inventing_connection(self):
        runs, _ = selected_runs(self.track(times=(0, 5, 100, 105)), {}, [], True)
        self.assertEqual([[f.time for f in run] for run in runs], [[0, 5], [100, 105]])

    def test_manual_exclusion_does_not_bridge_over_rejected_fix(self):
        rule = {"track": "test", "start": "1970-01-01T00:00:05Z", "reason": "Known conflicting report"}
        runs, rejected = selected_runs(self.track(), {}, [rule], True)
        self.assertEqual([[f.time for f in run] for run in runs], [[10, 15]])
        self.assertIn("Known conflicting report", rejected[0]["reason"])

    def test_height_discontinuity_does_not_create_hotspot(self):
        runs, rejected = selected_runs(self.track(altitudes=(650, 650, 2000, 2000)), {}, [], True)
        self.assertEqual(runs, [])
        self.assertEqual(len(rejected), 2)

    def test_harmless_near_duplicate_does_not_split_line(self):
        track = Track("test", "fixture.kml", [Fix(-.12, 51.5, 650, 0), Fix(-.12, 51.5, 650, .1), Fix(-.119, 51.5, 650, 5), Fix(-.118, 51.5, 650, 10)], "absolute")
        runs, _ = selected_runs(track, {}, [], True)
        self.assertEqual([[f.time for f in run] for run in runs], [[0, 5, 10]])

    def test_conflicting_near_duplicate_is_rejected(self):
        track = Track("test", "fixture.kml", [Fix(-.12, 51.5, 650, 0), Fix(-.12, 51.5, 100, .1), Fix(-.119, 51.5, 650, 5), Fix(-.118, 51.5, 650, 10)], "absolute")
        runs, rejected = selected_runs(track, {}, [], True)
        self.assertEqual([[f.time for f in run] for run in runs], [[5, 10]])
        self.assertGreaterEqual(len(rejected), 2)

    def test_relative_heights_are_not_absolute_proximity(self):
        track = self.track(); track.altitude_mode = "relativeToGround"
        with self.assertRaisesRegex(ValueError, "absolute"):
            selected_runs(track, {}, [], True)

    def test_untimed_lines_only_work_for_paths(self):
        track = self.track(); track.fixes = [Fix(f.lon, f.lat, f.altitude, None) for f in track.fixes]
        self.assertTrue(selected_runs(track, {}, [], False)[0])
        with self.assertRaisesRegex(ValueError, "timestamped"):
            selected_runs(track, {}, [], True)

    def test_timestamp_requires_timezone(self):
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            timestamp("2026-01-01T12:00:00")

    def test_bad_kml_time_coordinate_count_is_actionable(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/"test.kml"
            p.write_text('<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:gx="http://www.google.com/kml/ext/2.2"><Placemark><gx:Track><when>2026-01-01T12:00:00Z</when></gx:Track></Placemark></kml>', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "counts differ"):
                load_kml([p])


class DistanceTests(unittest.TestCase):
    def test_exact_3d_distance_and_endpoint_clamping(self):
        distances, targets = closest_to_segments([[5, 3, 4], [15, 0, 0]], np.array([[0., 0, 0]]), np.array([[10., 0, 0]]))
        np.testing.assert_allclose(distances[:, 0], [5, 5])
        np.testing.assert_allclose(targets[:, 0], [[5, 0, 0], [10, 0, 0]])

    def test_projection_is_not_hardcoded_to_san_francisco(self):
        self.assertEqual(local_metric_crs([-122.5, 37.7, -122.3, 37.9]).to_epsg(), 32610)
        self.assertEqual(local_metric_crs([-.2, 51.4, 0, 51.6]).to_epsg(), 32630)
        self.assertEqual(local_metric_crs([151.1, -34, 151.3, -33.8]).to_epsg(), 32756)

    def test_unknown_or_two_dimensional_height_reference_rejected(self):
        with self.assertRaisesRegex(ValueError, "required"):
            height_crs(None, "tracks.height_crs")
        with self.assertRaisesRegex(ValueError, "3D/compound"):
            height_crs("EPSG:4326", "tracks.height_crs")

    def test_actual_terrain_changes_observer_distance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xy = Transformer.from_crs(4326, 32630, always_xy=True)
            ll = Transformer.from_crs(32630, 4326, always_xy=True)
            cx, cy = xy.transform(-.12, 51.5)
            lower = ll.transform(cx-600, cy-600); upper = ll.transform(cx+600, cy+600)
            a, b = ll.transform(cx-100, cy), ll.transform(cx+100, cy)
            track = Track("test", "fixture.kml", [Fix(*a, 650, 0), Fix(*b, 650, 5)], "absolute")
            cfg = {"_root": root, "region": {"bounds": [*lower, *upper]}, "tracks": {"height_crs": "EPSG:9707"},
                   "terrain": {"path": "terrain.tif", "height_crs": "EPSG:9707"},
                   "proximity": {"cell_size_m": 20, "observer_height_m": 1.7},
                   "viewpoints": [{"name": "eye", "longitude": -.12, "latitude": 51.5}]}
            results = []
            for elevation in (100, 400):
                with rasterio.open(root/"terrain.tif", "w", driver="GTiff", width=100, height=100, count=1,
                                   dtype="float32", crs="EPSG:32630", transform=from_origin(cx-1000, cy+1000, 20, 20), nodata=-9999) as ds:
                    ds.write(np.full((100, 100), elevation, np.float32), 1)
                model = calculate(cfg, [(track, [track.fixes])], root)
                results.append(viewpoint_estimates(cfg, model)[0]["closest_visible_m"])
            np.testing.assert_allclose(results, [548.3, 248.3], atol=.01)
            with rasterio.open(root/"terrain.tif", "r+") as ds:
                ds.write(np.full((100, 100), 700, np.float32), 1)
            with self.assertRaisesRegex(ValueError, "No continuous segments"):
                calculate(cfg, [(track, [track.fixes])], root)


if __name__ == "__main__":
    unittest.main()
