"""V1: tape production (unchanged) + price-aware metered market head.

Isolates the market fix so its contribution is measurable on its own. Unit
actions, hires, land and production targets all still come from the recorded
MMPQ tape; only selling and the funding of purchases change.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "agent"))

import market_policy as MP  # noqa: E402

_spec = importlib.util.spec_from_file_location("_tape_v1", _ROOT / "baseline" / "tape_agent.py")
_tape = importlib.util.module_from_spec(_spec)
sys.modules["_tape_v1"] = _tape
_spec.loader.exec_module(_tape)

SEED_COST = _tape.SEED_COST
ANIMAL_COST = _tape.ANIMAL_COST
CROPS = _tape.CROPS
ANIMALS = _tape.ANIMALS
MAX_ORDERS = 10


def _market(obs, action, row, config):
    """Fund the tape's purchases, then sell everything that clears its reserve."""
    projected = _tape.project(obs, action)
    farm = obs["farms"][obs["player"]]
    cash = float(farm["money"])
    shed = dict(projected["shed"])
    seeds = dict(projected["seeds"])
    inventory = dict(obs["market"]["inventory"])
    params = obs["market"].get("params")
    day = obs["day"]
    last = day == MP.LAST_DAY

    keep = {"WHEAT": 0 if last else row[7][0], "FERTILIZER": 0 if last else row[7][1]}

    # ---- what the tape wants to buy this turn, and what it costs ----
    buys = []  # (op, item, qty, unit_cost_or_None)
    hires = max(0, int(row[6]) - len(farm["hands"]))
    hire_cost = 0
    mult = int(config.get("farmHandCostMult", 1))
    for j in range(hires):
        hire_cost += _tape._fib(farm["hires_today"] + j) * mult

    for ci, crop in enumerate(CROPS):
        gap = max(0, int(row[3][ci]) - seeds.get(crop, 0))
        if gap:
            buys.append(("BUY_SEED", crop, gap, SEED_COST[crop]))
    if not last:
        for item, idx in (("WHEAT", 0), ("FERTILIZER", 1)):
            gap = max(0, row[7][idx] - shed.get(item, 0))
            if gap:
                buys.append(("BUY_PRODUCT", item, gap, None))
    for ai, animal in enumerate(ANIMALS):
        held = projected["animals"][animal] + projected["carried"][animal] + shed.get(animal, 0)
        gap = max(0, int(row[4][ai]) - held)
        if gap:
            buys.append(("BUY_ANIMAL", animal, gap, ANIMAL_COST[animal]))
    land = len(farm["unlocked_quadrants"])
    land_buys = max(0, int(row[5]) - land)

    need = float(hire_cost)
    for op, item, qty, unit in buys:
        need += qty * (unit if unit is not None else MP.price(item, inventory[item] - 1, params))
    for k in range(land_buys):
        idx = land + k
        need += (1000, 2000, 4000)[idx - 1] if 1 <= idx <= 3 else 10 ** 12

    # ---- selling: metered by reserve price, forced only as far as funding needs ----
    shed_total = sum(shed.values())
    candidates = []
    for item in MP.PRODUCTS:
        stock = max(0, shed.get(item, 0) - keep.get(item, 0))
        if stock:
            candidates.append((stock * MP.price(item, inventory[item], params), item, stock))
    candidates.sort(reverse=True)

    deficit = max(0.0, need - cash)
    sells = []
    for _value, item, stock in candidates:
        qty, revenue = MP.metered_sale(
            item, stock, inventory[item], day, shed_total, params,
            cash_target=deficit if deficit > 0 else 0.0,
        )
        if qty <= 0:
            continue
        sells.append(["SELL", item, qty])
        cash += revenue
        shed[item] -= qty
        shed_total -= qty
        inventory[item] += qty
        deficit = max(0.0, deficit - revenue)

    out = list(sells[:MAX_ORDERS])

    def room():
        return len(out) < MAX_ORDERS

    for _ in range(hires):
        cost = _tape._fib(farm["hires_today"]) * mult
        if not room() or cash < cost:
            break
        out.append(["HIRE"])
        cash -= cost

    for op, item, qty, unit in buys:
        if not room() or qty <= 0:
            continue
        capacity = max(0, MP.SHED_CAP - sum(shed.values())) if op in ("BUY_ANIMAL", "BUY_PRODUCT") else qty
        count = 0
        for _ in range(min(qty, capacity)):
            unit_cost = unit if unit is not None else MP.price(item, inventory[item] - 1, params)
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

    for k in range(land_buys):
        idx = land + k
        cost = (1000, 2000, 4000)[idx - 1] if 1 <= idx <= 3 else 10 ** 12
        if not room() or cash < cost:
            break
        out.append(["BUY_LAND"])
        cash -= cost

    return out


_tape._market = _market
agent = _tape.agent
