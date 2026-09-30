"""V7: melon-rush opening, then a closed-loop farm.

Why this shape, from measurements on the official interpreter and the recorded
opponent pool:

Melon is the only product the town barely drains -- no shop demands it and the
town centre takes one a day -- so its market inventory is almost entirely
player-driven, and its glut curve is quadratic with T=300: the price hits the
$1 floor 158 units above equilibrium. Melon is therefore a race, and arriving
first matters far more than arriving with more. Against the recorded pool,
24 tiles harvested at age 8 (4 units each) and sold on day 9 is worth +15,709
of margin, while the same tiles harvested at age 10 for 6 units each and sold
on day 11 is worth -6,251, because by then the opponents have already sold.
They sell on day 10.

So the opening spends the starting quadrant and most of the starting cash on
melons, waters them through their bonus window, harvests on day 8 and sells on
day 9 into an untouched market. That also solves the problem that killed the
earlier from-scratch attempt: it enters the midgame with roughly 20k rather
than scrabbling for cash while its crops turned to weeds.

From day 9 the farm is built properly: land bought out, ~10 hands (Fibonacci
hire pricing makes that the affordable ceiling), and a crop and animal mix
ranked by value per *action*, since labour and not land is the binding
constraint. CARE is treated as a first-class task because it banks a bonus on
the animal's next yield, roughly doubling a goose's output and tripling a
cow's, for one action and no resources.
"""
from __future__ import annotations

import math
import os

BOARD = 10
TURNS_PER_DAY = 24
LAST_DAY = 29
SHED_CAP = 100
MAX_ORDERS = 10
I0 = 10000

CROPS = {
    "WHEAT": dict(seed=10, first=2, maxday=4, interval=0, maxy=6, ongoing=False),
    "CARROT": dict(seed=20, first=2, maxday=3, interval=0, maxy=4, ongoing=False),
    "TOMATO": dict(seed=50, first=8, maxday=8, interval=1, maxy=4, ongoing=True),
    "STRAWBERRY": dict(seed=100, first=10, maxday=10, interval=2, maxy=4, ongoing=True),
    "MELON": dict(seed=80, first=10, maxday=12, interval=0, maxy=6, ongoing=False),
}
ANIMALS = {
    "GOOSE": dict(cost=300, structure="COOP", first=4, interval=1, maxheld=4, product="EGG"),
    "COW": dict(cost=400, structure="PASTURE", first=8, interval=2, maxheld=6, product="MILK"),
    "SHEEP": dict(cost=500, structure="PASTURE", first=6, interval=3, maxheld=6, product="WOOL"),
}
PRODUCTS = tuple(CROPS) + ("EGG", "MILK", "WOOL", "FERTILIZER")
STRUCT_OF = {a: d["structure"] for a, d in ANIMALS.items()}
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
SHOPS = {
    "BAKERY": ("EGG", "WHEAT"),
    "BRUNCH_SPOT": ("EGG", "WHEAT", "STRAWBERRY"),
    "FARMERS_MARKET": ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY"),
    "ICE_CREAM_SHOP": ("STRAWBERRY", "MILK", "WHEAT"),
    "PET_CAFE": ("CARROT",),
    "PIZZA_SHOP": ("MILK", "TOMATO", "WHEAT"),
    "SMOOTHIE_SHOP": ("STRAWBERRY", "MILK"),
    "YARN_STORE": ("WOOL",),
}


def _e(name, default, cast=float):
    try:
        return cast(os.environ.get(name, default))
    except (TypeError, ValueError):
        return cast(default)


