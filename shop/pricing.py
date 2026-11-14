"""A tiny checkout service. v0 ships with a few real-world-ish bugs on purpose."""

COUPONS = {"SAVE10": 0.10, "SAVE20": 0.20, "FREESHIP": 0.0}
SHIPPING_WAIVERS = {"FREESHIP"}
BULK_THRESHOLD = 50  # average item price above this gets 5% off
FREE_SHIPPING_MIN = 100
SHIPPING_FEE = 8


def checkout(items, coupon=None):
    if not items:
        return 0.0
    subtotal = sum(i["price"] * i.get("qty", 1) for i in items)
    avg = subtotal / sum(i.get("qty", 1) for i in items)
    if avg > BULK_THRESHOLD:
        subtotal *= 0.95
    if coupon:
        coupon = coupon.strip().upper()
        if coupon not in COUPONS:
            raise ValueError(f"unknown coupon: {coupon}")
        subtotal *= 1 - COUPONS[coupon]
    shipping = 0 if subtotal >= FREE_SHIPPING_MIN else SHIPPING_FEE
    if coupon in SHIPPING_WAIVERS:
        shipping = 0
    return round(subtotal + shipping, 2)
