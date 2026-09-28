"""Let the drain tests share their network builder.

`test_drain_kernel` checks the compiled kernel against the NumPy `drain1d.step` on the same
network `test_drain1d` already builds, so it imports that module's `_simple_network` and
`_init_state` rather than keeping a second copy that could drift apart - the whole point of
those tests is that the two solvers see *identical* input.

The repository runs pytest with `--import-mode=importlib` (`pyproject.toml`), which imports each
test module by path and puts nothing on `sys.path`, so a sibling test module cannot be imported
by name. Pointing pytest at `services/twin` happened to work because the qualified name
`tests.test_drain1d` resolved against that directory; from the repository root the same name
found the repo's own `tests/` package instead and `uv run pytest` - the SPEC.md 14 gate - died
on a collection error before running anything.

pytest imports this file before collecting the modules beside it whatever the import mode is, so
adding the directory here is enough and neither test file has to know about it.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = str(Path(__file__).parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
