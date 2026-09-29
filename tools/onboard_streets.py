"""Ship an onboarded city's street layer with the web app, so its first forecast draws anywhere.

The deployed API keeps whatever city build its volume holds, and a city built by older code
carries other segment ids than the first forecast it serves: Pravesh then cannot colour a single
street and falls back to raw 30 m depth cells, which an authority cannot read. This writes the
exact payload ``GET /v1/city/<city>/layers/segments`` serves on the machine that made the forecast
(display names included) to ``apps/command/public/onboard/<city>-streets.json``, trimmed to what
the map draws and with coordinates rounded to about a metre. Pravesh reads it only when the
server's own layer does not match the forecast's street count.

    uv run python tools/onboard_streets.py --city chennai
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from varuna_api.routers.city import layer_path, named_segments_bytes

REPO = Path(__file__).resolve().parents[1]
KEEP = ("segment_id", "name", "display_name", "display_kind", "display_anchor", "class", "highway")


def _round(coords: object) -> object:
    if isinstance(coords, list) and coords and isinstance(coords[0], (int, float)):
        return [round(float(coords[0]), 5), round(float(coords[1]), 5)]
    if isinstance(coords, list):
        return [_round(c) for c in coords]
    return coords


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--city", default="chennai")
    args = parser.parse_args()
    payload = json.loads(named_segments_bytes(args.city, layer_path(args.city, "segments")))
    features = []
    for feature in payload["features"]:
        props = {k: v for k, v in feature["properties"].items() if k in KEEP and v is not None}
        geometry = feature["geometry"]
        features.append(
            {
                "type": "Feature",
                "properties": props,
                "geometry": {"type": geometry["type"], "coordinates": _round(geometry["coordinates"])},
            }
        )
    out = REPO / "apps" / "command" / "public" / "onboard" / f"{args.city}-streets.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    body = {"type": "FeatureCollection", "city": args.city, "features": features}
    out.write_text(json.dumps(body, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"Wrote {out} ({len(features)} streets, {out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
