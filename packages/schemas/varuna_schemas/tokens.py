"""Typed access to the design tokens in ``packages/tokens/tokens.json``.

The tokens file is the single source of truth for CSS, TypeScript and Python
(SPEC.md 6.2). This module never hard-codes a colour: every hex value is read
from the file so map pixels rendered in Python match the UI chips exactly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from varuna_schemas.paths import tokens_path

Hex = str
"""A ``#RRGGBB`` colour string exactly as written in ``tokens.json``."""


@dataclass(frozen=True, slots=True)
class DepthBand:
    """One band of the water-depth ramp.

    ``min_cm`` is inclusive, ``max_cm`` exclusive (``None`` = open-ended top band).
    """

    key: str
    hex: Hex
    min_cm: float
    max_cm: float | None
    label: str
    meaning: str

    @property
    def css_var(self) -> str:
        return f"--depth-{self.key}"

    def contains(self, cm: float) -> bool:
        if cm < self.min_cm:
            return False
        return self.max_cm is None or cm < self.max_cm


@dataclass(frozen=True, slots=True)
class DrainBand:
    """One band of the posterior-blockage (beta) ramp. ``min_beta`` inclusive, ``max_beta`` exclusive
    except for the top band, which includes 1.0."""

    key: str
    hex: Hex
    min_beta: float
    max_beta: float

    @property
    def css_var(self) -> str:
        return f"--drain-{self.key}"


@dataclass(frozen=True, slots=True)
class RainBand:
    """One band of the radar rain-rate ramp.

    ``min_mm_h`` is inclusive, ``max_mm_h`` exclusive (``None`` = open-ended top band).
    There is no band below 0.5 mm/h: a radar pixel under that is no echo, not a colour.
    """

    key: str
    hex: Hex
    min_mm_h: float
    max_mm_h: float | None
    label: str
    meaning: str

    @property
    def css_var(self) -> str:
        return f"--rain-{self.key}"

    def contains(self, mm_h: float) -> bool:
        if mm_h < self.min_mm_h:
            return False
        return self.max_mm_h is None or mm_h < self.max_mm_h


@dataclass(frozen=True, slots=True)
class ReachLevel:
    """A reachability isochrone level: the tide colour at a fixed opacity."""

    minutes: int
    hex: Hex
    opacity: float


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def load_tokens(path: Path | None = None) -> dict[str, Any]:
    """The parsed ``tokens.json`` (cached per path). Treat the result as read-only."""
    return _load(str((path or tokens_path()).resolve()))


def token(dotted: str, path: Path | None = None) -> Any:
    """Look up a token by dotted path, e.g. ``token("color.base.tide")`` -> ``"#2DD4BF"``.

    A node with a ``value`` key resolves to that value.
    """
    node: Any = load_tokens(path)
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            msg = f"Unknown token path {dotted!r} (failed at {part!r})"
            raise KeyError(msg)
        node = node[part]
    if isinstance(node, dict) and "value" in node:
        return node["value"]
    return node


def _values(section: dict[str, Any]) -> dict[str, Hex]:
    return {key: entry["value"] for key, entry in section.items()}


def base_colors(path: Path | None = None) -> dict[str, Hex]:
    """``ink``, ``deep``, ``well``, ``line``, ``line-strong``, ``text``, ``text-2``, ``text-3``,
    ``tide``, ``tide-soft``."""
    return _values(load_tokens(path)["color"]["base"])


def depth_bands(path: Path | None = None) -> list[DepthBand]:
    """The six depth bands in ascending order: ``dry``, ``1`` .. ``5``."""
    section = load_tokens(path)["color"]["depth"]
    bands = [
        DepthBand(
            key=key,
            hex=entry["value"],
            min_cm=float(entry["min_cm"]),
            max_cm=None if entry["max_cm"] is None else float(entry["max_cm"]),
            label=entry["label"],
            meaning=entry.get("meaning", ""),
        )
        for key, entry in section.items()
    ]
    return sorted(bands, key=lambda b: b.min_cm)


