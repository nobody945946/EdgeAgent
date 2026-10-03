from shop.pricing import cart_total


def test_coupon_only():
    assert cart_total([{"price": 500, "qty": 1}], coupon="TEN") == 450


def test_member_percent():
    assert cart_total([{"price": 1000, "qty": 1}], member=True, coupon="TEN") == 855
