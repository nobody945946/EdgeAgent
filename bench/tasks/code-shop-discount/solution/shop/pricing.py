from shop.discounts import member_discount, coupon_discount


def line_total(price, qty):
    return price * qty


def cart_total(items, member=False, coupon=None):
    subtotal = sum(line_total(i["price"], i["qty"]) for i in items)
    total = subtotal
    if member:
        total = member_discount(total)
    if coupon:
        total = coupon_discount(total, coupon)
    return round(total)
