"""D-03 — no street name anywhere starts with "[".

95 of Mumbai's 21,296 segments were stored as the *text* of a Python list, and two of them are on
the KEM-to-Sion demo route - ``"['Jaganath Shankur Seth (Dadar TT) Flyover', 'Dadar TT flyover']"``
and ``"[\\"King's Circle Flyover\\", 'Eastern Express Highway']"`` both appear in the route the
ambulance takes - so the string reached an alert headline, the pump board and the route response.
The rule is applied twice - where the city writes the name and where a product reads it - so a
city already on disk is repaired without a rebuild, and the two copies are pinned to each other
here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import pytest
from varuna_products.depth import segment_name_aliases, segment_names
from varuna_products.names import split_names, street_name
from varuna_schemas.paths import city_dir

if TYPE_CHECKING:
    from pathlib import Path

CASES: tuple[tuple[object, tuple[str | None, tuple[str, ...]]], ...] = (
    # (stored value, (name printed, aliases kept))
    (None, (None, ())),
    (float("nan"), (None, ())),
    ("", (None, ())),
    ("   ", (None, ())),
    ("Dr Ambedkar Road", ("Dr Ambedkar Road", ())),
    ("  Sion Road  ", ("Sion Road", ())),
    # A live OSMnx fetch: a genuine list.
    (
        ["Dr Ambedkar Road", "Kalachowki Road"],
        ("Dr Ambedkar Road", ("Kalachowki Road",)),
    ),
    (("Tilak Bridge", "Tilak Road"), ("Tilak Bridge", ("Tilak Road",))),
    # The GeoPackage cache every later build reads: the same value as text. This is the shape
    # `city/mumbai` actually carries, and the one the old rule walked straight past.
    (
        "['Dr Ambedkar Road', 'Kalachowki Road']",
        ("Dr Ambedkar Road", ("Kalachowki Road",)),
    ),
    (
        "[\"King's Circle Flyover\", 'Eastern Express Highway']",
        ("King's Circle Flyover", ("Eastern Express Highway",)),
    ),
    ("['Milan Subway']", ("Milan Subway", ())),
    ("[]", (None, ())),
    # A name that merely starts with a bracket is a name, not a list, and must survive whole.
    ("[Closed] Link Road", ("[Closed] Link Road", ())),
    ("[unterminated", ("[unterminated", ())),
    # Two transliterations that strip to the same street: one name, no alias.
    ("['Sion Road', 'Sion Road ']", ("Sion Road", ())),
    # Blanks inside a list do not become an alias.
    ("['Gandhi Market', '', '  ']", ("Gandhi Market", ())),
)


@pytest.mark.parametrize(("value", "expected"), CASES)
def test_split_names(value: object, expected: tuple[str | None, tuple[str, ...]]) -> None:
    assert split_names(value) == expected
    assert street_name(value) == expected[0]


def test_the_read_rule_is_the_build_rule() -> None:
    """One rule, two call sites. `varuna-products` cannot depend on `varuna-city` (osmnx,
    whitebox and matplotlib for twelve lines of string handling), so the duplication is real and
    this is what stops the two drifting."""
    from varuna_city.segments import split_names as build_rule

    for value, expected in CASES:
        assert build_rule(value) == expected, value


def _city(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    pd.DataFrame(rows).to_parquet(tmp_path / "segments.parquet", index=False)
    return tmp_path


def test_a_city_built_before_the_fix_is_repaired_at_read_time(tmp_path: Path) -> None:
    """No rebuild: `city/mumbai` is ten minutes of open data and this is a string."""
    root = _city(
        tmp_path,
        [
            {"segment_id": "S1-000", "name": "['Dr Ambedkar Road', 'Kalachowki Road']"},
            {"segment_id": "S2-000", "name": "Sion Road"},
            {"segment_id": "S3-000", "name": None},
        ],
    )

    assert segment_names(root) == {"S1-000": "Dr Ambedkar Road", "S2-000": "Sion Road"}
    assert segment_name_aliases(root) == {"S1-000": ("Kalachowki Road",)}


def test_a_city_built_after_the_fix_carries_its_aliases_in_their_own_column(
    tmp_path: Path,
) -> None:
    root = _city(
        tmp_path,
        [
            {
                "segment_id": "S1-000",
                "name": "Dr Ambedkar Road",
                "name_aliases": ["Kalachowki Road"],
            },
            {"segment_id": "S2-000", "name": "Sion Road", "name_aliases": []},
        ],
    )

    assert segment_names(root) == {"S1-000": "Dr Ambedkar Road", "S2-000": "Sion Road"}
    assert segment_name_aliases(root) == {"S1-000": ("Kalachowki Road",)}


def test_no_name_in_the_built_mumbai_city_starts_with_a_bracket() -> None:
    """The acceptance criterion, on the real table rather than a fixture."""
    root = city_dir("mumbai")
    if not (root / "segments.parquet").is_file():
        pytest.skip("city/mumbai is not built; run `make city CITY=mumbai` first")

    names = segment_names(root)
    stored = pd.read_parquet(root / "segments.parquet", columns=["name"])["name"]
    was_broken = sum(1 for v in stored.dropna() if str(v).startswith("["))

    assert was_broken > 0, "this table is already clean; the test would prove nothing"
    assert [v for v in names.values() if v.startswith("[")] == []
    # Nothing thrown away: every repaired row kept its other name.
    assert len(segment_name_aliases(root)) == was_broken


DEMO_ROUTE_SEGMENTS = {
    # The two the KEM -> Sion ambulance actually crosses, measured by running
    # `varuna_route.router.plan((72.841, 19.003), (72.862, 19.041))` against the demo runs: the
    # naive and the VARUNA route both list them, which is how the string reached a jury screen.
    "S0-036": ("Jaganath Shankur Seth (Dadar TT) Flyover", ("Dadar TT flyover",)),
    "S0-281": ("King's Circle Flyover", ("Eastern Express Highway",)),
}


def test_the_demo_route_streets_read_as_streets() -> None:
    """The two of the 95 that are on KEM -> Sion, which is why this is not a unit test.

    Asserted through `segment_names`, the mapping every product says *where* with. The router
    has its own read of the same column (`varuna_route.graph`), which this wave does not own;
    until that one is normalised too the route response still prints the list.
    """
    root = city_dir("mumbai")
    if not (root / "segments.parquet").is_file():
        pytest.skip("city/mumbai is not built; run `make city CITY=mumbai` first")

    names = segment_names(root)
    aliases = segment_name_aliases(root)

    for segment_id, (primary, extra) in DEMO_ROUTE_SEGMENTS.items():
        assert names[segment_id] == primary
        assert aliases[segment_id] == extra
