"""Subprocess helpers for the task runner.

Everything the ``varuna`` CLI launches goes through this module so that Windows
(``pnpm.cmd``, ``taskkill``) and POSIX (process groups) differences live in one
place. Tests patch :func:`run_concurrently` and :func:`run` to keep the CLI
hermetic.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.markup import escape

IS_WINDOWS = sys.platform.startswith("win")


def _tolerate_unencodable_output() -> None:
    """Never let a child's banner character kill our output.

    Windows consoles and redirected streams default to cp1252, so relaying a line such as
    Next.js's "* Next.js 16.3.4" (U+25B2) raised UnicodeEncodeError inside rich and killed the
    log-pump thread: the service kept running but went silent for the operator. Replacing the
    character keeps the stream readable and the thread alive.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):  # detached or already-closed stream
            pass


_tolerate_unencodable_output()

console = Console(highlight=False)


class ToolMissingError(RuntimeError):
    """A required executable is not on PATH."""

    def __init__(self, name: str, hint: str | None = None) -> None:
        message = f"'{name}' is not on PATH."
        if hint:
            message = f"{message} {hint}"
        super().__init__(message)
        self.name = name


def resolve_executable(name: str) -> str | None:
    """Find ``name`` on PATH; on Windows also try the ``.cmd``/``.exe``/``.bat`` shims."""
    found = shutil.which(name)
    if found:
        return found
    if IS_WINDOWS:
        for ext in (".cmd", ".exe", ".bat"):
            found = shutil.which(name + ext)
            if found:
                return found
    return None


_HINTS: dict[str, str] = {
    "pnpm": "Install with 'npm install -g pnpm' or 'corepack enable'.",
    "node": "Install Node.js 22 or newer from nodejs.org.",
    "uv": "Install uv from astral.sh/uv.",
    "make": "On Windows: 'winget install ezwinports.make' (see docs/DECISIONS.md ADR-0004).",
    "docker": "Docker is optional (P1 services only).",
}


def command(name: str, *args: str) -> list[str]:
    """Build an argv for an external tool, raising :class:`ToolMissingError` when absent."""
    exe = resolve_executable(name)
    if exe is None:
        raise ToolMissingError(name, _HINTS.get(name))
    return [exe, *args]


def python(*args: str) -> list[str]:
    """Argv for the interpreter this CLI runs in (the uv virtualenv)."""
    return [sys.executable, *args]


def pnpm(*args: str) -> list[str]:
    """Argv for ``pnpm <args>``."""
    return command("pnpm", *args)


def display(argv: Sequence[str]) -> str:
    """Human-readable command line (executable basename, quoted arguments)."""
    parts: list[str] = []
    for index, part in enumerate(argv):
        text = Path(part).name if index == 0 else part
        parts.append(f'"{text}"' if " " in text else text)
    return " ".join(parts)


@dataclass(slots=True)
class RunResult:
    """Outcome of one :func:`run` call."""

    argv: list[str]
    returncode: int
    duration_s: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    label: str | None = None,
    quiet: bool = False,
) -> RunResult:
    """Run a command with inherited stdio (colours pass through) and return its exit code.

    Never raises for a non-zero exit; callers decide. A missing executable returns
    exit code 127 with a message, mirroring the shell convention.
    """
    merged_env = {**os.environ, **(env or {})}
    if not quiet:
        console.print(f"[bold]$[/bold] {escape(display(argv))}", style="dim")
    started = time.perf_counter()
    try:
        completed = subprocess.run(list(argv), cwd=cwd, env=merged_env, check=False)
        code = completed.returncode
    except FileNotFoundError:
        console.print(f"[red]Executable not found:[/red] {escape(argv[0])}")
        code = 127
    except KeyboardInterrupt:
        code = 130
    duration = time.perf_counter() - started
    if label and not quiet:
        status = "[green]ok[/green]" if code == 0 else f"[red]exit {code}[/red]"
        console.print(f"{escape(label)}: {status} ({duration:.1f} s)", style="dim")
    return RunResult(list(argv), code, duration)


def capture(argv: Sequence[str], *, timeout_s: float = 20.0) -> str | None:
    """Run a command and return its combined output stripped, or ``None`` when it fails."""
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return (completed.stdout or completed.stderr or "").strip()


# ---------------------------------------------------------------------------
# Concurrent long-running services (dev, demo)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Service:
    """A long-running child process with a coloured output prefix."""

    name: str
    argv: list[str]
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)
    color: str = "cyan"


def _pump(service: Service, proc: subprocess.Popen[str], width: int) -> None:
    prefix = f"[bold {service.color}]{service.name.ljust(width)} |[/] "
    assert proc.stdout is not None
    for line in proc.stdout:
        console.print(prefix + escape(line.rstrip("\r\n")), markup=True)


def kill_tree(proc: subprocess.Popen[str], *, grace_s: float = 5.0) -> None:
    """Stop a child and everything it spawned (Next.js and uvicorn fork workers)."""
    if proc.poll() is not None:
        return
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        import signal

        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + grace_s
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    try:
        proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        proc.kill()


def run_concurrently(services: Sequence[Service]) -> int:
    """Run services side by side with prefixed output; stop all when one fails or on Ctrl+C.

    Returns the first non-zero exit code, 0 when every service ended cleanly, and 130
    after a keyboard interrupt.
    """
    if not services:
        return 0
    width = max(len(s.name) for s in services)
    procs: list[tuple[Service, subprocess.Popen[str]]] = []
    threads: list[threading.Thread] = []
    popen_kwargs: dict[str, object] = {}
    if IS_WINDOWS:
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    exit_code = 0
    try:
        for service in services:
            console.print(
                f"[bold {service.color}]{service.name.ljust(width)} |[/] "
                f"[dim]$ {escape(display(service.argv))}[/dim]"
            )
            try:
                proc = subprocess.Popen(
                    service.argv,
                    cwd=service.cwd,
                    env={**os.environ, **service.env},
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    **popen_kwargs,  # type: ignore[arg-type]
                )
            except FileNotFoundError:
                console.print(f"[red]{service.name}: executable not found:[/red] {service.argv[0]}")
                exit_code = 127
                break
            procs.append((service, proc))
            thread = threading.Thread(target=_pump, args=(service, proc, width), daemon=True)
            thread.start()
            threads.append(thread)

        if exit_code == 0:
            exit_code = _wait_for_first_failure(procs)
    except KeyboardInterrupt:
        console.print("\nStopping services", style="dim")
        exit_code = 130
    finally:
        for _service, proc in procs:
            kill_tree(proc)
        for thread in threads:
            thread.join(timeout=2)
    return exit_code


def _wait_for_first_failure(procs: Sequence[tuple[Service, subprocess.Popen[str]]]) -> int:
    """Block until every service has exited cleanly or one has failed."""
    pending = list(procs)
    while pending:
        for item in list(pending):
            service, proc = item
            code = proc.poll()
            if code is None:
                continue
            pending.remove(item)
            if code != 0:
                console.print(
                    f"[red]{service.name} exited with code {code}; stopping the rest.[/red]"
                )
                return code
            console.print(f"{service.name} finished.", style="dim")
        time.sleep(0.25)
    return 0


__all__ = [
    "IS_WINDOWS",
    "RunResult",
    "Service",
    "ToolMissingError",
    "capture",
    "command",
    "console",
    "display",
    "kill_tree",
    "pnpm",
    "python",
    "resolve_executable",
    "run",
    "run_concurrently",
]
