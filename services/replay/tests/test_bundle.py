"""The bundle contract: layout, writers and loader (task P2.1, SPEC.md 10.2)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from varuna_replay import bundle as members
from varuna_replay.bundle import (
    BundleLayout,
    BundleNotFoundError,
    load_bundle,
    load_manifest,
    read_cube,
    read_cube_info,
    write_cube,
)
from varuna_replay.domain import StormDomain, step_times_min
from varuna_replay.validate import GROUND_TRUTH_FLOOR


# --------------------------------------------------------------------------- layout
def test_the_layout_is_the_one_the_spec_lists(tmp_path: Path) -> None:
    layout = BundleLayout(root=tmp_path / "MUM-2019-07-02")
    assert set(layout.members()) == {
        "manifest.json",
        "radar/frames.zarr",
        "truth/rain.zarr",
        "gauges.csv",
        "tide.csv",
        "traffic/speeds.parquet",
        "reports.jsonl",
        "ground_truth.geojson",
    }
    assert layout.bundle_id == "MUM-2019-07-02"
    assert layout.relative(layout.traffic) == "traffic/speeds.parquet"


def test_a_bundle_is_addressed_by_id_or_by_path(tmp_path: Path) -> None:
    from varuna_schemas.paths import bundle_dir

    assert BundleLayout.for_bundle("MUM-2019-07-02").root == bundle_dir("MUM-2019-07-02").resolve()
    assert BundleLayout.for_bundle(tmp_path / "here").root == (tmp_path / "here").resolve()


def test_loading_says_what_is_missing(tmp_path: Path) -> None:
    with pytest.raises(BundleNotFoundError, match="No bundle folder"):
        load_manifest(tmp_path / "not-there")
    empty = tmp_path / "MUM-EMPTY"
    empty.mkdir()
    with pytest.raises(BundleNotFoundError, match="not a bundle"):
        load_manifest(empty)


# --------------------------------------------------------------------------- cubes
def test_a_cube_round_trips_with_its_grid_and_time_axis(
    domain: StormDomain, tmp_path: Path, t0: datetime
) -> None:
    times = step_times_min(0.0, 60.0, 5.0)
    data = np.linspace(0.0, 40.0, times.size * domain.n_px**2, dtype=np.float32).reshape(
        times.size, domain.n_px, domain.n_px
    )
    path = write_cube(
        tmp_path / "rain.zarr",
        data,
        variable=members.TRUTH_VARIABLE,
        times_min=times,
        domain=domain,
        t0=t0,
        step_min=5.0,
        units="mm/h",
        attrs={"bundle": "MUM-TEST-2019"},
    )
    info = read_cube_info(path, members.TRUTH_VARIABLE)
    assert info.shape == (times.size, domain.n_px, domain.n_px)
    assert info.units == "mm/h"
    assert info.crs == domain.crs_string
    assert info.res_m == domain.res_m
    assert info.transform == domain.transform
    assert info.t0 == t0
    assert info.step_min == 5.0
    assert info.n_times == times.size
    assert np.array_equal(info.times_min, times)
    assert info.timestamps()[-1] == t0 + timedelta(minutes=60)
    assert info.attrs["bundle"] == "MUM-TEST-2019"
    assert np.array_equal(read_cube(path, members.TRUTH_VARIABLE), data)


def test_a_cube_refuses_a_shape_that_does_not_fit_the_domain(
    domain: StormDomain, tmp_path: Path, t0: datetime
) -> None:
    times = step_times_min(0.0, 10.0, 5.0)
    kwargs = {
        "variable": members.TRUTH_VARIABLE,
        "times_min": times,
        "domain": domain,
        "t0": t0,
        "step_min": 5.0,
        "units": "mm/h",
    }
    with pytest.raises(ValueError, match=r"\(t, y, x\)"):
        write_cube(tmp_path / "a.zarr", np.zeros((3, 4)), **kwargs)
    with pytest.raises(ValueError, match="instants"):
        write_cube(tmp_path / "b.zarr", np.zeros((2, *domain.shape)), **kwargs)
    with pytest.raises(ValueError, match="does not match the domain"):
        write_cube(tmp_path / "c.zarr", np.zeros((3, 8, 8)), **kwargs)


# --------------------------------------------------------------------------- streams
def test_streams_round_trip_through_the_writers(tmp_path: Path, t0: datetime) -> None:
    layout = BundleLayout(root=tmp_path / "MUM-TEST-2019")
    layout.root.mkdir()
    members.write_gauges(
        layout.gauges,
        [
            {
                "ts": t0,
                "station_id": "santacruz",
                "lat": 19.0896,
                "lon": 72.8656,
                "mm_5min": 4.5,
                "synthetic": True,
            }
        ],
    )
    members.write_tide(layout.tide, [{"ts": t0, "stage_m": 2.4, "source": "illustrative"}])
    members.write_traffic(
        layout.traffic,
        [
            {
                "ts": t0,
                "segment_id": "seg-1",
                "kmh": 4.0,
                "baseline_kmh": 27.0,
                "synthetic": True,
            }
        ],
    )
    members.write_reports(
        layout.reports,
        [{"ts": t0, "lat": 19.01, "lon": 72.84, "depth_hint": "knee", "synthetic": True}],
    )

    text = layout.gauges.read_text(encoding="utf-8")
    assert text.splitlines()[0] == ",".join(members.GAUGES_COLUMNS)
    assert t0.isoformat() in text, "timestamps keep their +05:30 offset"
    assert "\r\n" not in text, "line endings are LF"

    from varuna_schemas.models.bundle import BundleManifest

    manifest_path = layout.manifest
    manifest = BundleManifest.model_validate(
        {
            "id": "MUM-TEST-2019",
            "city": "mumbai",
            "label": "Design storm",
            "t0": t0,
            "t1": t0 + timedelta(minutes=60),
            "cadences": {"radar": 10, "truth": 5, "cycle": 5},
            "radar_domain": {
                "center_lon": 72.86,
                "center_lat": 19.065,
                "size_km": 20.0,
                "res_m": 500.0,
            },
            "aoi": [72.815, 18.995, 72.905, 19.135],
            "seed": 2019,
        }
    )
    members.write_manifest(manifest_path, manifest)
    bundle = load_bundle(layout.root)
    assert bundle.id == "MUM-TEST-2019"
    assert bundle.read_gauges().iloc[0]["station_id"] == "santacruz"
    assert bundle.read_tide().iloc[0]["source"] == "illustrative"
    assert bool(bundle.read_traffic().iloc[0]["synthetic"]) is True
    assert bundle.read_reports()[0]["depth_hint"] == "knee"


def test_reports_are_one_json_object_per_line_with_sorted_keys(
    tmp_path: Path, t0: datetime
) -> None:
    path = tmp_path / "reports.jsonl"
    members.write_reports(
        path,
        [
            {"ts": t0, "lat": 19.01, "lon": 72.84, "depth_hint": "ankle", "synthetic": True},
            {"ts": t0, "lat": 19.03, "lon": 72.86, "depth_hint": "waist", "synthetic": True},
        ],
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert list(first) == sorted(first)
    assert first["ts"].endswith("+05:30")


def test_ground_truth_is_written_as_a_feature_collection(tmp_path: Path, pin) -> None:
    path = tmp_path / "ground_truth.geojson"
    members.write_ground_truth(
        path, [pin()], name="test pins", description="Sourced pins for the test bundle."
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["type"] == "FeatureCollection"
    assert payload["name"] == "test pins"
    assert payload["features"][0]["properties"]["source_url"].startswith("https://")


# --------------------------------------------------------------------------- listing
def test_only_folders_with_a_manifest_are_bundles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VARUNA_BUNDLES_DIR", str(tmp_path))
    (tmp_path / "MUM-REAL").mkdir()
    (tmp_path / "MUM-REAL" / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "not-a-bundle").mkdir()
    assert members.list_bundle_ids() == ["MUM-REAL"]


def test_a_full_bundle_loads_every_member(full_bundle: Path) -> None:
    bundle = load_bundle(full_bundle)
    assert bundle.manifest.label == "Reconstructed replay"
    assert bundle.manifest.storm is not None and bundle.manifest.storm.cells
    truth, radar = bundle.truth_info(), bundle.radar_info()
    assert truth.grid_key() == radar.grid_key()
    assert truth.n_times == 13 and radar.n_times == 7  # 60 min at 5 and 10 minutes
    assert bundle.read_truth().shape == truth.shape
    assert len(bundle.read_ground_truth()["features"]) == GROUND_TRUTH_FLOOR
