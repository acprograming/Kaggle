"""V2: closed-loop Kaggriculture agent (score optimiser, standard library only).

Design notes, all grounded in measurements on the official interpreter:

* Labour, not land, is the binding constraint. Tile utilisation of the tape
  agent was already 85-88%, but 43% of its unit actions were movement. Hiring
  is Fibonacci-priced, so ~10-11 hands per day is the affordable ceiling
  (143/day at 10, 986/day at 14). Value is therefore ranked per *action*, not
  per tile: melon ~$118/action, sheep ~$94, cow ~$78, wheat/tomato ~$17.
* Prices are convex and inventory-driven. Products the town drains stay scarce
  and sell *above* base, so the target mix is conditioned on unlocked_shops.
* Metering sales alone was measured to be a net loss: it raised unit prices but
  cut volume and starved reinvestment. So we sell promptly and fix the mix
  instead, keeping only a floor that avoids selling into the $1 collapse.
* Fertilizer is used, not sold. Collected fertilizer doubles the watering
  bonus on one-time crops.
"""
from __future__ import annotations

import math

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
ANIMAL_OF_PRODUCT = {v["product"]: k for k, v in ANIMALS.items()}

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

# Tunables (see reports/tuning.json for the sweep that selected these).
CFG = {
    "hands": 10,
    "dist_penalty": 2.0,
    "sell_floor_frac": 0.30,
    "liquidate_days": 2,
    "wheat_buffer_days": 2.0,
    "land_deadline": 20,
    "tile_ambition": 100,
    "seed_buffer": 3,
    "action_efficiency": 0.55,
    "fert_min_hold": 6,
    "care_value": 30.0,
    "plan_horizon": 20,
}

_MOVE = {"NORTH": (0, -1), "SOUTH": (0, 1), "WEST": (-1, 0), "EAST": (1, 0)}
_STATE = {}


# --------------------------------------------------------------------------- market


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
    """Units the town removes from the market each day, per product."""
    out = {p: 1.0 for p in PRODUCTS}
    out["FERTILIZER"] = 0.0  # the town centre skips fertilizer
    for shop in shops:
        products = SHOPS.get(shop, ())
        rate = 12.0 if len(products) == 1 else 6.0
        for p in products:
            out[p] = out.get(p, 0.0) + rate
    return out


# --------------------------------------------------------------------------- planner

# Units produced and tile-days occupied per planting, unfertilised.
CROP_YIELD = {"WHEAT": (4.0, 5.0), "CARROT": (3.0, 4.0), "MELON": (6.0, 11.0),
              "TOMATO": (4.0, 13.0), "STRAWBERRY": (4.0, 18.0)}
# Actions consumed per tile-day (water/harvest amortised).
CROP_ACTIONS = {"WHEAT": 1.25, "CARROT": 1.30, "MELON": 1.10, "TOMATO": 1.08, "STRAWBERRY": 1.06}
# Animals, assuming CARE every day: units per day, actions per day.
ANIMAL_RATE = {"GOOSE": (2.0, 3.0), "COW": (1.5, 2.6), "SHEEP": (1.33, 2.4)}


def plan_targets(obs, shops, params, tile_budget):
    """Marginal-value allocation across production options.

    Greedy: repeatedly add the option with the highest net value per action,
    charging each added unit against the price curve it will itself depress.
    Bounded by BOTH the labour budget and the tiles actually available, since
    targets beyond the tile count only cause seed hoarding.
    """
    day = obs["day"]
    horizon = min(CFG["plan_horizon"], max(1, LAST_DAY - day))
    drain = drain_per_day(shops)
    inv = dict(obs["market"]["inventory"])
    wheat_price = price("WHEAT", inv.get("WHEAT", I0), params)

    # Projected inventory each product would sit at, net of town drain.
    proj = {p: max(0.0, inv.get(p, I0) - drain.get(p, 0.0) * horizon) for p in PRODUCTS}
    added = {p: 0.0 for p in PRODUCTS}

    options = []
    for crop, cd in CROPS.items():
        # Only plant what can still deliver before the season ends.
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

    budget = CFG["hands"] * TURNS_PER_DAY * CFG["action_efficiency"]
    targets = {k: 0 for k in list(CROPS) + list(ANIMALS)}
    used = 0.0
    placed = 0
    while used < budget and placed < tile_budget:
        best = None
        for kind, name, product, per_day, acts, upkeep, feed in options:
            if per_day <= 0:
                continue
            level = proj[product] + added[product]
            unit_price = price(product, int(level), params)
            value = per_day * unit_price - upkeep - feed
            per_action = value / acts
            if best is None or per_action > best[0]:
                best = (per_action, kind, name, product, per_day, acts)
        if best is None or best[0] <= 0:
            break
        _pa, kind, name, product, per_day, acts = best
        targets[name] += 1
        added[product] += per_day * horizon
        used += acts
        placed += 1
    return targets


