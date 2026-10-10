import numpy as np

from flatcast.imagery import cloudmask as cm


def fm(*bits, aerosol=0):
    v = 0
    for b in bits:
        v |= b
    return v | (aerosol << cm.AEROSOL_SHIFT)


def test_decode_reads_each_bit():
    arr = np.array([fm(cm.CLOUD), fm(cm.SHADOW), fm(cm.ADJACENT), fm(cm.CIRRUS), fm(cm.WATER), fm(cm.SNOW)], dtype=np.uint8)
    d = cm.decode(arr)
    assert d["cloud"].tolist() == [True, False, False, False, False, False]
    assert d["shadow"].tolist() == [False, True, False, False, False, False]
    assert d["adjacent"].tolist() == [False, False, True, False, False, False]
    assert d["cirrus"].tolist() == [False, False, False, True, False, False]
    assert d["water"].tolist() == [False, False, False, False, True, False]
    assert d["snow"].tolist() == [False, False, False, False, False, True]


def test_aerosol_level_is_two_high_bits():
    d = cm.decode(np.array([fm(aerosol=0), fm(aerosol=1), fm(aerosol=2), fm(cm.WATER, aerosol=3)], dtype=np.uint8))
    assert d["aerosol"].tolist() == [0, 1, 2, 3]


def test_fill_is_never_clear_and_never_flagged():
    d = cm.decode(np.array([cm.FILL], dtype=np.uint8))
    assert d["fill"][0] and not d["cloud"][0] and not d["water"][0]
    assert not cm.clear_mask(np.array([cm.FILL], dtype=np.uint8))[0]


def test_clear_mask_defaults():
    arr = np.array(
        [0, fm(cm.WATER), fm(cm.CLOUD), fm(cm.SHADOW), fm(cm.ADJACENT), fm(cm.CIRRUS), fm(cm.SNOW), fm(aerosol=3)],
        dtype=np.uint8,
    )
    clear = cm.clear_mask(arr)
    # clear land, clear water, cloud, shadow, adjacent, cirrus, snow (kept: bright sand), high aerosol (kept)
    assert clear.tolist() == [True, True, False, False, False, False, True, True]


def test_clear_mask_options():
    arr = np.array([fm(cm.ADJACENT), fm(cm.SNOW), fm(aerosol=3), fm(cm.CIRRUS)], dtype=np.uint8)
    clear = cm.clear_mask(arr, mask_adjacent=False, mask_cirrus=False, mask_snow=True, mask_high_aerosol=True)
    assert clear.tolist() == [True, False, False, True]


def test_water_bit_does_not_remove_pixel():
    """Fmask's own water flag is ignored for masking: MNDWI decides water."""
    assert cm.clear_mask(np.array([fm(cm.WATER)], dtype=np.uint8))[0]


def test_clear_fraction_counts_fill_as_unclear():
    arr = np.array([[0, 0], [cm.FILL, fm(cm.CLOUD)]], dtype=np.uint8)
    assert cm.clear_fraction(cm.clear_mask(arr)) == 0.5
