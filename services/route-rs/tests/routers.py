"""Shared harness for the Rust/Python routing parity test and the benchmark (task P8.12).

Both routers are pointed at the same inputs: the city on disk (``VARUNA_CITY_DIR`` or
``<repo>/city``), the Python graph export, and a private data directory holding copies of the
demo runs plus an ops log this harness writes - so a closure typed into the real desk while the
test runs cannot make the two disagree, and the test can close a street on purpose.

Nothing here is a pytest fixture; ``test_route_rs_parity.py`` and ``tools/bench.py`` both use it.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

CRATE = Path(__file__).resolve().parents[1]
EXE = CRATE / "target" / "release" / ("varuna-route.exe" if os.name == "nt" else "varuna-route")

# The demo cycles, by the IST time the cycle ran. 0310Z is the 08:40 cycle the demo trip uses.
RUNS = {
    "0710": "MUM-20190702T0110Z-sky1.0-twin1.0-flash0.1-baked",
    "0740": "MUM-20190702T0210Z-sky1.0-twin1.0-flash0.1-baked",
    "0810": "MUM-20190702T0240Z-sky1.0-twin1.0-flash0.1-baked",
    "0810-1member": "MUM-20190702T0240Z-sky1.0-twin1.0-flash0.0-baked",
    "0840": "MUM-20190702T0310Z-sky1.0-twin1.0-flash0.1-baked",
    "0910": "MUM-20190702T0340Z-sky1.0-twin1.0-flash0.1-baked",
    # Newest by name, so a request with no run_id routes on it in both routers.
    "latest": "MUM-20190702T0340Z-sky1.0-twin1.0-flash0.1-live",
}

# Places from the city's own asset layer and the hotspot register (lon, lat).
KEM = (72.84218, 19.001551)
SION = (72.858231, 19.034843)
WORLI = (72.822586, 19.012812)
CHEMBUR = (72.894641, 19.055714)
BHABHA_KURLA = (72.878112, 19.070846)
BANDRA_FIRE = (72.837564, 19.050443)
HINDUJA = (72.83823, 19.033224)
HINDMATA = (72.841, 19.012)
MILAN_SUBWAY = (72.840, 19.079)
ANDHERI_SUBWAY = (72.844, 19.119)
DADAR_TT = (72.845, 19.019)


@dataclass(frozen=True)
class Trip:
    name: str
    origin: tuple[float, float]
    destination: tuple[float, float]
    run: str | None
    """A key of :data:`RUNS`, or None for "the newest run" (no run_id sent)."""
    depart_at: str | None = None
    profile: str = "ambulance"
    risk_tolerance: float | None = None
    trip_id: str | None = None
    spread: bool = True
    explain: bool = True
    closed: bool = False
    """Route against the data directory whose ops log closes streets on this trip's naive way."""

    def body(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "origin": list(self.origin),
            "destination": list(self.destination),
            "profile": self.profile,
            "spread": self.spread,
            "explain": self.explain,
        }
        if self.run is not None:
            out["run_id"] = RUNS[self.run]
        if self.depart_at is not None:
            out["depart_at"] = self.depart_at
        if self.risk_tolerance is not None:
            out["risk_tolerance"] = self.risk_tolerance
        if self.trip_id is not None:
            out["trip_id"] = self.trip_id
        return out


def at(hhmm: str) -> str:
    return f"2019-07-02T{hhmm}:00+05:30"