def drain_bands(path: Path | None = None) -> list[DrainBand]:
    """The four blockage bands in ascending order of beta."""
    section = load_tokens(path)["color"]["drain"]
    bands = [
        DrainBand(
            key=key,
            hex=entry["value"],
            min_beta=float(entry["min_beta"]),
            max_beta=float(entry["max_beta"]),
        )
        for key, entry in section.items()
    ]
    return sorted(bands, key=lambda b: b.min_beta)


def rain_bands(path: Path | None = None) -> list[RainBand]:
    """The six rain-rate bands in ascending order: ``1`` .. ``6`` (0.5 mm/h upwards)."""
    section = load_tokens(path)["color"]["rain"]
    bands = [
        RainBand(
            key=key,
            hex=entry["value"],
            min_mm_h=float(entry["min_mm_h"]),
            max_mm_h=None if entry["max_mm_h"] is None else float(entry["max_mm_h"]),
            label=entry["label"],
            meaning=entry.get("meaning", ""),
        )
        for key, entry in section.items()
    ]
    return sorted(bands, key=lambda b: b.min_mm_h)


def semantic_colors(path: Path | None = None) -> dict[str, Hex]:
    """``surcharge``, ``naive``, ``truth``, ``danger``."""
    return _values(load_tokens(path)["color"]["semantic"])


def status_colors(path: Path | None = None) -> dict[str, Hex]:
    """Mode-banner colours: ``live``, ``replay``, ``baked``, ``degraded``."""
    return _values(load_tokens(path)["color"]["status"])


def obs_colors(path: Path | None = None) -> dict[str, Hex]:
    """Observation-type colours: ``traffic``, ``report``, ``sensor``, ``cctv``, ``sar``."""
    return _values(load_tokens(path)["color"]["obs"])


def chart_colors(path: Path | None = None) -> list[Hex]:
    """``--chart-1`` .. ``--chart-5`` in order."""
    section = load_tokens(path)["color"]["chart"]
    return [section[key]["value"] for key in sorted(section, key=int)]


def reach_levels(path: Path | None = None) -> list[ReachLevel]:
    """Isochrone levels (5, 10, 15 min) with their opacities."""
    section = load_tokens(path)["color"]["reach"]
    levels = [
        ReachLevel(
            minutes=int(entry["minutes"]), hex=entry["value"], opacity=float(entry["opacity"])
        )
        for entry in section.values()
    ]
    return sorted(levels, key=lambda lvl: lvl.minutes)


def probability_thresholds_cm(path: Path | None = None) -> list[int]:
    """Threshold choices for probability mode: ``[15, 30, 45, 60]``."""
    return [int(v) for v in load_tokens(path)["probability"]["thresholds_cm"]]


def probability_min_opacity(path: Path | None = None) -> float:
    """Floor for segment opacity in probability mode so nothing vanishes (0.15)."""
    return float(load_tokens(path)["probability"]["min_opacity"])


def depth_layer_opacity(path: Path | None = None) -> float:
    """Opacity of the depth raster on the map (0.55)."""
    return float(load_tokens(path)["raster"]["depth_layer_opacity"])


def font_families(path: Path | None = None) -> dict[str, str]:
    """``display``, ``sans``, ``mono`` family names."""
    fonts = load_tokens(path)["font"]
    return {key: fonts[key]["family"] for key in ("display", "sans", "mono")}


__all__ = [
    "DepthBand",
    "DrainBand",
    "Hex",
    "RainBand",
    "ReachLevel",
    "base_colors",
    "chart_colors",
    "depth_bands",
    "depth_layer_opacity",
    "drain_bands",
    "font_families",
    "load_tokens",
    "obs_colors",
    "probability_min_opacity",
    "probability_thresholds_cm",
    "rain_bands",
    "reach_levels",
    "semantic_colors",
    "status_colors",
    "token",
]
