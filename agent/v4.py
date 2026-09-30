"""V4 = V3 idle work (in place only) + capital discipline + a sell floor.

Additions over V3, each isolated and measured:

1. Animal purchase discipline. The tape buys animals it never places: ~13 per
   game sit in the shed to the final turn, about 5k of dead capital. Buying is
   suppressed while an animal of that structure type is already waiting, since
   the bottleneck is placement, not supply.
2. A mild sell floor. Full metering was measured to be a net loss (it raised
   unit prices but cut volume and starved reinvestment), so this only refuses
   the genuinely collapsed end of the curve and still liquidates at the close.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("_tape_v4", _ROOT / "baseline" / "tape_agent.py")
_tape = importlib.util.module_from_spec(_spec)
sys.modules["_tape_v4"] = _tape
_spec.loader.exec_module(_tape)

_vspec = importlib.util.spec_from_file_location("_v3lib", _ROOT / "agent" / "v3.py")
_v3 = importlib.util.module_from_spec(_vspec)
sys.modules["_v3lib"] = _v3
_vspec.loader.exec_module(_v3)

_idle_task = _v3._idle_task
_step_toward = _v3._step_toward
_MOVE_NAMES = ("NORTH", "SOUTH", "EAST", "WEST")

RADIUS = int(os.environ.get("V4_RADIUS", "0"))
SELL_FLOOR = float(os.environ.get("V4_SELL_FLOOR", "0.30"))
LIQUIDATE_DAYS = int(os.environ.get("V4_LIQ_DAYS", "2"))
ANIMAL_DISCIPLINE = os.environ.get("V4_ANIMAL_DISCIPLINE", "1") == "1"
SHED_PRESSURE = 88
LAST_DAY = 29
BASE = {"WHEAT": 25, "CARROT": 35, "TOMATO": 60, "STRAWBERRY": 120, "MELON": 250,
        "EGG": 50, "MILK": 160, "WOOL": 200, "FERTILIZER": 100}
STRUCT_OF = {"GOOSE": "COOP", "COW": "PASTURE", "SHEEP": "PASTURE"}

CROPS = _tape.CROPS
ANIMALS = _tape.ANIMALS
PRODUCTS = _tape.PRODUCTS
SEED_COST = _tape.SEED_COST
ANIMAL_COST = _tape.ANIMAL_COST
price = _tape.price


def _free_structures(farm):
    free = {"COOP": 0, "PASTURE": 0}
    for row in farm["tiles"]:
        for tile in row:
            if isinstance(tile, dict) and tile.get("kind") in free and not tile.get("animal"):
                free[tile["kind"]] += 1
    return free


def _market(obs, action, row, config):
    projected = _tape.project(obs, action)
    farm = obs["farms"][obs["player"]]
    cash = float(farm["money"])
    shed = dict(projected["shed"])
    seeds = dict(projected["seeds"])
    inventory = dict(obs["market"]["inventory"])
    params = obs["market"].get("params")
    day = obs["day"]
    last = day == LAST_DAY
    out = []
    keep = {"WHEAT": 0 if last else row[7][0], "FERTILIZER": 0 if last else row[7][1]}

    shed_total = sum(v for v in shed.values() if v > 0)
    days_left = LAST_DAY - day
    floor_frac = SELL_FLOOR
    if days_left < LIQUIDATE_DAYS:
        floor_frac = 0.0
    elif shed_total > SHED_PRESSURE:
        floor_frac *= max(0.0, (100 - shed_total) / float(100 - SHED_PRESSURE))

    sales = []
    for item in PRODUCTS:
        n = max(0, shed.get(item, 0) - keep.get(item, 0))
        if n:
            sales.append((n * obs["market"]["prices"].get(item, 0), item, n))
    sales.sort(reverse=True)

    def sell_next():
        """Sell one queued product, stopping at its floor price."""
        nonlocal cash
        while sales and len(out) < 10:
            _value, item, n = sales.pop(0)
            floor = floor_frac * BASE.get(item, 0)
            sold = 0
            gained = 0.0
            level = inventory[item]
            while sold < n:
                unit = price(item, level, params)
                if unit < floor:
                    break
                sold += 1
                gained += unit
                if unit > 1:
                    level += 1
            if sold <= 0:
                continue
            out.append(["SELL", item, sold])
            shed[item] -= sold
            inventory[item] = level
            cash += gained
            return True
        return False

    def prefund(amount):
        while cash < amount and sell_next():
            pass

    def buy(op, item, quantity, cost):
        nonlocal cash
        if quantity <= 0 or len(out) >= 10:
            return 0
        prefund(quantity * (cost if cost is not None else price(item, inventory[item] - quantity, params)))
        if len(out) >= 10:
            return 0
        capacity = max(0, 100 - sum(shed.values())) if op in ("BUY_ANIMAL", "BUY_PRODUCT") else quantity
        count = 0
        for _ in range(min(quantity, capacity)):
            unit_cost = cost if cost is not None else price(item, inventory[item] - 1, params)
            if unit_cost > cash:
                break
            count += 1
            cash -= unit_cost
            if op == "BUY_PRODUCT":
                inventory[item] -= 1
            if op in ("BUY_PRODUCT", "BUY_ANIMAL"):
                shed[item] = shed.get(item, 0) + 1
        if count:
            out.append([op, item, count])
        return count

    wanted = max(0, int(row[6]) - len(farm["hands"]))
    for j in range(wanted):
        cost = _tape._fib(farm["hires_today"] + j) * int(config.get("farmHandCostMult", 1))
        prefund(cost)
        if len(out) >= 10 or cash < cost:
            break
        out.append(["HIRE"])
        cash -= cost

    for ci, crop in enumerate(CROPS):
        gap = max(0, int(row[3][ci]) - seeds.get(crop, 0))
        if gap:
            buy("BUY_SEED", crop, gap, SEED_COST[crop])
    for item, idx in (("WHEAT", 0), ("FERTILIZER", 1)):
        if not last:
            buy("BUY_PRODUCT", item, max(0, row[7][idx] - shed.get(item, 0)), None)

    free = _free_structures(farm)
    for ai, animal in enumerate(ANIMALS):
        count = projected["animals"][animal] + projected["carried"][animal] + shed.get(animal, 0)
        gap = max(0, int(row[4][ai]) - count)
        if gap and ANIMAL_DISCIPLINE:
            structure = STRUCT_OF[animal]
            waiting = sum(shed.get(a, 0) + projected["carried"].get(a, 0)
                          for a, s in STRUCT_OF.items() if s == structure)
            gap = min(gap, max(0, free.get(structure, 0) - waiting))
        buy("BUY_ANIMAL", animal, gap, ANIMAL_COST[animal])

    land = len(farm["unlocked_quadrants"])
    for _ in range(max(0, row[5] - land)):
        cost = (1000, 2000, 4000)[land - 1] if 1 <= land <= 3 else 10 ** 12
        prefund(cost)
        if cash < cost or len(out) >= 10:
            break
        out.append(["BUY_LAND"])
        cash -= cost
        land += 1

    while sell_next():
        pass
    return out


_tape._market = _market


def agent(obs, configuration=None):
    action = _tape.agent(obs, configuration)
    seat = int(obs.get("player", 0))
    farm = obs["farms"][seat]
    day = obs["day"]
    bags = obs["private"].get("inventories", [])
    positions = [tuple(farm["farmer"])] + [tuple(p) for p in farm["hands"]]
    units = [action.get("farmer") or ["PASS"]] + list(action.get("hands") or [])

    claimed = {positions[i] for i, cmd in enumerate(units)
               if i < len(positions) and cmd and cmd[0] not in ("PASS",) + _MOVE_NAMES}

    saved = _v3.RADIUS
    _v3.RADIUS = RADIUS
    try:
        for i, cmd in enumerate(units):
            if (cmd and cmd[0] != "PASS") or i >= len(positions):
                continue
            found = _idle_task(farm, positions[i], bags[i] if i < len(bags) else {}, day, claimed)
            if not found:
                continue
            _score, target, command = found
            units[i] = command if target == positions[i] else _step_toward(positions[i], target)
            claimed.add(target)
    finally:
        _v3.RADIUS = saved

    action["farmer"] = units[0] if units else ["PASS"]
    action["hands"] = units[1:]
    return action
