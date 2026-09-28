from __future__ import annotations

from datetime import UTC, datetime

import pytest
from varuna_schemas.constants import IST
from varuna_schemas.models.run import (
    RunIdError,
    RunMeta,
    build_run_id,
    city_code,
    is_run_id,
    parse_run_id,
)
from varuna_schemas.samples import sample


def test_build_renders_ist_as_utc_compact() -> None:
    ts = datetime(2019, 7, 2, 17, 40, tzinfo=IST)
    run_id = build_run_id("mumbai", ts, "1.0", "1.0", "0.3", "baked")
    assert run_id == "MUM-20190702T1210Z-sky1.0-twin1.0-flash0.3-baked"
    assert build_run_id("chennai", ts, "1.0", "1.0", "0.3", "live").startswith("CHN-")
    assert build_run_id("MUM", ts, "1.0", "1.0", "0.3", "live").startswith("MUM-")


def test_parse_round_trip() -> None:
    ts = datetime(2019, 7, 2, 12, 10, tzinfo=UTC)
    run_id = build_run_id("mumbai", ts, "1.0", "2.1", "0.3", "live")
    parts = parse_run_id(run_id)
    assert parts.city_code == "MUM" and parts.city == "mumbai"
    assert parts.cycle_ts == ts
    assert parts.cycle_ts_ist.hour == 17 and parts.cycle_ts_ist.minute == 40
    assert (parts.sky_version, parts.twin_version, parts.flash_version) == ("1.0", "2.1", "0.3")
    assert parts.mode == "live"
    assert (
        build_run_id(
            parts.city,
            parts.cycle_ts,
            parts.sky_version,
            parts.twin_version,
            parts.flash_version,
            parts.mode,
        )
        == run_id
    )
    assert is_run_id(run_id)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "MUM-20190702T1210Z-sky1.0-twin1.0-flash0.3",
        "MUM-20190702T1210-sky1.0-twin1.0-flash0.3-baked",
        "mum-20190702T1210Z-sky1.0-twin1.0-flash0.3-baked",
        "MUM-20191302T1210Z-sky1.0-twin1.0-flash0.3-baked",
        "MUM-20190702T1210Z-sky1.0-twin1.0-flash0.3-demo",
        "MUMBAI-20190702T1210Z-sky1.0-twin1.0-flash0.3-baked",
    ],
)
def test_parse_rejects(bad: str) -> None:
    assert not is_run_id(bad)
    with pytest.raises(RunIdError):
        parse_run_id(bad)


def test_build_rejects_naive_and_bad_inputs() -> None:
    with pytest.raises(RunIdError, match="timezone-aware"):
        build_run_id("mumbai", datetime(2019, 7, 2, 17, 40), "1.0", "1.0", "0.3", "baked")
    ts = datetime(2019, 7, 2, 17, 40, tzinfo=IST)
    with pytest.raises(RunIdError):
        build_run_id("mumbai", ts, "1.0", "1.0", "0.3", "demo")  # type: ignore[arg-type]
    with pytest.raises(RunIdError):
        build_run_id("mumbai", ts, "v1", "1.0", "0.3", "baked")
    with pytest.raises(RunIdError):
        build_run_id("bombay", ts, "1.0", "1.0", "0.3", "baked")
    assert city_code("Mumbai") == "MUM" and city_code("pun") == "PUN"


def test_run_meta_validates_run_id_and_round_trips_computed_fields() -> None:
    meta = sample("RunMeta")
    assert isinstance(meta, RunMeta)
    dumped = meta.model_dump(mode="json")
    assert dumped["total_ms"] == sum(meta.stage_ms.values())
    assert dumped["lead_max_min"] == 180
    assert dumped["cycle_ts"].endswith("+05:30")
    again = RunMeta.model_validate(dumped)
    assert again == meta
    assert again.parts.city == "mumbai"
    with pytest.raises(ValueError):
        RunMeta.model_validate({**dumped, "run_id": "not-a-run"})
    with pytest.raises(ValueError):
        RunMeta.model_validate({**dumped, "unexpected": 1})
