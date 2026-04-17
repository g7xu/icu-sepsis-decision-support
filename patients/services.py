"""Backwards-compatible re-exports.

Historic call sites import public helpers from ``patients.services``; the
implementation now lives in :mod:`patients.features` and
:mod:`patients.scoring`.
"""

from .features import (
    assemble_hourly_wide_table,
    get_hourly_feature_sources,
    get_static_feature_sources,
)
from .scoring import (
    get_current_feature_vector,
    get_prediction,
    get_similar_patients,
)

__all__ = [
    "assemble_hourly_wide_table",
    "get_current_feature_vector",
    "get_hourly_feature_sources",
    "get_prediction",
    "get_similar_patients",
    "get_static_feature_sources",
]