CFG = {
    "melon_tiles": _e("V7_MELON_TILES", 24, int),
    # The engine refuses HARVEST before first_yield_day, which is 10 for melon,
    # so an age-8 harvest is impossible however well fertilized. The race is
    # therefore decided *within* day 10, by the turn: harvest at the top of the
    # day, drop into the shed and sell before the opponent's units have walked
    # to their own fields. Melons are planted next to the shed for that reason
    # -- a melon on a shed-access tile is harvested and dropped without a step.
    "melon_harvest_age": _e("V7_MELON_AGE", 10, int),
    "melon_reserve": _e("V7_MELON_RESERVE", 250.0),   # cash kept back on day 0
    "hands_open": _e("V7_HANDS_OPEN", 6, int),
    "hands": _e("V7_HANDS", 11, int),
    "crop_per_unit": _e("V7_CROP_PER_UNIT", 6.0),
    "animal_per_unit": _e("V7_ANIMAL_PER_UNIT", 1.4),
    "dist_weight": _e("V7_DW", 0.85),
    "crit_dist_weight": _e("V7_CDW", 0.22),
    "sell_floor_frac": _e("V7_FLOOR", 0.30),
    "liquidate_days": _e("V7_LIQ", 2, int),
    "wheat_buffer_days": _e("V7_WHEAT_BUF", 2.2),
    "fert_hold": _e("V7_FERT_HOLD", 6, int),
    "cash_reserve": _e("V7_RESERVE", 300.0),
}
_STATE = {}


# --------------------------------------------------------------------------- prices


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


def price(item, inventory, params=None):
    base, scale, below, bt, above, at = PARAMS[item]
    patch = (params or {}).get(item, {})
    base, scale = patch.get("base", base), patch.get("T", scale)
    origin = patch.get("I0", I0)
    if inventory < origin:
        kind, frac = patch.get("below_func", below), patch.get("below_target", bt)
        value = base + frac * base / _shape(kind, scale, scale) * _shape(kind, origin - inventory, scale)
    else:
        kind, frac = patch.get("above_func", above), patch.get("above_target", at)
        value = base - frac * base / _shape(kind, scale, scale) * _shape(kind, inventory - origin, scale)
    return max(1, int(round(value)))


def drain_per_day(shops):
    out = {p: 1.0 for p in PRODUCTS}
    out["FERTILIZER"] = 0.0
    for shop in shops:
        products = SHOPS.get(shop, ())
        rate = 12.0 if len(products) == 1 else 6.0
        for p in products:
            out[p] = out.get(p, 0.0) + rate
    return out


# --------------------------------------------------------------------------- board


def _shed_tiles():
    h = BOARD // 2
    return [(h - 1, h - 1), (h, h - 1), (h - 1, h), (h, h)]


def _dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _step_toward(pos, target):
    if pos[0] < target[0]:
        return ["EAST"]
    if pos[0] > target[0]:
        return ["WEST"]
    if pos[1] < target[1]:
        return ["SOUTH"]
    if pos[1] > target[1]:
        return ["NORTH"]
    return ["PASS"]


def survey(farm):
    crops = {c: 0 for c in CROPS}
    animals = {a: 0 for a in ANIMALS}
    free_struct = {"COOP": [], "PASTURE": []}
    empties, weeds = [], []
    unlocked = 0
    for y in range(BOARD):
        for x in range(BOARD):
            tile = farm["tiles"][y][x]
            if tile == "LOCKED":
                continue
            unlocked += 1
            if tile is None:
                empties.append((x, y))
            elif tile.get("kind") == "WEED":
                weeds.append((x, y))
            elif tile.get("kind") == "PLANT":
                crops[tile["crop"]] = crops.get(tile["crop"], 0) + 1
            elif tile.get("animal"):
                animals[tile["animal"]] = animals.get(tile["animal"], 0) + 1
            elif tile.get("kind") in free_struct:
                free_struct[tile["kind"]].append((x, y))
    return crops, animals, free_struct, empties, weeds, unlocked


