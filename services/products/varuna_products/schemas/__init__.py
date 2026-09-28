"""Standards schemas VARUNA's products are checked against, vendored so checks run offline.

**CAP 1.2** (SPEC.md 7.5 and 11.10). ``CAP-v1.2.xsd`` is the OASIS schema, byte for byte as
published; ``SOURCE.txt`` beside it records the URL it came from, the retrieval date and its
sha256 (rule 7). It is vendored rather than fetched because the test suite runs with
``VARUNA_OFFLINE=1``, and a validation test that needs the network is a test that silently stops
running.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import xmlschema

__all__ = ["CAP_XSD", "CAP_XSD_SHA256", "cap_schema", "validate_cap"]

CAP_XSD = Path(__file__).with_name("CAP-v1.2.xsd")
"""The vendored OASIS CAP 1.2 schema."""

CAP_XSD_SHA256 = "b7798ef25868b068c97b268bda02d067c7d4ba9373adc5638bf37105804ee723"
"""sha256 of ``CAP-v1.2.xsd`` as retrieved on 2026-09-14; mirrored in ``SOURCE.txt``."""


@lru_cache(maxsize=1)
def cap_schema() -> xmlschema.XMLSchema:
    """The CAP 1.2 schema, parsed once per process from the vendored file only."""
    return xmlschema.XMLSchema(str(CAP_XSD))


def validate_cap(document: str | Path) -> list[str]:
    """Every CAP 1.2 schema violation in a document, as ``"<path>: <reason>"``; empty when valid.

    ``document`` is CAP XML text or a path to a ``.cap.xml`` file. A list rather than a boolean,
    so a failing test names the element that broke instead of reporting only that something did.
    """
    source = document.read_text(encoding="utf-8") if isinstance(document, Path) else document
    return [f"{error.path or '/'}: {error.reason}" for error in cap_schema().iter_errors(source)]
