"""VARUNA twin service  -  coupled 2D surface + 1D drain simulation.

Modules:
    swe2d        -  2D local-inertial shallow-water solver (Numba, parallel)
    drain1d      -  1D diffusive-wave-lite drain solver
    hydrology    -  SCS-CN + depression storage -> effective rain
    coupling     -  inlet capture, surcharge exchange between 2D ↔ 1D
    runner       -  top-level ``run_twin()`` orchestrator
    types        -  data structures shared across the solvers
"""

__version__ = "0.1.0"