def _water_window(tile, day):
    """One-time crops gain a unit per watered day inside this window."""
    cd = CROPS[tile["crop"]]
    if cd["ongoing"]:
        return False
    age = day - tile["planted_day"]
    return ((cd["maxday"] + 1) // 2) <= age <= cd["maxday"]


def _harvest_age(crop, melon_age):
    """Age at which a one-time crop is worth taking."""
    if crop == "MELON":
        return melon_age
    return CROPS[crop]["maxday"]


# --------------------------------------------------------------------------- planner

CROP_YIELD = {"WHEAT": (4.0, 5.0), "CARROT": (3.0, 4.0), "MELON": (6.0, 11.0),
              "TOMATO": (4.0, 13.0), "STRAWBERRY": (4.0, 18.0)}
CROP_ACTIONS = {"WHEAT": 1.25, "CARROT": 1.30, "MELON": 1.10, "TOMATO": 1.08, "STRAWBERRY": 1.06}
ANIMAL_RATE = {"GOOSE": (2.0, 3.0), "COW": (1.5, 2.6), "SHEEP": (1.33, 2.4)}


def crop_units(crop, days_left):
    """Units a planting made today will actually deliver before the season ends.

    Ranking crops by their full-cycle yield built a 66-tile strawberry
    monoculture: strawberry only starts yielding at age 10 and then every other
    day, so one planted on day 18 delivers a single unit and one planted on day
    20 delivers nothing, while the planner still valued it at four. A crop is
    worth what it can finish, not what it could finish given a longer season.
    """
    cd = CROPS[crop]
    if days_left < cd["first"]:
        return 0.0
    if not cd["ongoing"]:
        # One-time crops are harvested at max_yield_day; earlier means less.
        if days_left >= cd["maxday"]:
            return CROP_YIELD[crop][0]
        watered = max(0, days_left - ((cd["maxday"] + 1) // 2) + 1)
        return min(CROP_YIELD[crop][0], 1.0 + watered)
    ticks = (days_left - cd["first"]) // max(1, cd["interval"]) + 1
    return float(min(cd["maxy"], max(0, ticks)))


def animal_units(animal, days_left):
    """Units an animal bought today will deliver, with CARE banked each day."""
    ad = ANIMALS[animal]
    if days_left <= ad["first"]:
        return 0.0
    ticks = (days_left - ad["first"]) // ad["interval"] + 1
    per_tick = 1.0 + ad["interval"]          # CARE banks one bonus per day fed
    return float(ticks) * min(per_tick, ad["maxheld"])


def plan_shares(obs, shops, params, day):
    """Production shares ranked by value per action, charged against the curve.

    Labour is the binding constraint, not land, so options are ranked per
    action rather than per tile. Each unit added is priced against the
    inventory its own output will create, and the town's drain is subtracted
    first, because the products the town consumes stay scarce and sell above
    base while the ones it ignores collapse.
    """
    horizon = max(4, min(20, LAST_DAY - day))
    drain = drain_per_day(shops)
    inv = dict(obs["market"]["inventory"])
    wheat_price = price("WHEAT", inv.get("WHEAT", I0), params)
    proj = {p: max(0.0, inv.get(p, I0) - drain.get(p, 0.0) * horizon) for p in PRODUCTS}
    added = {p: 0.0 for p in PRODUCTS}

    days_left = LAST_DAY - day
    options = []
    for crop, cd in CROPS.items():
        units = crop_units(crop, days_left)
        if units <= 0:
            continue
        tiledays = min(CROP_YIELD[crop][1], max(1.0, float(days_left)))
        cycles = max(1.0, days_left / CROP_YIELD[crop][1]) if not cd["ongoing"] else 1.0
        total = units * cycles
        occupancy = min(float(days_left), CROP_YIELD[crop][1] * cycles)
        options.append((crop, crop, total / occupancy, CROP_ACTIONS[crop],
                        cd["seed"] * cycles / occupancy, 0.0))
    for animal, ad in ANIMALS.items():
        units = animal_units(animal, days_left)
        if units <= 0:
            continue
        _rate, acts = ANIMAL_RATE[animal]
        options.append((animal, ad["product"], units / float(days_left), acts,
                        ad["cost"] / float(days_left), wheat_price))
    if not options:
        return {"WHEAT": 1.0}, {}

    counts = {k: 0 for k in list(CROPS) + list(ANIMALS)}
    for _ in range(80):
        best = None
        for name, product, per_day, acts, upkeep, feed in options:
            if per_day <= 0:
                continue
            unit_price = price(product, int(proj[product] + added[product]), params)
            per_action = (per_day * unit_price - upkeep - feed) / acts
            if best is None or per_action > best[0]:
                best = (per_action, name, product, per_day)
        if best is None or best[0] <= 0:
            break
        _pa, name, product, per_day = best
        counts[name] += 1
        added[product] += per_day * horizon

    ctot = sum(counts[c] for c in CROPS) or 1
    atot = sum(counts[a] for a in ANIMALS) or 1
    crop_shares = {c: counts[c] / ctot for c in CROPS if counts[c]}
    animal_shares = {a: counts[a] / atot for a in ANIMALS if counts[a]}
    return (crop_shares or {"WHEAT": 1.0}), animal_shares


# --------------------------------------------------------------------------- tasks


def build_tasks(farm, obs, day, crop_target, animal_target, crops, animals,
                free_struct, empties, weeds, shed, bags, crop_cap, melon_age):
    tasks = []
    add = tasks.append
    for y in range(BOARD):
        for x in range(BOARD):
            tile = farm["tiles"][y][x]
            if not isinstance(tile, dict):
                continue
            pos = (x, y)
            kind = tile.get("kind")
            if kind == "PLANT":
                cd = CROPS[tile["crop"]]
                age = day - tile["planted_day"]
                if not tile.get("watered_today"):
                    if tile.get("consecutive_unwatered", 0) >= 1:
                        add((250.0, pos, "WATER", None))      # weeds tonight otherwise
                    elif _water_window(tile, day):
                        add((90.0, pos, "WATER", None))       # a watered day is a unit
                    elif cd["ongoing"]:
                        add((10.0, pos, "WATER", None))       # no yield gain; alternate days
                    else:
                        add((14.0, pos, "WATER", None))
                if tile.get("yield_units", 0) > 0 and age >= cd["first"]:
                    if cd["ongoing"]:
                        add((95.0 + 12.0 * tile["yield_units"], pos, "HARVEST", None))
                    elif age >= _harvest_age(tile["crop"], melon_age):
                        # Melon is a race: taking it early and selling a day
                        # ahead of the opponent beats taking more of it later.
                        bonus = 120.0 if tile["crop"] == "MELON" else 0.0
                        add((100.0 + 12.0 * tile["yield_units"] + bonus, pos, "HARVEST", None))
                if (not cd["ongoing"] and tile.get("fertilized_until_day", -1) < day
                        and _water_window(tile, day) and shed.get("FERTILIZER", 0) > CFG["fert_hold"]):
                    add((30.0, pos, "FERTILIZE", None))
            elif tile.get("animal"):
                ad = ANIMALS[tile["animal"]]
                if not tile.get("fed_today"):
                    add((260.0 if tile.get("consecutive_unfed", 0) >= 1 else 70.0,
                         pos, "FEED", None))
                held = tile.get("yield_units", 0)
                if held >= ad["maxheld"]:
                    add((150.0, pos, "HARVEST", None))        # at the cap, yield is lost
                elif held > 0:
                    add((55.0 + 14.0 * held, pos, "HARVEST", None))
                if not tile.get("cared_today"):
                    add((60.0, pos, "CARE", None))            # doubles/triples output
                if tile.get("fertilizer_available"):
                    add((16.0, pos, "COLLECT_FERTILIZER", None))
            elif kind == "WEED":
                # A weed holds its tile for the rest of the season, and they
                # spread through unworked land: 49 of them by day 29 in an
                # earlier build. Worth clearing once they accumulate.
                add((20.0 + 4.0 * min(len(weeds), 12), pos, "DIG", None))
            elif kind in free_struct:
                add((110.0, pos, "PLACE_STRUCT", kind))

    room = max(0, crop_cap - sum(crops.values()))
    wants = []
    for crop, want in crop_target.items():
        gap = want - crops.get(crop, 0)
        if gap > 0 and crop_units(crop, LAST_DAY - day) > 0:
            wants.append((gap, crop))
    wants.sort(reverse=True)
    order = tuple(c for _g, c in wants)
    if order and room > 0:
        melon_open = day <= 1 and order[0] == "MELON"
        spots = empties
        if melon_open:
            access = _shed_tiles()
            spots = sorted(empties, key=lambda q: min(_dist(q, t) for t in access))
        value = 200.0 if melon_open else 58.0
        for pos in spots[:room]:
            add((value, pos, "PLANT_ANY", order))

    need = {}
    for animal, want in animal_target.items():
        gap = want - animals.get(animal, 0)
        if gap > 0 and ANIMALS[animal]["first"] <= LAST_DAY - day:
            st = STRUCT_OF[animal]
            need[st] = need.get(st, 0) + gap
    for st in list(need):
        need[st] = max(0, need[st] - len(free_struct.get(st, ())))
    idx = room
    for st, gap in need.items():
        for _ in range(gap):
            if idx >= len(empties):
                break
            add((40.0, empties[idx], "BUILD", st))
            idx += 1

    # Harvested produce sits in a unit's inventory and cannot be sold from
    # there. On melon-selling day that delivery is the most valuable action on
    # the board, because the price decays with every unit either side sells.
    access = [t for t in _shed_tiles() if farm["tiles"][t[1]][t[0]] != "LOCKED"] or _shed_tiles()
    for bag in bags:
        if bag.get("MELON", 0) > 0:
            for pos in access:
                add((240.0, pos, "DELIVER", None))
            break
    unfed = sum(1 for t in tasks if t[2] == "FEED")
    wheat_carried = sum(b.get("WHEAT", 0) for b in bags)
    if unfed > wheat_carried and shed.get("WHEAT", 0) > 0:
        for pos in access:
            add((200.0, pos, "GET_WHEAT", None))
    waiting = [a for a in ANIMALS if shed.get(a, 0) > 0 and free_struct.get(STRUCT_OF[a])]
    if waiting:
        for pos in access:
            add((130.0, pos, "GET_ANIMAL", tuple(waiting)))
    if shed.get("FERTILIZER", 0) > CFG["fert_hold"]:
        for pos in access:
            add((28.0, pos, "GET_FERT", None))
    return tasks


def assign(farm, obs, tasks, seeds, shed, day, prev):
    positions = [tuple(farm["farmer"])] + [tuple(p) for p in farm["hands"]]
    bags = obs["private"].get("inventories", [])
    n = len(positions)
    cmds = [None] * n
    seed_pool = dict(seeds)
    shed_pool = dict(shed)
    open_tasks = list(tasks)
    free_units = list(range(n))

    def feasible(kind, arg, bag):
        if kind == "FEED":
            return bag.get("WHEAT", 0) > 0
        if kind == "FERTILIZE":
            return bag.get("FERTILIZER", 0) > 0
        if kind == "PLANT_ANY":
            return any(seed_pool.get(c, 0) > 0 for c in arg)
        if kind == "PLACE_STRUCT":
            return any(bag.get(a, 0) > 0 for a, st in STRUCT_OF.items() if st == arg)
        if kind == "GET_WHEAT":
            return shed_pool.get("WHEAT", 0) > 0 and bag.get("WHEAT", 0) < 5
        if kind == "GET_ANIMAL":
            return any(shed_pool.get(a, 0) > 0 for a in arg)
        if kind == "GET_FERT":
            return shed_pool.get("FERTILIZER", 0) > CFG["fert_hold"] and bag.get("FERTILIZER", 0) < 3
        if kind == "DELIVER":
            return sum(bag.values()) > 0
        return True

    while free_units and open_tasks:
        best = None
        for ui in free_units:
            bag = bags[ui] if ui < len(bags) else {}
            pos = positions[ui]
            for ti, (value, tpos, kind, arg) in enumerate(open_tasks):
                if not feasible(kind, arg, bag):
                    continue
                # Value per action spent getting there: one action per step plus
                # the job itself. Urgent work is allowed to reach further.
                weight = CFG["crit_dist_weight"] if value >= 200.0 else CFG["dist_weight"]
                score = value / (1.0 + weight * _dist(pos, tpos))
                if prev.get(ui) == (tpos, kind):
                    score *= 1.15
                if best is None or score > best[0]:
                    best = (score, ui, ti)
        if best is None:
            break
        _s, ui, ti = best
        value, tpos, kind, arg = open_tasks.pop(ti)
        pos = positions[ui]
        bag = bags[ui] if ui < len(bags) else {}
        prev[ui] = (tpos, kind)
        if pos != tpos:
            cmds[ui] = _step_toward(pos, tpos)
        elif kind == "PLANT_ANY":
            crop = next((c for c in arg if seed_pool.get(c, 0) > 0), None)
            if crop:
                seed_pool[crop] -= 1
                cmds[ui] = ["PLANT", crop]
            else:
                cmds[ui] = ["PASS"]
        elif kind == "PLACE_STRUCT":
            animal = next((a for a, st in STRUCT_OF.items() if st == arg and bag.get(a, 0) > 0), None)
            cmds[ui] = ["PLACE", animal, 1] if animal else ["PASS"]
        elif kind == "BUILD":
            cmds[ui] = ["BUILD_PASTURE"] if arg == "PASTURE" else ["BUILD_COOP"]
        elif kind == "GET_WHEAT":
            take = min(6, shed_pool.get("WHEAT", 0))
            shed_pool["WHEAT"] = shed_pool.get("WHEAT", 0) - take
            cmds[ui] = ["PICKUP", "WHEAT", take] if take else ["PASS"]
        elif kind == "GET_ANIMAL":
            animal = next((a for a in arg if shed_pool.get(a, 0) > 0), None)
            if animal:
                shed_pool[animal] -= 1
                cmds[ui] = ["PICKUP", animal, 1]
            else:
                cmds[ui] = ["PASS"]
        elif kind == "DELIVER":
            cmds[ui] = ["DROP"]
        elif kind == "GET_FERT":
            take = min(3, shed_pool.get("FERTILIZER", 0))
            shed_pool["FERTILIZER"] = shed_pool.get("FERTILIZER", 0) - take
            cmds[ui] = ["PICKUP", "FERTILIZER", take] if take else ["PASS"]
        else:
            cmds[ui] = [kind]
        free_units.remove(ui)

    access = [t for t in _shed_tiles() if farm["tiles"][t[1]][t[0]] != "LOCKED"] or _shed_tiles()
    for ui in free_units:
        bag = bags[ui] if ui < len(bags) else {}
        pos = positions[ui]
        if sum(bag.values()) > 0:
            target = min(access, key=lambda t: _dist(pos, t))
            cmds[ui] = ["DROP"] if pos == target else _step_toward(pos, target)
        else:
            cmds[ui] = ["PASS"]
    return [c if c else ["PASS"] for c in cmds]


# --------------------------------------------------------------------------- market


def _fib(n):
    a, b = 1, 1
    for _ in range(max(0, n)):
        a, b = b, a + b
    return a


def market_orders(obs, farm, day, hour, crop_target, animal_target, crops, animals,
                  free_struct, empties, seeds, shed, hands_target, config, melon_phase):
    params = obs["market"].get("params")
    inv = dict(obs["market"]["inventory"])
    cash = float(farm["money"])
    shed = dict(shed)
    last = day >= LAST_DAY
    mult = int(config.get("farmHandCostMult", 1))

    animal_count = sum(animals.values())
    wheat_need = 0 if last else int(math.ceil(animal_count * CFG["wheat_buffer_days"]))
    fert_hold = 0 if last else CFG["fert_hold"]
    hires = 0 if last or hour > 1 else max(0, hands_target - len(farm["hands"]))

    # ---- sell first: cash now compounds, and prompt selling also keeps the
    # shed clear, since end-of-day overflow past 100 items is discarded.
    frac = 0.0 if (LAST_DAY - day) < CFG["liquidate_days"] else CFG["sell_floor_frac"]
    shed_total = sum(v for v in shed.values() if v > 0)
    if shed_total > 88:
        frac *= max(0.0, (SHED_CAP - shed_total) / 12.0)
    queue = []
    for item in PRODUCTS:
        hold = wheat_need if item == "WHEAT" else (fert_hold if item == "FERTILIZER" else 0)
        stock = shed.get(item, 0) - hold
        if stock > 0:
            queue.append((stock * price(item, inv[item], params), item, stock))
    queue.sort(reverse=True)
    sells = []
    for _v, item, stock in queue:
        floor = frac * PARAMS[item][0]
        sold = 0
        gained = 0.0
        level = inv[item]
        while sold < stock:
            unit = price(item, level, params)
            if unit < floor:
                break
            sold += 1
            gained += unit
            if unit > 1:
                level += 1
        if sold:
            sells.append(["SELL", item, sold])
            cash += gained
            inv[item] = level
            shed[item] = shed.get(item, 0) - sold

    out = []
    for j in range(hires):
        cost = _fib(farm["hires_today"] + j) * mult
        if len(out) >= MAX_ORDERS or cash < cost:
            break
        out.append(["HIRE"])
        cash -= cost
    for order in sells:
        if len(out) >= MAX_ORDERS:
            break
        out.append(order)

    # ---- the melon opening spends nearly everything on seeds: the race is
    # decided on day 0 and nothing else bought this early returns as much.
    if melon_phase:
        planted = crops.get("MELON", 0)
        want = CFG["melon_tiles"] - planted - seeds.get("MELON", 0)
        room = len(empties)
        budget = max(0.0, cash - CFG["melon_reserve"])
        qty = min(want, room, int(budget // CROPS["MELON"]["seed"]))
        if qty > 0 and len(out) < MAX_ORDERS:
            out.append(["BUY_SEED", "MELON", qty])
            cash -= qty * CROPS["MELON"]["seed"]
        return out[:MAX_ORDERS]

    reserve = 0.0 if last else CFG["cash_reserve"]
    land = len(farm["unlocked_quadrants"])
    land_cost = (1000, 2000, 4000)[land - 1] if 1 <= land <= 3 else None
    if land_cost is not None and not last and day <= 21 and len(out) < MAX_ORDERS:
        if cash >= land_cost + reserve:
            out.append(["BUY_LAND"])
            cash -= land_cost

    buys = []
    if not last:
        short = wheat_need - shed.get("WHEAT", 0)
        if short > 0:
            buys.append(("BUY_PRODUCT", "WHEAT", min(short, 20), None))
    for animal, ad in ANIMALS.items():
        if ad["first"] > LAST_DAY - day:
            continue
        gap = animal_target.get(animal, 0) - animals.get(animal, 0) - shed.get(animal, 0)
        st = STRUCT_OF[animal]
        waiting = sum(shed.get(a, 0) for a, s in STRUCT_OF.items() if s == st)
        qty = min(gap, max(0, len(free_struct.get(st, ())) - waiting), 2)
        if qty > 0:
            buys.append(("BUY_ANIMAL", animal, qty, ad["cost"]))
    plantable = len(empties) + 3
    for crop, want in crop_target.items():
        if plantable <= 0 or CROPS[crop]["first"] > LAST_DAY - day:
            continue
        gap = want - crops.get(crop, 0) - seeds.get(crop, 0)
        qty = min(gap, plantable, 8)
        if qty > 0:
            buys.append(("BUY_SEED", crop, qty, CROPS[crop]["seed"]))
            plantable -= qty

    for op, item, qty, unit in buys:
        if len(out) >= MAX_ORDERS:
            break
        room = max(0, SHED_CAP - sum(v for v in shed.values() if v > 0)) if op != "BUY_SEED" else qty
        count = 0
        for _ in range(min(qty, room)):
            unit_cost = unit if unit is not None else price(item, inv[item] - 1, params)
            if unit_cost + reserve > cash:
                break
            count += 1
            cash -= unit_cost
            if op == "BUY_PRODUCT":
                inv[item] -= 1
            if op != "BUY_SEED":
                shed[item] = shed.get(item, 0) + 1
        if count:
            out.append([op, item, count])
    return out[:MAX_ORDERS]


# --------------------------------------------------------------------------- entry


def agent(obs, configuration=None):
    config = configuration or {}
    seat = int(obs.get("player", 0))
    farm = obs["farms"][seat]
    day, hour = obs["day"], obs["hour"]
    step = int(obs.get("step", TURNS_PER_DAY * day + hour))
    params = obs["market"].get("params")
    shops = list(obs.get("town", {}).get("unlocked_shops", []))

    state = _STATE.get(seat)
    if state is None or step == 0 or step <= state.get("step", -1):
        state = {"step": -1, "shops": None, "shares": None, "prev": {}, "day": -1}
        _STATE[seat] = state
    state["step"] = step
    if state["day"] != day:
        state["prev"] = {}
        state["day"] = day

    crops, animals, free_struct, empties, weeds, unlocked = survey(farm)
    shed = dict(obs["private"].get("shed", {}))
    seeds = dict(obs["private"].get("seeds", {}))
    bags = obs["private"].get("inventories", [])
    melon_age = CFG["melon_harvest_age"]

    # Days 0-1 are the melon race; everything else waits.
    melon_phase = day <= 1
    if melon_phase:
        crop_target = {"MELON": CFG["melon_tiles"]}
        animal_target = {}
        crop_cap = CFG["melon_tiles"]
        hands_target = CFG["hands_open"]
    else:
        if state["shares"] is None or state["shops"] != shops:
            state["shares"] = plan_shares(obs, shops, params, day)
            state["shops"] = list(shops)
        crop_shares, animal_shares = state["shares"]
        workload = sum(crops.values()) * 1.2 + sum(animals.values()) * 2.6 + min(len(empties), 14)
        hands_target = min(CFG["hands"], max(3, int(math.ceil(workload / 6.5))))
        work_units = hands_target + 1
        animal_cap = min(int(work_units * CFG["animal_per_unit"]), max(0, unlocked - 12))
        crop_cap = min(int(work_units * CFG["crop_per_unit"]), max(0, unlocked - animal_cap))
        crop_target = {c: int(round(s * crop_cap)) for c, s in crop_shares.items()}
        animal_target = {a: int(round(s * animal_cap)) for a, s in animal_shares.items()}

    tasks = build_tasks(farm, obs, day, crop_target, animal_target, crops, animals,
                        free_struct, empties, weeds, shed, bags, crop_cap, melon_age)
    cmds = assign(farm, obs, tasks, seeds, shed, day, state["prev"])
    market = market_orders(obs, farm, day, hour, crop_target, animal_target, crops,
                           animals, free_struct, empties, seeds, shed, hands_target,
                           config, melon_phase)
    return {"farmer": cmds[0] if cmds else ["PASS"], "hands": cmds[1:], "market": market}
