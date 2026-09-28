"""VARUNA shared schemas: Pydantic v2 data contracts, settings, paths, design tokens and ramps.

Import paths::

    from varuna_schemas.models import RunMeta, SegmentForecastRow, ...   # every model
    from varuna_schemas.constants import PROFILE_THRESHOLDS_CM, IST, ...  # shared numbers
    from varuna_schemas.settings import get_settings
    from varuna_schemas.paths import repo_root, run_dir, bundle_dir, city_dir
    from varuna_schemas.tokens import load_tokens, depth_bands
    from varuna_schemas.ramps import depth_color_hex, depth_array_to_rgba
    from varuna_schemas.samples import sample, all_samples
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
