COUPONS = {"SAVE100": ("amount", 100), "TEN": ("percent", 10)}


def member_discount(total):
    """Members get 5% off."""
    return total * 0.95


def coupon_discount(total, code):
    kind, value = COUPONS[code]
    if kind == "amount":
        return max(0, total - value)
    return total * (100 - value) / 100
