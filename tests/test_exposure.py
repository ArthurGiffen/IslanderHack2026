"""The per-pixel fit, checked on synthetic flats with known answers."""

import numpy as np
import pytest
from scipy.special import expit

from flatcast.imagery.water_index import AMBIGUOUS, DRY, NODATA, WATER
from flatcast.models import exposure as E


def synthetic(T=360, H=12, W=12, A=None, k=30.0, seed=0, z=None, years=6.0, z_late=None):
    rng = np.random.default_rng(seed)
    times = np.datetime64("2018-01-01") + (np.sort(rng.uniform(0, years * 365.25, T)) * 86400).astype("timedelta64[s]")
    h = rng.normal(0.30, 0.15, T)
    tau = rng.normal(0, 40, (T, 2))
    x = np.linspace(-8, 8, W, dtype=np.float32)
    y = np.linspace(-8, 8, H, dtype=np.float32)
    dx, dy = np.meshgrid(x, y)
    if z is None:
        z = rng.uniform(0.05, 0.55, (H, W))
    A = np.zeros((2, 2)) if A is None else np.asarray(A)
    s = tau @ A.T
    eta = h[:, None, None] + s[:, 0, None, None] * dx + s[:, 1, None, None] * dy
    zt = np.broadcast_to(z, (T, H, W)).copy()
    if z_late is not None:  # flats shift: later half of the record uses z_late
        zt[T // 2 :] = z_late
    p = expit(k * (zt - eta))
    masks = np.where(rng.uniform(size=p.shape) < p, DRY, WATER).astype(np.uint8)
    masks[rng.uniform(size=masks.shape) < 0.15] = NODATA  # clouds
    masks[rng.uniform(size=masks.shape) < 0.03] = AMBIGUOUS
    return masks, h, tau, times, dx, dy, z


def test_recovers_thresholds():
    masks, h, tau, times, dx, dy, z = synthetic()
    m = E.fit(masks, h, tau, times, dx, dy, fit_setup=False, half_life_years=100)
    ok = m.status == E.FITTED
    assert ok.mean() > 0.9
    assert np.median(np.abs(m.z[ok] - z[ok])) < 0.02


def test_probability_falls_as_water_rises():
    masks, h, tau, times, dx, dy, _ = synthetic(seed=1)
    m = E.fit(masks, h, tau, times, dx, dy, fit_setup=False)
    levels = np.linspace(-0.2, 0.9, 25)
    stack = np.stack([m.p_dry(l) for l in levels])
    assert np.all(np.diff(stack, axis=0) <= 1e-6)
    assert np.all(np.exp(m.logk) > 0)


def test_learns_wind_tilt():
    A_true = [[4e-4, 0.0], [0.0, 2.5e-4]]
    masks, h, tau, times, dx, dy, z = synthetic(T=500, A=A_true, seed=2)
    m = E.fit(masks, h, tau, times, dx, dy, fit_setup=True, rounds=4, half_life_years=100)
    np.testing.assert_allclose(m.A, A_true, atol=1e-4)
    ok = m.status == E.FITTED
    assert np.median(np.abs(m.z[ok] - z[ok])) < 0.02


def test_never_dry_and_sparse_pixels_are_flagged():
    z = np.full((6, 6), 0.3)
    z[0, 0] = -3.0  # always under water
    z[0, 1] = 3.0  # always dry
    masks, h, tau, times, dx, dy, _ = synthetic(H=6, W=6, z=z, seed=3)
    masks[:, 5, 5] = NODATA  # never seen
    m = E.fit(masks, h, tau, times, dx, dy, fit_setup=False)
    assert m.status[0, 0] == E.NEVER_DRY
    assert m.status[0, 1] == E.NEVER_WET
    assert m.status[5, 5] == E.SPARSE
    assert np.isnan(m.p_dry(0.3)[5, 5])
    assert np.isfinite(m.z[0, 0]) and m.z[0, 0] < h.min()


def test_recent_years_weigh_more():
    early = np.full((5, 5), 0.20)
    late = np.full((5, 5), 0.40)
    masks, h, tau, times, dx, dy, _ = synthetic(H=5, W=5, z=early, z_late=late, T=600, seed=4)
    flat = E.fit(masks, h, tau, times, dx, dy, fit_setup=False, half_life_years=1000)
    recent = E.fit(masks, h, tau, times, dx, dy, fit_setup=False, half_life_years=1.0)
    assert abs(recent.z.mean() - 0.40) < abs(flat.z.mean() - 0.40)
    assert recent.z.mean() > 0.33


def test_uncertainty_softens_probabilities():
    masks, h, tau, times, dx, dy, _ = synthetic(seed=5)
    m = E.fit(masks, h, tau, times, dx, dy, fit_setup=False)
    sharp = m.p_dry(0.1)
    soft = m.p_dry(0.1, sigma=0.15)
    ok = np.isfinite(sharp)
    assert np.abs(soft[ok] - 0.5).mean() < np.abs(sharp[ok] - 0.5).mean()


def test_save_load_roundtrip(tmp_path):
    masks, h, tau, times, dx, dy, _ = synthetic(H=4, W=4, seed=6)
    m = E.fit(masks, h, tau, times, dx, dy)
    m2 = E.ExposureModel.load(m.save(tmp_path / "m.npz"))
    np.testing.assert_array_equal(m.z, m2.z)
    np.testing.assert_array_equal(m.A, m2.A)
    assert m2.meta["n_images"] == len(h)
    assert m.p_dry(0.3, (10, -5)) == pytest.approx(m2.p_dry(0.3, (10, -5)), nan_ok=True)


def test_recency_weights_half_life():
    t = np.array(["2020-01-01", "2022-01-01"], dtype="datetime64[s]")
    w = E.recency_weights(t, half_life_years=2.0)
    assert w[1] == 1.0
    assert w[0] == pytest.approx(0.5, abs=0.01)
