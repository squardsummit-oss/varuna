"""`POST /v1/runs/upload`: how a live run computed off the host reaches the API (ADR-0095)."""

from __future__ import annotations

import io
import json
import tarfile

import pytest
from fastapi.testclient import TestClient
from varuna_cycle.registry import RunRegistry

TOKEN = "t0ken-for-tests"
RUN = "MUM-20260930T0905Z-sky1.0-twin1.0-flash0.1-live"


def _archive(files: dict[str, bytes], *, symlink: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if symlink:
            info = tarfile.TarInfo(symlink)
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
    return buffer.getvalue()


def _run(run_id: str = RUN, bundle: str = "MUM-LIVE") -> dict[str, bytes]:
    meta = {"run_id": run_id, "bundle": bundle, "mode": "live"}
    return {
        f"{run_id}/run.json": json.dumps(meta).encode(),
        f"{run_id}/segments_wet.json": b"{}",
    }


def _post(client: TestClient, body: bytes, token: str | None = TOKEN):
    headers = {"Content-Type": "application/gzip"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.post("/v1/runs/upload", content=body, headers=headers)


def test_uploads_are_off_without_a_token(client, monkeypatch):
    monkeypatch.delenv("VARUNA_RUN_UPLOAD_TOKEN", raising=False)
    assert _post(client, _archive(_run())).status_code == 404


def test_a_wrong_token_is_refused(client, monkeypatch):
    monkeypatch.setenv("VARUNA_RUN_UPLOAD_TOKEN", TOKEN)
    assert _post(client, _archive(_run()), token="nope").status_code == 401
    assert _post(client, _archive(_run()), token=None).status_code == 401


@pytest.mark.parametrize(
    "files, symlink",
    [
        ({"../escape.txt": b"x"}, None),
        ({f"{RUN}/../../escape.txt": b"x"}, None),
        (_run(), f"{RUN}/link"),
        (_run("MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked"), None),
        (_run(bundle="MUM-2019-07-02"), None),
    ],
)
def test_anything_but_one_live_run_folder_is_refused(client, monkeypatch, files, symlink):
    monkeypatch.setenv("VARUNA_RUN_UPLOAD_TOKEN", TOKEN)
    response = _post(client, _archive(files, symlink=symlink))
    assert response.status_code == 400, response.text


def test_a_live_run_is_stored_and_only_the_newest_two_are_kept(
    client, monkeypatch, registry: RunRegistry
):
    monkeypatch.setenv("VARUNA_RUN_UPLOAD_TOKEN", TOKEN)
    ids = [
        "MUM-20260930T0805Z-sky1.0-twin1.0-flash0.1-live",
        "MUM-20260930T0835Z-sky1.0-twin1.0-flash0.1-live",
        RUN,
    ]
    for run_id in ids:
        response = _post(client, _archive(_run(run_id)))
        assert response.status_code == 201, response.text
        assert response.json()["run_id"] == run_id
    kept = sorted(p.name for p in registry.runs_dir.iterdir() if not p.name.startswith("."))
    assert kept == ids[1:]
