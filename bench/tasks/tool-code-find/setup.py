import sys
from pathlib import Path
w = Path(sys.argv[1]) / "shop"
w.mkdir()
(w / "__init__.py").write_text("")
(w / "cart.py").write_text("""from shop.pricing import shipping_fee\n\n\nclass Cart:\n    def __init__(self):\n        self.items = []\n\n    def add(self, name, price, qty=1):\n        self.items.append((name, price, qty))\n\n    def subtotal(self):\n        return sum(p * q for _, p, q in self.items)\n\n    def total(self, region):\n        return self.subtotal() + shipping_fee(self.subtotal(), region)\n""")
(w / "pricing.py").write_text("""\"\"\"Prices, discounts and delivery.\"\"\"\n\nTAX = 0.05\n\n\ndef with_tax(amount):\n    return round(amount * (1 + TAX))\n\n\ndef member_discount(amount, level):\n    return amount * {"gold": 0.9, "silver": 0.95}.get(level, 1)\n\n\ndef shipping_fee(subtotal, region):\n    \"\"\"Free over 1000; outlying islands pay more.\"\"\"\n    if subtotal >= 1000:\n        return 0\n    return 150 if region == "island" else 60\n""")
(w / "report.py").write_text("""def monthly(orders):\n    return sum(o["total"] for o in orders)\n""")
