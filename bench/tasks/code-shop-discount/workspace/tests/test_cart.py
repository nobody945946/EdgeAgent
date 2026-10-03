from shop.pricing import cart_total

ITEMS = [{"price": 1000, "qty": 2}]


def test_member_only():
    assert cart_total(ITEMS, member=True) == 1900


def test_member_and_coupon():
    # 5% member discount once, then the coupon.
    assert cart_total(ITEMS, member=True, coupon="SAVE100") == 1800
