import pytest

from shop.pricing import checkout


def test_basic_total_with_shipping():
    assert checkout([{"price": 10, "qty": 2}]) == 28.0


def test_free_shipping_over_100():
    assert checkout([{"price": 40, "qty": 3}]) == 120.0


def test_bulk_discount():
    assert checkout([{"price": 100, "qty": 1}]) == 95.0 + 8


def test_coupon():
    assert checkout([{"price": 50, "qty": 2}], coupon="SAVE10") == 90.0 + 8


def test_unknown_coupon_rejected():
    with pytest.raises(ValueError):
        checkout([{"price": 10, "qty": 1}], coupon="BOGUS")

# --- regression tests added by agents below ---
