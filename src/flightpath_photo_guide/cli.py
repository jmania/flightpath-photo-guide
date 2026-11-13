import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

from . import __version__
from .config import load_config, local_path
from .tracks import load_kml, selected_runs, geojson_features


def build(config_path):
    cfg = load_config(config_path)
    out = local_path(cfg, cfg.get("output", {}).get("directory", "output"))
    out.mkdir(parents=True, exist_ok=True)
    tracks = load_kml(cfg["_files"])
    accepted, rejected = [], []
    for track in tracks:
        runs, bad = selected_runs(track, cfg.get("filters", {}), cfg.get("exclude", []), cfg["mode"] == "proximity")
        accepted.append((track, runs)); rejected.extend(bad)
    if not any(runs for _, runs in accepted):
        raise ValueError("No continuous track runs remain after filtering.")
    from .proximity import calculate, viewpoint_estimates
    from .render import render
    model = calculate(cfg, accepted, out) if cfg["mode"] == "proximity" else None
    if model is not None:
        accepted = model.runs
    estimates = viewpoint_estimates(cfg, model) if model is not None else []
    image = render(cfg, accepted, model, estimates, out)
    (out/"tracks.geojson").write_text(json.dumps(geojson_features(accepted), indent=2, allow_nan=False), encoding="utf-8")
    if estimates:
        with (out/"viewpoints.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(estimates[0]))
            writer.writeheader(); writer.writerows(estimates)
    (out/"rejected-fixes.json").write_text(json.dumps(rejected, indent=2, allow_nan=False), encoding="utf-8")
    report = {"version": __version__, "mode": cfg["mode"], "bounds": cfg["region"]["bounds"],
              "inputs": [{"file": Path(p).name, "sha256": hashlib.sha256(Path(p).read_bytes()).hexdigest()} for p in cfg["_files"]],
              "track_geometries": len(tracks), "accepted_segments": sum(len(run)-1 for _, runs in accepted for run in runs),
              "rejected_fix_events": len(rejected), "exclusions": cfg.get("exclude", []),
              "display_smoothing": cfg.get("style", {}).get("smooth_paths", True),
              "metric_crs": model.crs.to_string() if model else None,
              "vertical_operation": model.vertical_operation if model else None,
              "terrain_or_coverage_rejected_segments": model.rejected_segments if model else 0,
              "sample_step_m": cfg["proximity"].get("sample_step_m", 10) if model else None,
              "max_gap_s": cfg.get("filters", {}).get("max_gap_s", 6),
              "image": image,
              "limits": "No future-route prediction, complete building coverage, or public-access guarantee. KML identifiers are not independently verified aircraft identities. Smoothed display curves do not enter distances. Proximity excludes isolated fixes."}
    (out/"report.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build photography maps from recorded KML flight tracks.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("build", help="Generate map images and auditable data exports.")
    command.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        output = build(args.config)
    except (ValueError, OSError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Saved map and analysis to {output}")
    return 0
