from pathlib import Path
import importlib.util
import json
import tempfile
import unittest

import numpy as np
import rasterio
import yaml
from PIL import Image

from flightpath_photo_guide.cli import build

ROOT = Path(__file__).resolve().parents[1]


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("make_demo", ROOT/"examples"/"make_demo.py")
        cls.demo = importlib.util.module_from_spec(spec); spec.loader.exec_module(cls.demo)

    def test_two_regions_build_offline_with_distinct_projections(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/"data").mkdir()
            self.demo.ROOT = root
            for name, lon, lat, crs in [("sf", -122.43, 37.8, 32610), ("london", -.12, 51.5, 32630)]:
                self.demo.make_demo(name, lon, lat, crs)
                for mode in ("paths", "proximity"):
                    path = root/f"{name}-{mode}.yaml"
                    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
                    cfg["output"].update({"directory": f"out-{name}-{mode}", "width": 980, "height": 1225})
                    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
                    out = build(path)
                    report = json.loads((out/"report.json").read_text(encoding="utf-8"))
                    self.assertGreater(report["accepted_segments"], 90)
                    with Image.open(out/"map.png") as image:
                        self.assertEqual(image.size, (980, 1225))
                    if mode == "proximity":
                        self.assertEqual(report["metric_crs"], f"EPSG:{crs}")
                        with rasterio.open(out/"proximity.tif") as ds:
                            self.assertTrue(np.any(np.isfinite(ds.read(1))))

    def test_display_smoothing_cannot_change_proximity_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/"data").mkdir()
            self.demo.ROOT = root; self.demo.make_demo("demo", -.12, 51.5, 32630)
            path = root/"demo-proximity.yaml"; cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
            arrays = []
            for smooth in (False, True):
                cfg["style"]["smooth_paths"] = smooth
                cfg["output"].update({"directory": f"out-{smooth}", "width": 980, "height": 1225})
                path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
                out = build(path)
                with rasterio.open(out/"proximity.tif") as ds:
                    arrays.append(ds.read(1))
            np.testing.assert_array_equal(*arrays)


if __name__ == "__main__":
    unittest.main()