# --------------------------------------------------------------------------- helpers


def _shed_tiles():
    h = BOARD // 2
    return [(h - 1, h - 1), (h, h - 1), (h - 1, h), (h, h)]


def _is_shed_adj(pos):
    return (pos[0], pos[1]) in [(x, y) for x, y in _shed_tiles()]


def _unlocked(farm, x, y):
    return farm["tiles"][y][x] != "LOCKED"


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


def _crop_ready(tile, day):
    """One-time crops are harvested at peak; ongoing ones whenever stocked."""
    cd = CROPS[tile["crop"]]
    age = day - tile["planted_day"]
    if age < cd["first"]:
        return False
    if tile.get("yield_units", 0) <= 0:
        return False
    if cd["ongoing"]:
        return True
    return age >= cd["maxday"]


def _in_water_window(tile, day):
    cd = CROPS[tile["crop"]]
    if cd["ongoing"]:
        return True
    age = day - tile["planted_day"]
    return ((cd["maxday"] + 1) // 2) <= age <= cd["maxday"]


# --------------------------------------------------------------------------- tasks


def build_tasks(obs, farm, targets, seeds, shed, day):
    """Every worthwhile tile action this turn, as (value, pos, kind, arg)."""
    tasks = []
    add = tasks.append
    tiles = farm["tiles"]
    have_crop = {c: 0 for c in CROPS}
    animals_placed = {a: 0 for a in ANIMALS}
    free_struct = {"COOP": [], "PASTURE": []}
    empties = []

    for y in range(BOARD):
        for x in range(BOARD):
            tile = tiles[y][x]
            if tile == "LOCKED":
                continue
            pos = (x, y)
            if tile is None:
                empties.append(pos)
                continue
            kind = tile.get("kind")
            if kind == "WEED":
                add((20.0, pos, "DIG", None))
                continue
            if kind == "PLANT":
                have_crop[tile["crop"]] = have_crop.get(tile["crop"], 0) + 1
                if not tile.get("watered_today"):
                    urgent = tile.get("consecutive_unwatered", 0) >= 1
                    if _in_water_window(tile, day):
                        add((140.0 if urgent else 46.0, pos, "WATER", None))
                    elif urgent:
                        # Watering outside the yield window still prevents a weed.
                        add((140.0, pos, "WATER", None))
                if _crop_ready(tile, day):
                    add((55.0 + 2.0 * tile.get("yield_units", 0), pos, "HARVEST", None))
                # Fertilize one-time crops just before their bonus window.
                cd = CROPS[tile["crop"]]
                if (not cd["ongoing"] and tile.get("fertilized_until_day", -1) < day
                        and shed.get("FERTILIZER", 0) > CFG["fert_min_hold"]
                        and _in_water_window(tile, day) and tile["crop"] in ("WHEAT", "CARROT", "MELON")):
                    add((22.0, pos, "FERTILIZE", None))
                continue
            if "animal" in tile and tile.get("animal"):
                animal = tile["animal"]
                animals_placed[animal] = animals_placed.get(animal, 0) + 1
                ad = ANIMALS[animal]
                if not tile.get("fed_today"):
                    urgent = tile.get("consecutive_unfed", 0) >= 1
                    add((150.0 if urgent else 52.0, pos, "FEED", None))
                held = tile.get("yield_units", 0)
                if held > 0:
                    full = held >= ad["maxheld"]
                    add((62.0 + 3.0 * held if full else 40.0 + 2.0 * held, pos, "HARVEST", None))
                if not tile.get("cared_today"):
                    add((CFG["care_value"], pos, "CARE", None))
                if tile.get("fertilizer_available"):
                    add((16.0, pos, "COLLECT_FERTILIZER", None))
            else:
                free_struct[tile["kind"]].append(pos)

    # Place animals waiting in the shed or in a unit's hands onto free structures.
    for structure, positions in free_struct.items():
        for pos in positions:
            add((80.0, pos, "PLACE_STRUCT", structure))

    # Plant toward the target mix, most under-target crop first.
    wants = []
    for crop, want in targets.items():
        if crop in ANIMALS:
            continue
        cd = CROPS[crop]
        if cd["first"] > LAST_DAY - day:
            continue
        gap = want - have_crop.get(crop, 0)
        if gap > 0:
            wants.append((gap, crop))
    wants.sort(reverse=True)
    for pos in empties:
        if not wants:
            break
        add((38.0, pos, "PLANT_ANY", tuple(c for _g, c in wants)))
        # Build structures on spare land when animals are under target.
    need_struct = {}
    for animal, ad in ANIMALS.items():
        gap = targets.get(animal, 0) - animals_placed.get(animal, 0)
        if gap > 0 and ad["first"] <= LAST_DAY - day:
            need_struct[ad["structure"]] = need_struct.get(ad["structure"], 0) + gap
    for structure in list(need_struct):
        need_struct[structure] -= len(free_struct.get(structure, ()))
    for pos in empties[: sum(max(0, v) for v in need_struct.values())]:
        for structure, gap in need_struct.items():
            if gap > 0:
                add((30.0, pos, "BUILD", structure))
                need_struct[structure] -= 1
                break
    return tasks, have_crop, animals_placed, free_struct


def assign(obs, farm, tasks, seeds, shed, day, prev):
    """Greedy nearest-value assignment of units to tasks."""
    positions = [tuple(farm["farmer"])] + [tuple(p) for p in farm["hands"]]
    bags = obs["private"].get("inventories", [])
    n = len(positions)
    cmds = [None] * n
    seed_pool = dict(seeds)
    shed_pool = dict(shed)
    penalty = CFG["dist_penalty"]

    # Units needing wheat to feed, or fertilizer, resupply at the shed.
    feed_tasks = sum(1 for t in tasks if t[2] == "FEED")
    shed_targets = _shed_tiles()

    open_tasks = list(tasks)
    free_units = list(range(n))
    while free_units and open_tasks:
        best = None
        for ui in free_units:
            bag = bags[ui] if ui < len(bags) else {}
            pos = positions[ui]
            for ti, (value, tpos, kind, arg) in enumerate(open_tasks):
                if kind == "FEED" and bag.get("WHEAT", 0) <= 0:
                    continue
                if kind == "FERTILIZE" and bag.get("FERTILIZER", 0) <= 0:
                    continue
                if kind == "PLANT_ANY" and not any(seed_pool.get(c, 0) > 0 for c in arg):
                    continue
                if kind == "PLACE_STRUCT":
                    wanted = [a for a, ad in ANIMALS.items() if ad["structure"] == arg]
                    if not any(bag.get(a, 0) > 0 for a in wanted):
                        continue
                score = value - penalty * _dist(pos, tpos)
                if prev.get(ui) == (tpos, kind):
                    score += 6.0  # stickiness: do not abandon a trip half way
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
            wanted = [a for a, ad in ANIMALS.items() if ad["structure"] == arg]
            animal = next((a for a in wanted if bag.get(a, 0) > 0), None)
            cmds[ui] = ["PLACE", animal, 1] if animal else ["PASS"]
        elif kind == "BUILD":
            cmds[ui] = ["BUILD_PASTURE"] if arg == "PASTURE" else ["BUILD_COOP"]
        else:
            cmds[ui] = [kind]
        free_units.remove(ui)

    # Idle units resupply: carry wheat for feeding, fertilizer, and waiting animals.
    for ui in list(free_units):
        pos = positions[ui]
        bag = bags[ui] if ui < len(bags) else {}
        target = min(shed_targets, key=lambda t: _dist(pos, t))
        if not _unlocked(farm, *target):
            target = min((t for t in shed_targets if _unlocked(farm, *t)),
                         key=lambda t: _dist(pos, t), default=target)
        if pos != tuple(target):
            cmds[ui] = _step_toward(pos, target)
            continue
        pending_animal = next((a for a in ANIMALS if shed_pool.get(a, 0) > 0), None)
        want_wheat = feed_tasks > 0 and bag.get("WHEAT", 0) < 4
        if pending_animal:
            shed_pool[pending_animal] -= 1
            cmds[ui] = ["PICKUP", pending_animal, 1]
        elif want_wheat and shed_pool.get("WHEAT", 0) > 0:
            take = min(6, shed_pool["WHEAT"])
            shed_pool["WHEAT"] -= take
            cmds[ui] = ["PICKUP", "WHEAT", take]
        elif shed_pool.get("FERTILIZER", 0) > CFG["fert_min_hold"] and bag.get("FERTILIZER", 0) < 3:
            take = min(3, shed_pool["FERTILIZER"])
            shed_pool["FERTILIZER"] -= take
            cmds[ui] = ["PICKUP", "FERTILIZER", take]
        elif sum(bag.values()) > 0:
            cmds[ui] = ["DROP"]
        else:
            cmds[ui] = ["PASS"]

    return [c if c else ["PASS"] for c in cmds]


# --------------------------------------------------------------------------- market head


def market_orders(obs, farm, targets, have_crop, animals_placed, free_struct, empty_tiles, config):
    day, hour = obs["day"], obs["hour"]
    params = obs["market"].get("params")
    inv = dict(obs["market"]["inventory"])
    shed = dict(obs["private"].get("shed", {}))
    seeds = dict(obs["private"].get("seeds", {}))
    cash = float(farm["money"])
    out = []
    last = day >= LAST_DAY

    animal_count = sum(animals_placed.values())
    wheat_reserve = 0 if last else int(math.ceil(animal_count * CFG["wheat_buffer_days"]))
    fert_reserve = 0 if last else CFG["fert_min_hold"]

    # ---- hires first: the day's whole labour budget depends on them ----
    hires_needed = 0
    if hour <= 1 and not last:
        hires_needed = max(0, CFG["hands"] - len(farm["hands"]))

    # ---- what we want to buy ----
    buys = []
    # Wheat first: an unfed animal escapes and is unrecoverable.
    if not last:
        short = wheat_reserve - shed.get("WHEAT", 0)
        if short > 0:
            buys.append(("BUY_PRODUCT", "WHEAT", min(short, 25), None))
    # Animals only when somewhere to put them: the pipeline is
    # BUILD -> BUY -> PICKUP -> PLACE, and an animal idle in the shed is
    # dead capital (measured at ~13 per game on the tape agent).
    for animal, ad in ANIMALS.items():
        if ad["first"] > LAST_DAY - day:
            continue
        held = animals_placed.get(animal, 0) + shed.get(animal, 0)
        gap = targets.get(animal, 0) - held
        vacancies = len(free_struct.get(ad["structure"], ()))
        waiting = sum(shed.get(a, 0) for a in ANIMALS if ANIMALS[a]["structure"] == ad["structure"])
        room = max(0, vacancies - waiting)
        if gap > 0 and room > 0:
            buys.append(("BUY_ANIMAL", animal, min(gap, room, 2), ad["cost"]))
    # Seeds last, and only for tiles that are free now: buying toward the full
    # target while tiles are occupied hoards seeds and starves everything else.
    plantable = max(0, empty_tiles) + CFG["seed_buffer"]
    for crop, want in targets.items():
        if crop in ANIMALS or plantable <= 0:
            continue
        cd = CROPS[crop]
        if cd["first"] > LAST_DAY - day:
            continue
        gap = want - have_crop.get(crop, 0) - seeds.get(crop, 0)
        qty = min(gap, plantable, 8)
        if qty > 0:
            buys.append(("BUY_SEED", crop, qty, cd["seed"]))
            plantable -= qty

    land = len(farm["unlocked_quadrants"])
    land_cost = (1000, 2000, 4000)[land - 1] if 1 <= land <= 3 else None
    land_buys = 0
    if land_cost is not None and not last and day <= CFG["land_deadline"]:
        land_buys = 1

    need = 0.0
    for j in range(hires_needed):
        need += _fib(farm["hires_today"] + j) * int(config.get("farmHandCostMult", 1))
    for op, item, qty, unit in buys:
        need += qty * (unit if unit is not None else price(item, inv[item] - 1, params))
    if land_buys:
        need += land_cost

    # ---- sell: prompt, with a floor that only avoids the $1 collapse ----
    frac = CFG["sell_floor_frac"]
    days_left = LAST_DAY - day
    if days_left < CFG["liquidate_days"]:
        frac = 0.0
    sellable = []
    for item in PRODUCTS:
        reserve = wheat_reserve if item == "WHEAT" else (fert_reserve if item == "FERTILIZER" else 0)
        stock = shed.get(item, 0) - reserve
        if stock > 0:
            sellable.append((stock * price(item, inv[item], params), item, stock))
    sellable.sort(reverse=True)

    sells = []
    deficit = max(0.0, need - cash)
    for _v, item, stock in sellable:
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

    # ---- emit, respecting the per-turn order cap ----
    mult = int(config.get("farmHandCostMult", 1))
    for j in range(hires_needed):
        cost = _fib(farm["hires_today"] + j) * mult
        if len(out) >= MAX_ORDERS or cash < cost:
            break
        out.append(["HIRE"])
        cash -= cost
    for order in sells:
        if len(out) >= MAX_ORDERS:
            break
        out.append(order)
    if land_buys and len(out) < MAX_ORDERS and cash >= land_cost:
        out.append(["BUY_LAND"])
        cash -= land_cost
        land_buys = 0
    for op, item, qty, unit in buys:
        if len(out) >= MAX_ORDERS:
            break
        room = max(0, SHED_CAP - sum(v for v in shed.values() if v > 0)) if op != "BUY_SEED" else qty
        count = 0
        for _ in range(min(qty, room)):
            unit_cost = unit if unit is not None else price(item, inv[item] - 1, params)
            if unit_cost > cash:
                break
            count += 1
            cash -= unit_cost
            if op == "BUY_PRODUCT":
                inv[item] -= 1
            if op != "BUY_SEED":
                shed[item] = shed.get(item, 0) + 1
        if count:
            out.append([op, item, count])
    if land_buys and len(out) < MAX_ORDERS and cash >= land_cost:
        out.append(["BUY_LAND"])
    return out[:MAX_ORDERS]


def _fib(n):
    a, b = 1, 1
    for _ in range(max(0, n)):
        a, b = b, a + b
    return a


# --------------------------------------------------------------------------- entry


def agent(obs, configuration=None):
    config = configuration or {}
    seat = int(obs.get("player", 0))
    farm = obs["farms"][seat]
    day = obs["day"]
    step = int(obs.get("step", TURNS_PER_DAY * day + obs["hour"]))
    params = obs["market"].get("params")
    shops = list(obs.get("town", {}).get("unlocked_shops", []))

    state = _STATE.get(seat)
    if state is None or step == 0 or step <= state.get("step", -1):
        state = {"step": -1, "day": -1, "targets": None, "shops": None, "prev": {}}
        _STATE[seat] = state
    state["step"] = step

    # Replan when the day turns or the town changes: both move the price curves.
    unlocked = sum(1 for row in farm["tiles"] for t in row if t != "LOCKED")
    # Plan for the land we intend to own, not just what is unlocked today.
    tile_budget = unlocked if day > CFG["land_deadline"] else max(unlocked, CFG["tile_ambition"])
    if state["targets"] is None or state["day"] != day or state["shops"] != shops:
        state["targets"] = plan_targets(obs, shops, params, tile_budget)
        state["day"] = day
        state["shops"] = list(shops)
        state["prev"] = {}
    targets = state["targets"]

    shed = dict(obs["private"].get("shed", {}))
    seeds = dict(obs["private"].get("seeds", {}))
    tasks, have_crop, animals_placed, free_struct = build_tasks(
        obs, farm, targets, seeds, shed, day)
    cmds = assign(obs, farm, tasks, seeds, shed, day, state["prev"])
    empty_tiles = sum(1 for row in farm["tiles"] for t in row if t is None)
    market = market_orders(obs, farm, targets, have_crop, animals_placed,
                           free_struct, empty_tiles, config)
    return {"farmer": cmds[0] if cmds else ["PASS"], "hands": cmds[1:], "market": market}
