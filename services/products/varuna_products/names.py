"""Street names, read back from a city that may have been built before they were normalised.

OSM gives a way several names often enough that it is not an edge case, and OSMnx hands that
over as a Python list. `varuna_city.segments.split_names` picks one and keeps the rest from the
build that produced ``segments.parquet`` - but `city/mumbai` was built long before it did, and
95 of its 21,296 segments carry the *text* ``"['Dr Ambedkar Road', 'Kalachowki Road']"`` as
their name. Two of them are on the KEM-to-Sion demo route, so the string reaches a route
response, the road-conditions feed and an alert headline, none of which is a sentence a ward
officer can act on.

Rebuilding Mumbai is ten minutes and a different kind of change, so the same rule is applied
where the name is *read*: an already-built city is repaired in memory, every cycle, with no
rebuild. Once a city is rebuilt this module finds nothing to do, which is the intended end
state rather than a duplicate of the build-time rule.

The two implementations are pinned to each other by
``services/products/tests/test_names.py::test_the_read_rule_is_the_build_rule`` - one table of
cases run through both - because the only thing worse than one normaliser is two that disagree.
The duplication exists because `varuna-products` does not depend on `varuna-city` (osmnx,
whitebox and matplotlib for a twelve-line string rule), and the shared package both *do* depend
on, `varuna-schemas`, is another chunk's file this wave.
"""

from __future__ import annotations

import ast

__all__ = ["split_names", "street_name"]


def split_names(value: object) -> tuple[str | None, tuple[str, ...]]:
    """A stored name as ``(the name to print, the other names the way carries)``.

    The first name is taken rather than a joined one invented: "Dr Ambedkar Road" is a street a
    person can find, "Dr Ambedkar Road / Kalachowki Road" is a string VARUNA made up. A value
    that merely starts with a bracket without being a list literal ("[Closed] Link Road") is a
    name and is left alone.
    """
    names = [text for text in (_one(item) for item in _candidates(value)) if text]
    unique = list(dict.fromkeys(names))
    if not unique:
        return None, ()
    return unique[0], tuple(unique[1:])


def street_name(value: object) -> str | None:
    """The name a product prints, or ``None`` when the way is unnamed."""
    return split_names(value)[0]


def _candidates(value: object) -> list[object]:
    if isinstance(value, list | tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                return [value]
            if isinstance(parsed, list | tuple):
                return list(parsed)
        return [value]
    return [value]


def _one(value: object) -> str | None:
    if value is None or (isinstance(value, float) and value != value):
        return None
    text = str(value).strip()
    return text or None
