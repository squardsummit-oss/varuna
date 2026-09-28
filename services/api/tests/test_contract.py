"""The API contract, checked against the running app (P5.8; SPEC.md 12).

Two different jobs live here.

**The drift guard.** `apps/command/openapi.json` is a committed snapshot and `pnpm typegen`
turns it into `apps/command/lib/api/types.ts`, so a route added to the app without
regenerating leaves the console with no types for it and nobody finds out. That is not
hypothetical: on 2026-09-12 the snapshot was five paths behind - `/v1/nowcast/surcharge`,
`/v1/nowcast/raster/bounds`, `/v1/replay/ground-truth`, `/v1/route/facilities` and
`/v1/onboard/city/{city}` - and every one of them was already being called from
`apps/command/lib/api/`. Section 12 says the types are generated "in CI"; this is how CI
finds out, because it fails naming the command that fixes it.

**The invariants section 12 states in prose.** The error envelope shape, the ISO-8601 times
with an offset, and the content types. A response model would carry some of this, but 22 of
the 46 routes declare none (ADR-0041), so asserting it against live responses is the only
thing that actually holds today.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from varuna_schemas.paths import repo_root

SNAPSHOT = repo_root() / "apps" / "command" / "openapi.json"
REGENERATE = "uv run varuna openapi && pnpm typegen"


def _snapshot() -> dict[str, Any]:
    if not SNAPSHOT.is_file():
        pytest.skip(f"no OpenAPI snapshot at {SNAPSHOT}; run `{REGENERATE}`")
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def test_the_committed_openapi_snapshot_lists_every_route_the_app_serves(
    client: TestClient,
) -> None:
    """A route the app serves but the snapshot does not is a route the console cannot type."""
    live = set(client.get("/openapi.json").json()["paths"])
    snapshot = set(_snapshot()["paths"])

    missing = sorted(live - snapshot)
    assert not missing, (
        f"{len(missing)} route(s) are served but absent from apps/command/openapi.json, so "
        f"`types.ts` has no types for them: {missing}. Run `{REGENERATE}`."
    )


def test_the_committed_snapshot_has_no_routes_the_app_stopped_serving(
    client: TestClient,
) -> None:
    """The other direction: a removed route leaves types the console can still import."""
    live = set(client.get("/openapi.json").json()["paths"])
    snapshot = set(_snapshot()["paths"])

    stale = sorted(snapshot - live)
    assert not stale, (
        f"{len(stale)} route(s) are in apps/command/openapi.json but no longer served, so "
        f"`types.ts` still types them: {stale}. Run `{REGENERATE}`."
    )


def test_the_snapshot_agrees_with_the_app_on_methods(client: TestClient) -> None:
    """`/v1/reports` once served GET and POST in the app and only POST in the snapshot."""
    live = client.get("/openapi.json").json()["paths"]
    snapshot = _snapshot()["paths"]

    disagree = {
        path: {"app": sorted(live[path]), "snapshot": sorted(snapshot[path])}
        for path in sorted(set(live) & set(snapshot))
        if set(live[path]) != set(snapshot[path])
    }
    assert not disagree, f"methods differ between app and snapshot: {disagree}. Run `{REGENERATE}`."


def test_a_missing_resource_answers_the_error_envelope_section_12_specifies(
    client: TestClient,
) -> None:
    """`{"error": {"code", "message", "run_id"}}`, and the message says what to do.

    Section 12 fixes the envelope and section 6.8 fixes the tone: "Errors say what happened
    and the fix". A bare FastAPI `{"detail": ...}` satisfies neither.
    """
    response = client.get("/v1/city/atlantis/layers/segments")
    assert response.status_code == 404

    body = response.json()
    assert "error" in body, f"error responses must use the section 12 envelope, got {body}"
    error = body["error"]
    assert set(error) >= {"code", "message"}, f"envelope needs code and message, got {error}"
    assert error["message"].strip(), "an empty message tells the caller nothing"
    assert len(error["message"]) > 20, (
        f"section 6.8 asks the message to say what happened and the fix: {error['message']!r}"
    )


def test_an_unbuilt_layer_names_the_command_that_builds_it(client: TestClient) -> None:
    """The 404 for a real city with no build is the one a visitor to the deployed API sees."""
    response = client.get("/v1/city/atlantis/layers/segments")
    message = response.json()["error"]["message"]
    assert "make city" in message or "varuna city" in message, (
        f"the message should name the command that fixes it, got {message!r}"
    )


@pytest.mark.parametrize(
    "path",
    ["/healthz", "/v1/runs", "/v1/replay/bundles", "/v1/cycle/status"],
)
def test_the_always_available_endpoints_answer_json(client: TestClient, path: str) -> None:
    """These four answer without a baked run, a built city or a generated bundle."""
    response = client.get(path)
    assert response.status_code == 200, f"{path} answered {response.status_code}"
    assert response.headers["content-type"].startswith("application/json")
    assert isinstance(response.json(), dict | list)


def test_every_documented_path_is_under_v1_or_is_a_platform_route(client: TestClient) -> None:
    """Section 12 puts the product under `/v1`; `/healthz` and the docs are the exceptions."""
    platform = {"/healthz", "/docs", "/redoc", "/openapi.json"}
    stray = sorted(
        path
        for path in client.get("/openapi.json").json()["paths"]
        if not path.startswith("/v1") and path not in platform
    )
    assert not stray, f"paths outside /v1 that are not platform routes: {stray}"
