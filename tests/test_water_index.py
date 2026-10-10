import numpy as np
import pytest

from flatcast.imagery import water_index as wi


def test_mndwi_values():
    green = np.array([1000, 500, 800], dtype=np.int16)
    swir = np.array([200, 1500, 800], dtype=np.int16)
    out = wi.mndwi(green, swir)
    np.testing.assert_allclose(out, [800 / 1200, -1000 / 2000, 0.0], rtol=1e-6)


def test_mndwi_fill_becomes_nan():
    out = wi.mndwi(np.array([-9999, 500]), np.array([300, -9999]))
    assert np.isnan(out).all()


def test_mndwi_negative_swir_over_water_stays_high_and_finite():
    out = wi.mndwi(np.array([600]), np.array([-30]))
    assert np.isfinite(out[0]) and out[0] > 0.99


def test_mndwi_scale_invariant():
    g, s = np.array([400.0]), np.array([900.0])
    assert wi.mndwi(g, s)[0] == pytest.approx(wi.mndwi(g * 3, s * 3)[0])


def test_classify_three_classes_and_nodata():
    idx = np.array([0.5, 0.10, 0.0, -0.10, -0.6, np.nan])
    cls = wi.classify(idx, water_above=0.10, dry_below=-0.10)
    assert cls.tolist() == [wi.WATER, wi.WATER, wi.AMBIGUOUS, wi.DRY, wi.DRY, wi.NODATA]


def test_classify_respects_cloud_mask():
    idx = np.array([0.5, -0.5])
    cls = wi.classify(idx, 0.1, -0.1, clear=np.array([False, True]))
    assert cls.tolist() == [wi.NODATA, wi.DRY]


def test_classify_rejects_inverted_thresholds():
    with pytest.raises(ValueError):
        wi.classify(np.array([0.0]), water_above=-0.2, dry_below=0.2)


def test_otsu_splits_bimodal_scene():
    rng = np.random.default_rng(1)
    idx = np.concatenate([rng.normal(-0.35, 0.06, 5000), rng.normal(0.45, 0.08, 5000)])
    t = wi.otsu_threshold(idx)
    assert -0.15 < t < 0.25


def test_encode_roundtrip():
    idx = np.array([0.1234, -0.9, np.nan, 1.0], dtype=np.float32)
    back = wi.decode(wi.encode(idx))
    np.testing.assert_allclose(back[[0, 1, 3]], [0.1234, -0.9, 1.0], atol=1e-4)
    assert np.isnan(back[2])
