"""Lazy pandas helpers — avoids loading numpy/pandas on every Django startup."""

from __future__ import annotations

import math
from typing import Any


def get_pandas():
    import pandas as pd

    return pd


def cell_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    text = str(value).strip().lower()
    if text == "nan":
        return True
    try:
        return bool(get_pandas().isna(value))
    except (TypeError, ValueError):
        return False
