"""V5: closed-loop score optimiser, rebuilt around the measured failure modes.

V2 collapsed for four diagnosable reasons, each addressed here:

1. It planted far beyond what its workforce could water, so crops turned to
   weeds (36 weeds by day 6). Plantings are now capped by a labour capacity
   derived from the units actually on the board.
2. It spent every dollar on seeds, so it could not hire, so it could not water.
   Hiring is now funded first: it is Fibonacci-priced and ~143/day for ten
   hands, the cheapest multiplier in the game.
3. Its targets thrashed (SHEEP 4 -> 16 -> 28 on consecutive samples), which
   built 26 pastures that were never filled. The mix is now computed as stable
   *shares* and only revised when the town changes.
4. 68% of its actions were movement. Units are now assigned vertical zones, so
   each works a short column instead of crossing the board.
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
STRUCT_OF = {a: d["structure"] for a, d in ANIMALS.items()}

# Units produced and tile-days occupied per planting; actions per tile-day.
CROP_YIELD = {"WHEAT": (4.0, 5.0), "CARROT": (3.0, 4.0), "MELON": (6.0, 11.0),
              "TOMATO": (4.0, 13.0), "STRAWBERRY": (4.0, 18.0)}
CROP_ACTIONS = {"WHEAT": 1.25, "CARROT": 1.30, "MELON": 1.10, "TOMATO": 1.08, "STRAWBERRY": 1.06}
ANIMAL_RATE = {"GOOSE": (2.0, 3.0), "COW": (1.5, 2.6), "SHEEP": (1.33, 2.4)}


def _env(name, default, cast=float):
    try:
        return cast(os.environ.get(name, default))
    except (TypeError, ValueError):
        return cast(default)


CFG = {
    # labour
    "hands_max": _env("V5_HANDS", 11, int),
    "hands_early": 5,
    "crop_per_unit": _env("V5_CROP_PER_UNIT", 6.2),
    "animal_per_unit": _env("V5_ANIMAL_PER_UNIT", 1.5),
    # routing
    "dist_penalty": _env("V5_DIST", 3.0),
    "zone_penalty": _env("V5_ZONE", 28.0),
    "stick_bonus": 8.0,
    # market
    "sell_floor_frac": _env("V5_FLOOR", 0.28),
    "liquidate_days": 2,
    "wheat_buffer_days": _env("V5_WHEAT_BUF", 2.2),
    "fert_hold": 8,
    "seed_buffer": 4,
    "cash_reserve": _env("V5_RESERVE", 400.0),
    "land_deadline": 21,
    "critical_value": 150.0,
    "dist_weight": _env("V5_DW", 0.85),
    "crit_dist_weight": _env("V5_CDW", 0.22),
    "zone_factor": _env("V5_ZF", 0.68),
    "stick_factor": 1.18,
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


# --------------------------------------------------------------------------- mix


def plan_shares(obs, shops, params, day):
    """Stable production shares, revised only when the town changes.

    Greedy marginal value per action over a nominal 80-tile farm, charging each
    added unit against the price curve its own output will depress. Returns
    crop shares (summing to 1) and animal shares, not absolute counts, so the
    executor can scale them to whatever labour is actually available.
    """
    horizon = max(4, min(20, LAST_DAY - day))
    drain = drain_per_day(shops)
    inv = dict(obs["market"]["inventory"])
    wheat_price = price("WHEAT", inv.get("WHEAT", I0), params)
    proj = {p: max(0.0, inv.get(p, I0) - drain.get(p, 0.0) * horizon) for p in PRODUCTS}
    added = {p: 0.0 for p in PRODUCTS}

    options = []
    for crop, cd in CROPS.items():
        if cd["first"] > LAST_DAY - day:
            continue
        units, tiledays = CROP_YIELD[crop]
        cycles = max(1.0, horizon / tiledays)
        options.append(("CROP", crop, crop, units * cycles / horizon,
                        CROP_ACTIONS[crop], cd["seed"] * cycles / horizon, 0.0))
    for animal, ad in ANIMALS.items():
        if ad["first"] > LAST_DAY - day:
            continue
        rate, acts = ANIMAL_RATE[animal]
        ramp = max(0.0, (horizon - ad["first"]) / horizon)
        options.append(("ANIMAL", animal, ad["product"], rate * ramp, acts,
                        ad["cost"] / horizon, wheat_price))
    if not options:
        return {"WHEAT": 1.0}, {}

    counts = {k: 0 for k in list(CROPS) + list(ANIMALS)}
    for _ in range(80):
        best = None
        for kind, name, product, per_day, acts, upkeep, feed in options:
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

    crop_total = sum(counts[c] for c in CROPS) or 1
    animal_total = sum(counts[a] for a in ANIMALS) or 1
    crop_shares = {c: counts[c] / crop_total for c in CROPS if counts[c]}
    animal_shares = {a: counts[a] / animal_total for a in ANIMALS if counts[a]}
    if not crop_shares:
        crop_shares = {"WHEAT": 1.0}
    return crop_shares, animal_shares


# --------------------------------------------------------------------------- survey


def survey(farm, day):
    """One pass over the board: counts, and the tile lists the planner needs."""
    crops = {c: 0 for c in CROPS}
    animals = {a: 0 for a in ANIMALS}
    free_struct = {"COOP": [], "PASTURE": []}
    empties = []
    weeds = []
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


def _in_window(tile, day):
    cd = CROPS[tile["crop"]]
    if cd["ongoing"]:
        return True
    age = day - tile["planted_day"]
    return ((cd["maxday"] + 1) // 2) <= age <= cd["maxday"]


def _harvestable(tile, day):
    cd = CROPS[tile["crop"]]
    if tile.get("yield_units", 0) <= 0:
        return False
    age = day - tile["planted_day"]
    if age < cd["first"]:
        return False
    return True if cd["ongoing"] else age >= cd["maxday"]


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


# --------------------------------------------------------------------------- tasks


def build_tasks(farm, day, crop_target, animal_target, crops, animals,
                free_struct, empties, weeds, seeds, shed, crop_cap, bags):
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
                cdata = CROPS[tile["crop"]]
                if not tile.get("watered_today"):
                    if tile.get("consecutive_unwatered", 0) >= 1:
                        # Unwatered twice and the tile becomes a weed.
                        add((200.0, pos, "WATER", None))
                    elif cdata["ongoing"]:
                        # No yield benefit; water it on the alternate day only.
                        add((12.0, pos, "WATER", None))
                    elif _in_window(tile, day):
                        # One-time crops gain a whole unit per watered day here.
                        add((75.0, pos, "WATER", None))
                    else:
                        add((22.0, pos, "WATER", None))
                if _harvestable(tile, day):
                    add((90.0 + 14.0 * tile.get("yield_units", 0), pos, "HARVEST", None))
                cd = CROPS[tile["crop"]]
                if (not cd["ongoing"] and tile.get("fertilized_until_day", -1) < day
                        and _in_window(tile, day) and shed.get("FERTILIZER", 0) > CFG["fert_hold"]):
                    add((20.0, pos, "FERTILIZE", None))
            elif tile.get("animal"):
                ad = ANIMALS[tile["animal"]]
                if not tile.get("fed_today"):
                    add((210.0 if tile.get("consecutive_unfed", 0) >= 1 else 66.0, pos, "FEED", None))
                held = tile.get("yield_units", 0)
                if held >= ad["maxheld"]:
                    # At the cap further production is silently lost.
                    add((150.0, pos, "HARVEST", None))
                elif held > 0:
                    add((60.0 + 16.0 * held, pos, "HARVEST", None))
                if not tile.get("cared_today"):
                    # CARE banks a bonus paid on the next yield: it roughly
                    # doubles goose output and triples a cow's, for one action.
                    add((40.0, pos, "CARE", None))
                if tile.get("fertilizer_available"):
                    add((15.0, pos, "COLLECT_FERTILIZER", None))
            elif kind == "WEED":
                add((24.0 if len(weeds) < 6 else 40.0, pos, "DIG", None))
            elif kind in free_struct:
                add((95.0, pos, "PLACE_STRUCT", kind))

    # Plant toward the mix, but never beyond what the workforce can water.
    room = max(0, crop_cap - sum(crops.values()))
    wants = []
    for crop, want in crop_target.items():
        gap = want - crops.get(crop, 0)
        if gap > 0 and CROPS[crop]["first"] <= LAST_DAY - day:
            wants.append((gap, crop))
    wants.sort(reverse=True)
    order = tuple(c for _g, c in wants)
    if order and room > 0:
        for pos in empties[:room]:
            add((52.0, pos, "PLANT_ANY", order))

    # Build structures for animals we intend to own and can house.
    need = {}
    for animal, want in animal_target.items():
        gap = want - animals.get(animal, 0)
        if gap > 0 and ANIMALS[animal]["first"] <= LAST_DAY - day:
            structure = STRUCT_OF[animal]
            need[structure] = need.get(structure, 0) + gap
    for structure in list(need):
        need[structure] = max(0, need[structure] - len(free_struct.get(structure, ())))
    idx = room
    for structure, gap in need.items():
        for _ in range(gap):
            if idx >= len(empties):
                break
            add((34.0, empties[idx], "BUILD", structure))
            idx += 1

    # Supply trips. V5's first cut left pickups to idle units only, and with
    # every unit busy walking, animals sat in the shed (17 per game beside 14
    # empty pastures) and FEED fired 11 times in a whole season. Feeding is
    # existential -- an animal unfed twice escapes for good -- so the trip to
    # the shed is a task in its own right.
    access = [t for t in _shed_tiles() if farm["tiles"][t[1]][t[0]] != "LOCKED"] or _shed_tiles()
    unfed = sum(1 for t in tasks if t[2] == "FEED")
    wheat_in_bags = sum(b.get("WHEAT", 0) for b in bags)
    if unfed > wheat_in_bags and shed.get("WHEAT", 0) > 0:
        for pos in access:
            add((170.0, pos, "GET_WHEAT", None))
    waiting = [a for a in ANIMALS if shed.get(a, 0) > 0
               and free_struct.get(STRUCT_OF[a])]
    if waiting:
        for pos in access:
            add((120.0, pos, "GET_ANIMAL", tuple(waiting)))
    if shed.get("FERTILIZER", 0) > CFG["fert_hold"]:
        for pos in access:
            add((26.0, pos, "GET_FERT", None))
    return tasks


def assign(farm, obs, tasks, seeds, shed, day, prev, n_units):
    """Nearest-first routing inside per-unit zones.

    Value-minus-distance made units chase far-off high-value tiles and then
    re-decide next turn, spending 72% of actions walking. A tile action needs
    the unit standing on it, so a unit sweeping n adjacent tiles cannot beat
    n actions plus n-1 steps; the way to approach that floor is to always take
    the *nearest* job and to stay inside a zone. Value now only breaks ties,
    except for existential work (a plant about to become a weed, an animal
    about to escape), which is allowed to pull a unit across the board.
    """
    positions = [tuple(farm["farmer"])] + [tuple(p) for p in farm["hands"]]
    bags = obs["private"].get("inventories", [])
    n = len(positions)
    cmds = [None] * n
    seed_pool = dict(seeds)
    shed_pool = dict(shed)
    span = BOARD / float(max(1, n))
    zones = [(int(i * span), max(int((i + 1) * span) - 1, int(i * span))) for i in range(n)]
    critical = CFG["critical_value"]

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
        return True

    while free_units and open_tasks:
        best = None
        for ui in free_units:
            bag = bags[ui] if ui < len(bags) else {}
            pos = positions[ui]
            lo, hi = zones[ui] if ui < len(zones) else (0, BOARD - 1)
            for ti, (value, tpos, kind, arg) in enumerate(open_tasks):
                if not feasible(kind, arg, bag):
                    continue
                dist = _dist(pos, tpos)
                # Value per action spent: one action per step plus the job
                # itself. Pure proximity starved harvests (266 a game against
                # the tape's 594) because any nearer watering outranked them.
                weight = CFG["crit_dist_weight"] if value >= critical else CFG["dist_weight"]
                score = value / (1.0 + weight * dist)
                if not (lo <= tpos[0] <= hi):
                    score *= CFG["zone_factor"]
                if prev.get(ui) == (tpos, kind):
                    score *= CFG["stick_factor"]
                if best is None or score > best[0]:
                    best = (score, ui, ti)
        if best is None:
            break
        _score, ui, ti = best
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
        elif kind == "GET_FERT":
            take = min(3, shed_pool.get("FERTILIZER", 0))
            shed_pool["FERTILIZER"] = shed_pool.get("FERTILIZER", 0) - take
            cmds[ui] = ["PICKUP", "FERTILIZER", take] if take else ["PASS"]
        else:
            cmds[ui] = [kind]
        free_units.remove(ui)

    # Anything still idle drops its load so the shed can be sold from.
    for ui in free_units:
        bag = bags[ui] if ui < len(bags) else {}
        pos = positions[ui]
        if sum(bag.values()) > 0:
            access = [t for t in _shed_tiles() if farm["tiles"][t[1]][t[0]] != "LOCKED"] or _shed_tiles()
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
                  free_struct, empties, seeds, shed, hands_target, config):
    params = obs["market"].get("params")
    inv = dict(obs["market"]["inventory"])
    cash = float(farm["money"])
    shed = dict(shed)
    last = day >= LAST_DAY
    mult = int(config.get("farmHandCostMult", 1))
    reserve = 0.0 if last else CFG["cash_reserve"]

    animal_count = sum(animals.values())
    wheat_need = 0 if last else int(math.ceil(animal_count * CFG["wheat_buffer_days"]))
    fert_hold = 0 if last else CFG["fert_hold"]

    hires = 0 if last else max(0, hands_target - len(farm["hands"])) if hour <= 1 else 0

    buys = []
    if not last:
        short = wheat_need - shed.get("WHEAT", 0)
        if short > 0:
            buys.append(("BUY_PRODUCT", "WHEAT", min(short, 20), None))
    for animal, ad in ANIMALS.items():
        if ad["first"] > LAST_DAY - day:
            continue
        gap = animal_target.get(animal, 0) - animals.get(animal, 0) - shed.get(animal, 0)
        structure = STRUCT_OF[animal]
        waiting = sum(shed.get(a, 0) for a, s in STRUCT_OF.items() if s == structure)
        room = max(0, len(free_struct.get(structure, ())) - waiting)
        qty = min(gap, room, 2)
        if qty > 0:
            buys.append(("BUY_ANIMAL", animal, qty, ad["cost"]))
    plantable = len(empties) + CFG["seed_buffer"]
    for crop, want in crop_target.items():
        if plantable <= 0 or CROPS[crop]["first"] > LAST_DAY - day:
            continue
        gap = want - crops.get(crop, 0) - seeds.get(crop, 0)
        qty = min(gap, plantable, 8)
        if qty > 0:
            buys.append(("BUY_SEED", crop, qty, CROPS[crop]["seed"]))
            plantable -= qty

    land = len(farm["unlocked_quadrants"])
    land_cost = (1000, 2000, 4000)[land - 1] if 1 <= land <= 3 else None
    want_land = land_cost is not None and not last and day <= CFG["land_deadline"]

    need = reserve
    for j in range(hires):
        need += _fib(farm["hires_today"] + j) * mult
    for op, item, qty, unit in buys:
        need += qty * (unit if unit is not None else price(item, inv[item] - 1, params))
    if want_land:
        need += land_cost

    # Sell promptly. Metering was measured to be a net loss (higher unit price,
    # lower volume, starved reinvestment), so the floor only refuses the
    # collapsed tail of the curve, and steps aside when cash is needed.
    frac = CFG["sell_floor_frac"]
    if LAST_DAY - day < CFG["liquidate_days"]:
        frac = 0.0
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

    deficit = max(0.0, need - cash)
    sells = []
    for _v, item, stock in queue:
        floor = frac * PARAMS[item][0]
        sold = 0
        gained = 0.0
        level = inv[item]
        while sold < stock:
            unit = price(item, level, params)
            if unit < floor and gained >= deficit:
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
            deficit = max(0.0, deficit - gained)

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
    if want_land and len(out) < MAX_ORDERS and cash >= land_cost + reserve:
        out.append(["BUY_LAND"])
        cash -= land_cost
        want_land = False
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

    crops, animals, free_struct, empties, weeds, unlocked = survey(farm, day)
    shed = dict(obs["private"].get("shed", {}))
    seeds = dict(obs["private"].get("seeds", {}))

    # Shares are stable: recomputed only when the town changes (or first call).
    if state["shares"] is None or state["shops"] != shops:
        state["shares"] = plan_shares(obs, shops, params, day)
        state["shops"] = list(shops)
    crop_shares, animal_shares = state["shares"]

    # Labour first: capacity, then plantings sized to it.
    workload = sum(crops.values()) * 1.2 + sum(animals.values()) * 2.6 + min(len(empties), 12)
    hands_target = min(CFG["hands_max"], max(3, int(math.ceil(workload / 6.5))))
    work_units = hands_target + 1
    animal_cap = min(int(work_units * CFG["animal_per_unit"]), max(0, unlocked - 10))
    crop_cap = min(int(work_units * CFG["crop_per_unit"]), max(0, unlocked - animal_cap))

    crop_target = {c: int(round(s * crop_cap)) for c, s in crop_shares.items()}
    animal_target = {a: int(round(s * animal_cap)) for a, s in animal_shares.items()}

    bags = obs["private"].get("inventories", [])
    tasks = build_tasks(farm, day, crop_target, animal_target, crops, animals,
                        free_struct, empties, weeds, seeds, shed, crop_cap, bags)
    cmds = assign(farm, obs, tasks, seeds, shed, day, state["prev"], work_units)
    market = market_orders(obs, farm, day, hour, crop_target, animal_target, crops,
                           animals, free_struct, empties, seeds, shed, hands_target, config)
    return {"farmer": cmds[0] if cmds else ["PASS"], "hands": cmds[1:], "market": market}