TRIPS: list[Trip] = [
    Trip(
        "KEM to Sion, ambulance, 08:40",
        KEM,
        SION,
        "0840",
        at("08:40"),
        "ambulance",
        trip_id="kem-sion",
    ),
    Trip("KEM to Sion, car, 08:40, no trip id", KEM, SION, "0840", at("08:40"), "car"),
    Trip(
        "KEM to Sion, two-wheeler, 08:40",
        KEM,
        SION,
        "0840",
        at("08:40"),
        "two_wheeler",
        trip_id="kem-2w",
    ),
    Trip(
        "KEM to Sion, ambulance, tolerance 0.05", KEM, SION, "0840", at("08:40"), "ambulance", 0.05
    ),
    Trip("KEM to Sion, ambulance, newest run, no departure", KEM, SION, None),
    Trip(
        "Worli to Chembur, car, 08:40 (cross-city)",
        WORLI,
        CHEMBUR,
        "0840",
        at("08:40"),
        "car",
        trip_id="worli-chembur",
    ),
    Trip(
        "Worli to Chembur, bus, 09:10", WORLI, CHEMBUR, "0910", at("09:10"), "bus", trip_id="bus-1"
    ),
    Trip(
        "Worli to Chembur, two-wheeler, 07:10",
        WORLI,
        CHEMBUR,
        "0710",
        at("07:10"),
        "two_wheeler",
        trip_id="x",
    ),
    Trip(
        "Worli to Chembur, car, explain off",
        WORLI,
        CHEMBUR,
        "0840",
        at("08:40"),
        "car",
        explain=False,
    ),
    Trip(
        "KEM to Chembur, car, spread off",
        KEM,
        CHEMBUR,
        "0840",
        at("08:40"),
        "car",
        trip_id="nospread",
        spread=False,
    ),
    Trip(
        "Bandra fire station to KEM, fire tender, 08:10",
        BANDRA_FIRE,
        KEM,
        "0810",
        at("08:10"),
        "fire_tender",
        trip_id="ft",
    ),
    Trip(
        "Milan subway to Hindmata, pedestrian, 08:40",
        MILAN_SUBWAY,
        HINDMATA,
        "0840",
        at("08:40"),
        "pedestrian",
        trip_id="walk",
    ),
    Trip(
        "Andheri subway to KEM, car, run's own start",
        ANDHERI_SUBWAY,
        KEM,
        "0840",
        None,
        "car",
        trip_id="andheri",
    ),
    Trip(
        "Chembur to Bandra, truck, tolerance 0.9",
        CHEMBUR,
        BANDRA_FIRE,
        "0810",
        at("08:10"),
        "truck",
        0.9,
    ),
    Trip(
        "Hinduja to Bhabha Kurla, car, one-member run",
        HINDUJA,
        BHABHA_KURLA,
        "0810-1member",
        at("08:10"),
        "car",
        trip_id="one",
    ),
    Trip(
        "Worli to Sion, two-wheeler, past the window",
        WORLI,
        SION,
        "0840",
        at("11:30"),
        "two_wheeler",
    ),
    Trip(
        "Dadar TT to Bhabha Kurla, car, before the window",
        DADAR_TT,
        BHABHA_KURLA,
        "0840",
        at("06:30"),
        "car",
        trip_id="early",
    ),
    Trip(
        "Hindmata to Sion, two-wheeler, 08:40",
        HINDMATA,
        SION,
        "0840",
        at("08:40"),
        "two_wheeler",
        trip_id="hm",
    ),
    Trip("KEM to KEM, same junction", KEM, KEM, "0840", at("08:40"), "car", trip_id="same"),
    Trip(
        "KEM to Sion, car, tolerance 0 refuses every edge",
        KEM,
        SION,
        "0840",
        at("08:40"),
        "car",
        0.0,
    ),
    Trip(
        "Worli to Chembur, car, 07:40", WORLI, CHEMBUR, "0740", at("07:40"), "car", trip_id="w-0740"
    ),
    Trip(
        "Worli to Chembur, ambulance, 08:43:17.5 IST",
        WORLI,
        CHEMBUR,
        "0840",
        "2019-07-02T08:43:17.5+05:30",
        "ambulance",
        trip_id="odd",
    ),
    Trip(
        "Worli to Chembur, car, departure in UTC",
        WORLI,
        CHEMBUR,
        "0840",
        "2019-07-02T03:10:00Z",
        "car",
        trip_id="utc",
    ),
    *[
        Trip(
            f"Worli to Chembur, car, trip id {tid}",
            WORLI,
            CHEMBUR,
            "0840",
            at("08:40"),
            "car",
            trip_id=tid,
        )
        for tid in ("alpha", "beta", "gamma", "delta", "epsilon")
    ],
    Trip(
        "KEM to Sion, car, over a closed street",
        KEM,
        SION,
        "0840",
        at("08:40"),
        "car",
        trip_id="closed-1",
        closed=True,
    ),
    Trip(
        "Worli to Chembur, two-wheeler, over closed streets",
        WORLI,
        CHEMBUR,
        "0840",
        at("08:40"),
        "two_wheeler",
        trip_id="closed-2",
        closed=True,
    ),
    Trip(
        "KEM to Sion, ambulance, closure without a reason",
        KEM,
        SION,
        "0840",
        at("08:40"),
        "ambulance",
        trip_id="closed-3",
        closed=True,
    ),
]


def find_cargo() -> str | None:
    found = shutil.which("cargo")
    if found:
        return found
    home = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo")) / "bin"
    for name in ("cargo.exe", "cargo"):
        if (home / name).is_file():
            return str(home / name)
    return None


