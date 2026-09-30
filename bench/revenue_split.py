"""Where does each side's money come from, product by product?

Margin, not score, decides the game. Selling floods a product's market
inventory and drives its price down for BOTH players, so a sale is partly an
attack. To know which products are worth attacking we need each side's revenue
broken down, not just the totals.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sim"))
sys.path.insert(0, str(ROOT / "bench"))
import kaggriculture as K  # noqa: E402
import debt_benchmark as DB  # noqa: E402

BASE = {"WHEAT": 25, "CARROT": 35, "TOMATO": 60, "STRAWBERRY": 120, "MELON": 250,
        "EGG": 50, "MILK": 160, "WOOL": 200, "FERTILIZER": 100}

REV = [collections.Counter(), collections.Counter()]
UNITS = [collections.Counter(), collections.Counter()]
SPEND = [collections.Counter(), collections.Counter()]
_FARM_INDEX = {}

_wrapped = DB._commit_unit


def _commit(op, item, price, farm, private, market, shed_capacity=100):
    ok = _wrapped(op, item, price, farm, private, market, shed_capacity)
    if ok:
        seat = _FARM_INDEX.get(id(farm))
        if seat is not None:
            if op == "SELL":
                REV[seat][item] += price
                UNITS[seat][item] += 1
            elif op in ("BUY_PRODUCT", "BUY_SEED", "BUY_ANIMAL"):
                SPEND[seat][item] += price
    return ok


K._commit_unit = _commit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", type=Path, default=ROOT / "submission" / "main.py")
    ap.add_argument("--split", default="development")
    ap.add_argument("--games", type=int, default=40)
    args = ap.parse_args()

    prior = json.loads((ROOT / "reports" / "prior_per_game_results.json").read_text())
    wanted = {str(g["game_id"]) for g in prior["games"] if g["split"] == args.split}
    paths = [p for p in sorted((ROOT / "cache").glob("*.json.gz"))
             if p.name.split(".")[0] in wanted][: args.games]
    agent = DB.load_agent(args.agent, "cand_rev")

    n = 0
    for path in paths:
        record = DB.load_record(path)
        if record.get("seed") is None:
            continue
        for cand_seat in (0, 1):
            engine_seat = {"cand": cand_seat, "opp": 1 - cand_seat}
            _FARM_INDEX.clear()
            # play() builds the engine internally, so hook the farms by identity
            # on the first commit: simplest is to re-derive them here.
            orig_play = DB.play

            def play_hooked(rec, **kw):
                import runner
                eng = runner.Engine(int(rec["seed"]), DB._config(rec))
                farms = eng.state[0].observation.farms
                _FARM_INDEX[id(farms[engine_seat["cand"]])] = 0
                _FARM_INDEX[id(farms[engine_seat["opp"]])] = 1
                DB._CREDIT.clear()
                DB._CREDIT.add(id(farms[engine_seat["opp"]]))
                opp = engine_seat["opp"]
                for i, pair in enumerate(rec["actions"]):
                    if eng.done:
                        break
                    submit = [None, None]
                    submit[engine_seat["cand"]] = agent(eng.observation(engine_seat["cand"]),
                                                        dict(eng.configuration))
                    submit[opp] = pair[opp] or {}
                    eng.step(submit)
                    shops = rec["shops"]
                    if i < len(shops) and shops[i] is not None:
                        eng.state[0].observation.town["unlocked_shops"] = list(shops[i])
                return eng

            play_hooked(record)
            n += 1
    print(f"{n} matches, {args.split} split, agent {args.agent.name}\n")
    print(f"{'product':11s} {'OUR rev':>11s} {'OUR u':>7s} {'OUR $/u':>8s}"
          f" {'OPP rev':>11s} {'OPP u':>7s} {'OPP $/u':>8s} {'MARGIN':>11s}")
    total = [0, 0]
    rows = []
    for item in BASE:
        our, opp = REV[0][item], REV[1][item]
        total[0] += our
        total[1] += opp
        rows.append((our - opp, item, our, opp))
    for _m, item, our, opp in sorted(rows):
        ou, pu = UNITS[0][item], UNITS[1][item]
        print(f"{item:11s} {our/n:11,.0f} {ou/n:7.1f} {our/max(1,ou):8.1f}"
              f" {opp/n:11,.0f} {pu/n:7.1f} {opp/max(1,pu):8.1f} {(our-opp)/n:11,.0f}")
    print(f"{'TOTAL':11s} {total[0]/n:11,.0f} {'':7s} {'':8s} {total[1]/n:11,.0f}"
          f" {'':7s} {'':8s} {(total[0]-total[1])/n:11,.0f}")
    print(f"\nour spend/match  {sum(SPEND[0].values())/n:,.0f}"
          f" | opponent spend/match {sum(SPEND[1].values())/n:,.0f}")


if __name__ == "__main__":
    raise SystemExit(main())
