"""Future-wind window means must take each lead from the right forecast bucket."""

import numpy as np
import pandas as pd

from flatcast.models.waterlevel import features as F


def test_window_mean_uses_bucket_per_lead():
    idx = pd.date_range("2025-01-01", periods=200, freq="h", tz="UTC")
    one = pd.DataFrame({"u": 1.0, "v": 0.0, "p": 1010.0}, index=idx)
    two = pd.DataFrame({"u": 3.0, "v": 0.0, "p": 1010.0}, index=idx)
    src = F.prepare_sources({1: one, 2: two}, idx)
    i = np.array([10, 50])
    np.testing.assert_allclose(F._window_mean(src, "u", i, 0, 24), [1.0, 1.0])
    np.testing.assert_allclose(F._window_mean(src, "u", i, 24, 48), [3.0, 3.0])
    np.testing.assert_allclose(F._window_mean(src, "u", i, 0, 48), [2.0, 2.0])
    np.testing.assert_allclose(F._window_mean(src, "u", i, 18, 30), [2.0, 2.0])


def test_window_mean_missing_data_gives_nan():
    idx = pd.date_range("2025-01-01", periods=100, freq="h", tz="UTC")
    df = pd.DataFrame({"u": 1.0, "v": 0.0, "p": 1010.0}, index=idx)
    df.iloc[20:40, 0] = np.nan
    src = F.prepare_sources({1: df, 2: df}, idx)
    assert np.isnan(F._window_mean(src, "u", np.array([15]), 0, 24))[0]
    assert F._window_mean(src, "u", np.array([60]), 0, 24)[0] == 1.0


def test_bucket_of():
    assert [F.bucket_of(x) for x in (1, 6, 24, 25, 48)] == [1, 1, 1, 2, 2]
