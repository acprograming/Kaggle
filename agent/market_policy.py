"""Price-aware market policy.

The measured failure of the tape agent is that it dumps whole-shed sell orders
every turn, walking premium products down their own supply curve. Realised
revenue was 77% of base value: milk 59%, fertilizer 54%, wool 71%.

The curve is convex, so the cure is metering. Each turn we sell unit by unit
only while the *marginal* price stays at or above a per-product reserve. That
is self-regulating: when the town has drained a product the price is high and
we sell a lot; once our own selling walks the price down to the reserve we
stop, and the next town consumption tick restores headroom. No explicit rate
schedule is needed.

Reserves are expressed as a fraction of base price and are tighter for the
products whose above-equilibrium curve is steep (above_target > 1: strawberry,
milk, melon, wool), which the rules note "drive straight to the $1 floor".
"""
from __future__ import annotations

import math

CROPS = ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY", "MELON")
ANIMALS = ("GOOSE", "COW", "SHEEP")
PRODUCTS = CROPS + ("EGG", "MILK", "WOOL", "FERTILIZER")

PARAMS = {
    "WHEAT": (25, 400, "sqrt", 0.8, "log", 0.2),
    "CARROT": (35, 450, "hinge", 1.0, "sqrt", 0.7),
    "TOMATO": (60, 200, "hinge", 0.4, "sqrt", 0.6),
    "STRAWBERRY": (120, 100, "sqrt", 0.7, "linear", 1.6),
    "MELON": (250, 300, "log", 0.2, "sq", 3.6),
    "EGG": (50, 332, "hinge", 0.4, "log", 0.2),
    "MILK": (160, 122, "sqrt", 0.6, "linear", 1.6),
    "WOOL": (200, 105, "log", 0.2, "sq", 3.2),
    "FERTILIZER": (100, 200, "linear", 0.4, "linear", 0.4),
}

# Reserve as a fraction of base price. Steep-decay premium goods are protected
# hardest; egg and wheat barely decay so they are left almost free to sell.
RESERVE_FRAC = {
    "WHEAT": 0.70,
    "CARROT": 0.60,
    "TOMATO": 0.60,
    "STRAWBERRY": 0.62,
    "MELON": 0.62,
    "EGG": 0.75,
    "MILK": 0.62,
    "WOOL": 0.62,
    "FERTILIZER": 0.55,
}

# Days over which the reserve ramps to zero so nothing is left unsold at the
# end. Unsold stock scores nothing, so late in the season any price beats none.
LIQUIDATE_DAYS = 3
LAST_DAY = 29
SHED_CAP = 100
SHED_PRESSURE = 88  # above this, relax reserves: end-of-day overflow is discarded


def _shape(kind, x, scale):
    x = max(0.0, x)
    if kind == "linear":
        return x
    if kind == "sq":
        return x * x
    if kind == "sqrt":
        return math.sqrt(x)
    if kind == "log":
        return math.log1p(x)
    if kind == "log10":
        return math.log10(1 + x)
    if kind == "hinge":
        u = x / scale if scale > 0 else x
        return u + 8 * max(0.0, u - 1) ** 2 if scale > 0 else x
    return x


def price(item, inventory, market_params=None):
    """Exact official curve, including sparse per-product overrides."""
    base, scale, below, bt, above, at = PARAMS[item]
    patch = (market_params or {}).get(item, {})
    base, scale = patch.get("base", base), patch.get("T", scale)
    origin = patch.get("I0", 10000)
    if inventory < origin:
        kind, frac = patch.get("below_func", below), patch.get("below_target", bt)
        value = base + frac * base / _shape(kind, scale, scale) * _shape(kind, origin - inventory, scale)
    else:
        kind, frac = patch.get("above_func", above), patch.get("above_target", at)
        value = base - frac * base / _shape(kind, scale, scale) * _shape(kind, inventory - origin, scale)
    return max(1, int(round(value)))


def reserve_price(item, day, shed_total, market_params=None):
    """Marginal price below which we would rather hold this unit."""
    base = (market_params or {}).get(item, {}).get("base", PARAMS[item][0])
    frac = RESERVE_FRAC[item]
    # Ramp to zero across the closing days: unsold inventory is worth nothing.
    days_left = LAST_DAY - day
    if days_left <= 0:
        return 0.0
    if days_left < LIQUIDATE_DAYS:
        frac *= days_left / float(LIQUIDATE_DAYS)
    # The shed discards overflow at end of day, so pressure overrides patience.
    if shed_total > SHED_PRESSURE:
        frac *= max(0.0, (SHED_CAP - shed_total) / float(SHED_CAP - SHED_PRESSURE))
    return frac * base


def metered_sale(item, stock, inventory, day, shed_total, market_params=None, cash_target=0.0):
    """Units to sell now, and the cash they raise.

    Sells unit by unit while the marginal price holds at or above the reserve.
    `cash_target` forces additional units through the reserve when cash is
    needed for a purchase this turn.
    """
    if stock <= 0:
        return 0, 0.0
    floor = reserve_price(item, day, shed_total, market_params)
    sold = 0
    cash = 0.0
    inv = inventory
    while sold < stock:
        unit = price(item, inv, market_params)
        if unit < floor and cash >= cash_target:
            break
        sold += 1
        cash += unit
        if unit > 1:  # sales at the $1 floor do not add market supply
            inv += 1
    return sold, cash
