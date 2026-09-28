from __future__ import annotations

import json
from itertools import pairwise

from varuna_schemas import tokens
from varuna_schemas.paths import tokens_path


def _raw() -> dict:
    return json.loads(tokens_path().read_text(encoding="utf-8"))


def test_base_colors_match_file() -> None:
    raw = _raw()["color"]["base"]
    got = tokens.base_colors()
    assert got == {key: entry["value"] for key, entry in raw.items()}
    assert got["ink"] == "#0A1020" and got["tide"] == "#2DD4BF"


def test_depth_bands_match_file_and_are_contiguous() -> None:
    raw = _raw()["color"]["depth"]
    bands = tokens.depth_bands()
    assert [b.key for b in bands] == ["dry", "1", "2", "3", "4", "5"]
    assert [b.hex for b in bands] == [raw[b.key]["value"] for b in bands]
    assert [b.min_cm for b in bands] == [0, 5, 15, 30, 45, 60]
    for lower, upper in pairwise(bands):
        assert lower.max_cm == upper.min_cm
    assert bands[-1].max_cm is None
    assert bands[4].contains(45) and not bands[4].contains(60)
    assert bands[0].css_var == "--depth-dry" and bands[3].css_var == "--depth-3"


def test_drain_status_obs_chart_reach() -> None:
    raw = _raw()["color"]
    drains = tokens.drain_bands()
    assert [d.hex for d in drains] == [raw["drain"][d.key]["value"] for d in drains]
    assert [d.min_beta for d in drains] == [0.0, 0.25, 0.5, 0.75]
    assert tokens.status_colors() == {k: v["value"] for k, v in raw["status"].items()}
    assert tokens.obs_colors() == {k: v["value"] for k, v in raw["obs"].items()}
    assert tokens.semantic_colors()["danger"] == raw["semantic"]["danger"]["value"]
    assert tokens.chart_colors() == [raw["chart"][str(i)]["value"] for i in range(1, 6)]
    assert [lvl.minutes for lvl in tokens.reach_levels()] == [5, 10, 15]
    assert [lvl.opacity for lvl in tokens.reach_levels()] == [0.45, 0.28, 0.14]


def test_scalars_and_fonts() -> None:
    assert tokens.probability_thresholds_cm() == [15, 30, 45, 60]
    assert tokens.probability_min_opacity() == 0.15
    assert tokens.depth_layer_opacity() == 0.55
    fonts = tokens.font_families()
    assert fonts == {"display": "Bricolage Grotesque", "sans": "Geist Sans", "mono": "Geist Mono"}
    assert tokens.token("color.base.tide") == "#2DD4BF"
    assert tokens.token("radius.panel") == 12


def test_load_tokens_is_cached() -> None:
    assert tokens.load_tokens() is tokens.load_tokens()
