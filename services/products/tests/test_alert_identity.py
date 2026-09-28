"""The cycle-independent identity of an alert (`alert_identity`).

An alert id embeds the run that raised it - ``VARUNA-{run_id}-{key}-{level}`` in
:func:`varuna_products.alerts._alert_from_series` - so no two cycles share one. Measured on the
seven baked demo cycles: **zero ids are shared between any two consecutive cycles**, while six
identities carry from 02:40Z to 03:10Z and twelve from 03:10Z to 03:40Z. An officer who
acknowledges "Danda Avenue, severe" has acknowledged a situation, not a string, and the situation
outlives the cycle that named it.

This module is the Python half of a pair. ``apps/command/lib/alert-identity.ts`` is the other, and
the two must produce the same string for the same alert or the desk and the console disagree about
what has been seen. The cases below are deliberately the same three as
``apps/command/lib/alert-identity.test.ts``, on the same fixtures, so a change to one side that is
not made to the other fails here.
"""

from __future__ import annotations

from typing import Any

from varuna_products.alerts import alert_identity

# The two cycles as `apps/command/lib/alert-identity.test.ts` fixes them: the same street at the
# same level, named by two different runs.
AT_0640: dict[str, Any] = {
    "id": "VARUNA-MUM-20190702T0110Z-SKY1.0-TWIN1.0-FLASH0.1-BAKED-STREET-1105-SEVERE",
    "scope": "segment",
    "hotspot_id": None,
    "area_desc": "V B Worlikar Marg",
    "level": "severe",
}
AT_0840: dict[str, Any] = {
    "id": "VARUNA-MUM-20190702T0310Z-SKY1.0-TWIN1.0-FLASH0.1-BAKED-STREET-1105-SEVERE",
    "scope": "segment",
    "hotspot_id": None,
    "area_desc": "V B Worlikar Marg",
    "level": "severe",
}


def test_the_run_id_is_ignored_so_one_street_at_one_level_is_one_identity() -> None:
    assert alert_identity(AT_0640) == alert_identity(AT_0840)
    assert AT_0640["id"] != AT_0840["id"], "the ids differ; that is the whole problem"


def test_levels_scopes_and_places_are_separated() -> None:
    """Four changes, four identities. A street stepping up a level is a new situation."""
    identities = {
        alert_identity(AT_0640),
        alert_identity({**AT_0640, "level": "moderate"}),
        alert_identity({**AT_0640, "scope": "hotspot"}),
        alert_identity({**AT_0640, "area_desc": "Dr Ambedkar Road"}),
    }

    assert len(identities) == 4


def test_a_registered_hotspot_is_named_by_its_id_rather_than_its_area_text() -> None:
    """`area_desc` is prose and carries the ward; the register's id is the stable name."""
    hindmata = {
        "scope": "hotspot",
        "hotspot_id": "hindmata",
        "area_desc": "Hindmata junction",
        "level": "severe",
    }

    assert alert_identity({**hindmata, "area_desc": "Ward F/South, Hindmata"}) == alert_identity(
        hindmata
    )


def test_a_street_alert_falls_back_to_its_area_text() -> None:
    """`scope_id` is None on every street alert, so `area_desc` - the street name - is the place."""
    assert alert_identity(AT_0640) == "segment|V B Worlikar Marg|severe"


def test_the_shape_is_the_one_the_typescript_half_builds() -> None:
    """Pinned literally: this string crosses the wire into the desk's grouping (ADR-0062)."""
    assert (
        alert_identity(
            {"scope": "hotspot", "hotspot_id": "hindmata", "area_desc": "x", "level": "watch"}
        )
        == "hotspot|hindmata|watch"
    )