def load_exporter() -> Any:
    spec = importlib.util.spec_from_file_location(
        "route_rs_export_graph", CRATE / "tools" / "export_graph.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_release(cargo: str) -> None:
    subprocess.run([cargo, "build", "--release", "--quiet"], cwd=CRATE, check=True)


def ensure_export(city: str = "mumbai") -> Path:
    exporter = load_exporter()
    out: Path = exporter.default_out(city)
    if not exporter.is_current(out, city):
        exporter.export(city, out)
    return out


def copy_runs(source_runs: Path, target: Path) -> dict[str, bool]:
    """Copy each demo run's two routing inputs; returns which run keys were available."""
    present: dict[str, bool] = {}
    for key, run_id in RUNS.items():
        src = source_runs / run_id
        ok = (src / "segments_wet.json").is_file()
        present[key] = ok
        if not ok:
            continue
        dst = target / "runs" / run_id
        dst.mkdir(parents=True, exist_ok=True)
        for name in ("segments_wet.json", "run.json"):
            if (src / name).is_file():
                shutil.copyfile(src / name, dst / name)
    return present


def write_ops(data_dir: Path, lines: list[dict[str, Any]]) -> None:
    path = data_dir / "ops" / "mumbai.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for line in lines:
            handle.write(json.dumps(line, separators=(",", ":"), sort_keys=True) + "\n")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class RustServer:
    data_dir: Path
    graph: Path
    naive: str = "dijkstra"
    port: int = 0
    proc: subprocess.Popen[bytes] | None = None
    log: list[str] = field(default_factory=list)

    def start(self) -> RustServer:
        self.port = self.port or free_port()
        self.proc = subprocess.Popen(
            [
                str(EXE),
                "serve",
                "--port",
                str(self.port),
                "--data-dir",
                str(self.data_dir),
                "--graph",
                str(self.graph),
                "--naive",
                self.naive,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                health = self.get("/healthz")
                if health.get("status") == "ok":
                    return self
            except (urllib.error.URLError, ConnectionError, OSError):
                pass
            if self.proc.poll() is not None:
                msg = f"varuna-route exited with {self.proc.returncode} before answering /healthz"
                raise RuntimeError(msg)
            time.sleep(0.1)
        msg = "varuna-route did not answer /healthz within 120 s"
        raise RuntimeError(msg)

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def get(self, path: str) -> dict[str, Any]:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=10) as r:
            return json.loads(r.read())

    def post(self, body: Any, *, raw: bytes | None = None) -> tuple[int, dict[str, Any]]:
        data = raw if raw is not None else json.dumps(body).encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/route",
            data=data,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())


def python_route(trip: Trip) -> dict[str, Any]:
    """``plan()`` then ``as_dict()``, normalised through JSON as the API would send it."""
    from varuna_route.router import as_dict, plan

    result = plan(
        trip.origin,
        trip.destination,
        depart_at=datetime.fromisoformat(trip.depart_at) if trip.depart_at else None,
        vehicle=trip.profile,
        risk_tolerance=trip.risk_tolerance,
        run_id=RUNS[trip.run] if trip.run is not None else None,
        spread=trip.spread,
        trip_id=trip.trip_id,
        explain=trip.explain,
    )
    return json.loads(json.dumps(as_dict(result)))


def differences(python: Any, rust: Any, path: str = "") -> list[tuple[str, Any, Any]]:
    """Every leaf where the two responses differ, as ``(field, python, rust)``; ``ms`` excluded."""
    out: list[tuple[str, Any, Any]] = []
    if isinstance(python, dict) and isinstance(rust, dict):
        for key in sorted(set(python) | set(rust)):
            if path == "" and key == "ms":
                continue
            if key not in python or key not in rust:
                out.append(
                    (f"{path}.{key}", python.get(key, "<absent>"), rust.get(key, "<absent>"))
                )
                continue
            out.extend(differences(python[key], rust[key], f"{path}.{key}"))
    elif isinstance(python, list) and isinstance(rust, list):
        if len(python) != len(rust):
            out.append((f"{path}[len]", len(python), len(rust)))
        for i, (a, b) in enumerate(zip(python, rust, strict=False)):
            out.extend(differences(a, b, f"{path}[{i}]"))
    else:
        same = python == rust and type(python) is type(rust)
        # json.loads gives int for "5" and float for "5.0"; Rust writes every f64 with a point,
        # Python writes round(x, 1) the same way, so a type mismatch is a real difference.
        if not same:
            out.append((path, python, rust))
    return out


def python_process_count() -> int:
    """How many python processes are running on this machine, for the measurement record."""
    try:
        if os.name == "nt":
            text = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
            return sum(1 for line in text.splitlines() if line.startswith('"python.exe"'))
        text = subprocess.run(["pgrep", "-c", "python"], capture_output=True, text=True).stdout
        return int(text.strip() or 0)
    except OSError:
        return -1


if __name__ == "__main__":  # pragma: no cover - manual use
    print(len(TRIPS), "trips", file=sys.stderr)
