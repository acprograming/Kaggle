"""Stale-opponent benchmark, reproducing the prior session's debt protocol.

Protocol (as specified for the earlier run): the recorded opponent's commands
execute even when they push its cash negative, the debt counts against its
final score, opponent commands that fail for NON-cash reasons become no-ops,
and every game is retained. Only the opponent is extended credit; the candidate
must still afford everything it buys.

The recorded store schedule is forced after each transition. Store reveals and
weed spawns draw from the same RNG, so a candidate that leaves a different
number of empty tiles shifts the draw sequence and would otherwise face a
different town than the opponent was recorded against.

`--verify` replays both recorded seats instead, which must reproduce the
original final scores exactly; that is the fidelity check for everything else.
"""
from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import statistics as st
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sim"))
import kaggriculture as K  # noqa: E402
from runner import Engine  # noqa: E402

_CREDIT: set[int] = set()
_STATS = {"denied_noncash": 0}

_orig_commit = K._commit_unit
_orig_hire = K._do_hire
_orig_land = K._do_buy_land


def _commit_unit(op, item, price, farm, private, market, shed_capacity=100):
    """As the engine's, but the credited farm's cash check is skipped."""
    if id(farm) not in _CREDIT:
        return _orig_commit(op, item, price, farm, private, market, shed_capacity)
    if op == "SELL":
        if private["shed"].get(item, 0) <= 0:
            _STATS["denied_noncash"] += 1
            return False
        private["shed"][item] -= 1
        farm["money"] += price
        if price > 1:
            market["inventory"][item] += 1
        return True
    if op == "BUY_PRODUCT":
        if sum(private["shed"].values()) >= shed_capacity:
            _STATS["denied_noncash"] += 1
            return False
        farm["money"] -= price          # credit: may go negative
        private["shed"][item] = private["shed"].get(item, 0) + 1
        market["inventory"][item] -= 1
        return True
    if op == "BUY_SEED":
        farm["money"] -= price
        private["seeds"][item] = private["seeds"].get(item, 0) + 1
        return True
    if op == "BUY_ANIMAL":
        if sum(private["shed"].values()) >= shed_capacity:
            _STATS["denied_noncash"] += 1
            return False
        farm["money"] -= price
        private["shed"][item] = private["shed"].get(item, 0) + 1
        return True
    return False


def _do_hire(farm, private, board_size, mult=K.FARM_HAND_COST_MULT):
    if id(farm) not in _CREDIT:
        return _orig_hire(farm, private, board_size, mult)
    farm["money"] -= K._hire_cost(farm["hires_today"], mult)
    farm["hires_today"] += 1
    farm["hands"].append(K._spawn_hand(farm, board_size))
    private["inventories"].append({})


def _do_buy_land(farm, board_size):
    if id(farm) not in _CREDIT:
        return _orig_land(farm, board_size)
    extra = len(farm["unlocked_quadrants"]) - 1
    if extra >= len(K.LAND_ORDER):
        _STATS["denied_noncash"] += 1
        return
    farm["money"] -= K.LAND_PRICES[extra]
    quadrant = K.LAND_ORDER[extra]
    farm["unlocked_quadrants"].append(quadrant)
    for y in range(board_size):
        for x in range(board_size):
            if K._quadrant_of(x, y, board_size) == quadrant and farm["tiles"][y][x] == "LOCKED":
                farm["tiles"][y][x] = None


K._commit_unit = _commit_unit
K._do_hire = _do_hire
K._do_buy_land = _do_buy_land


def load_agent(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.agent


def load_record(path: Path):
    with gzip.open(path, "rt") as fh:
        return json.load(fh)


def _config(record):
    config = {k: v for k, v in (record.get("configuration") or {}).items() if k != "seed"}
    config.pop("runTimeout", None)
    return config


def play(record, agent=None, cand_seat=0, force_shops=True):
    """One game. With agent=None both seats replay their recorded actions."""
    _CREDIT.clear()
    engine = Engine(int(record["seed"]), _config(record))
    opp_seat = 1 - cand_seat
    if agent is not None:
        _CREDIT.add(id(engine.state[0].observation.farms[opp_seat]))

    actions = record["actions"]
    shops = record["shops"]
    min_cash = [0.0, 0.0]
    ever_negative = [False, False]
    worst_call = 0.0
    for i, pair in enumerate(actions):
        if engine.done:
            break
        if agent is None:
            submit = [pair[0] or {}, pair[1] or {}]
        else:
            t0 = time.perf_counter()
            own = agent(engine.observation(cand_seat), dict(engine.configuration))
            worst_call = max(worst_call, time.perf_counter() - t0)
            submit = [None, None]
            submit[cand_seat] = own
            submit[opp_seat] = pair[opp_seat] or {}
        engine.step(submit)
        farms = engine.state[0].observation.farms
        for s in (0, 1):
            money = float(farms[s]["money"])
            min_cash[s] = min(min_cash[s], money)
            if money < 0:
                ever_negative[s] = True
        if force_shops and agent is not None and i < len(shops) and shops[i] is not None:
            # Shared object: assigning here updates the town for both seats.
            engine.state[0].observation.town["unlocked_shops"] = list(shops[i])

    farms = engine.state[0].observation.farms
    return {
        "scores": [float(farms[0]["money"]), float(farms[1]["money"])],
        "min_cash": min_cash,
        "ever_negative": ever_negative,
        "max_call_seconds": worst_call,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", type=Path)
    ap.add_argument("--cache", type=Path, default=ROOT / "cache")
    ap.add_argument("--verify", action="store_true", help="replay both recorded seats")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--split", choices=["development", "validation", "final_test"],
                    help="restrict to the prior session's split, for a comparable number")
    ap.add_argument("--prior", type=Path, default=ROOT / "reports" / "prior_per_game_results.json")
    ap.add_argument("--label", default="bench")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()

    paths = sorted(p for p in args.cache.glob("*.json.gz"))
    if args.split:
        prior = json.loads(args.prior.read_text())
        wanted = {str(g["game_id"]) for g in prior["games"] if g["split"] == args.split}
        paths = [p for p in paths if p.name.split(".")[0] in wanted]
        print(f"split {args.split}: {len(wanted)} games in prior manifest, {len(paths)} present in cache")
    if args.limit:
        paths = paths[: args.limit]
    if not paths:
        print("no cached games found")
        return 1

    if args.verify:
        exact = mismatched = failed = 0
        diffs = []
        for path in paths:
            record = load_record(path)
            if not record.get("rewards") or record.get("seed") is None:
                failed += 1
                continue
            try:
                result = play(record, agent=None)
            except Exception as exc:
                failed += 1
                print(f"  !! {path.name}: {type(exc).__name__}: {exc}")
                continue
            want = [float(x) if x is not None else None for x in record["rewards"]]
            got = result["scores"]
            if want == got:
                exact += 1
            else:
                mismatched += 1
                if len(diffs) < 5:
                    diffs.append({"game": path.stem, "recorded": want, "replayed": got})
        print(f"FIDELITY: {exact}/{len(paths)} games reproduced exactly"
              f" ({mismatched} mismatched, {failed} unusable)")
        for d in diffs:
            print("   ", d)
        return 0

    agent = load_agent(args.agent, "candidate")
    rows = []
    t0 = time.time()
    for path in paths:
        record = load_record(path)
        if record.get("seed") is None:
            continue
        names = record.get("team_names") or ["?", "?"]
        for cand_seat in (0, 1):
            opp_seat = 1 - cand_seat
            try:
                result = play(record, agent=agent, cand_seat=cand_seat)
            except Exception as exc:
                rows.append({"game": path.stem, "cand_seat": cand_seat, "status": "candidate_error",
                             "error": f"{type(exc).__name__}: {exc}"})
                continue
            cand, opp = result["scores"][cand_seat], result["scores"][opp_seat]
            rows.append({
                "game": path.stem, "cand_seat": cand_seat, "status": "valid",
                "opponent_name": names[opp_seat] if opp_seat < len(names) else "?",
                "candidate_score": cand, "opponent_score": opp,
                "candidate_won": cand > opp,
                "opponent_debt_ever": result["ever_negative"][opp_seat],
                "opponent_min_cash": result["min_cash"][opp_seat],
                "opponent_final_debt": max(0.0, -opp),
                "max_call_seconds": result["max_call_seconds"],
            })

    valid = [r for r in rows if r["status"] == "valid"]
    errors = [r for r in rows if r["status"] != "valid"]
    wins = sum(1 for r in valid if r["candidate_won"])
    margins = sorted(r["candidate_score"] - r["opponent_score"] for r in valid)
    summary = {
        "label": args.label, "agent": str(args.agent), "split": args.split,
        "games": len(paths), "matches": len(valid), "candidate_errors": len(errors),
        "wins": wins, "win_rate": wins / len(valid) if valid else 0.0,
        "median_margin": st.median(margins) if margins else 0.0,
        "mean_candidate_score": st.mean([r["candidate_score"] for r in valid]) if valid else 0.0,
        "median_candidate_score": st.median([r["candidate_score"] for r in valid]) if valid else 0.0,
        "median_opponent_score": st.median([r["opponent_score"] for r in valid]) if valid else 0.0,
        "opponent_debt_matches": sum(1 for r in valid if r["opponent_debt_ever"]),
        "opponent_final_debt_matches": sum(1 for r in valid if r["opponent_final_debt"] > 0),
        "denied_noncash_market_units": _STATS["denied_noncash"],
        "max_call_seconds": max((r["max_call_seconds"] for r in valid), default=0.0),
        "wall_seconds": time.time() - t0,
    }
    print(f"=== {args.label} : {args.agent.name} ===")
    print(f"matches {summary['matches']}  errors {summary['candidate_errors']}"
          f"  wall {summary['wall_seconds']:.0f}s  max_call {summary['max_call_seconds']*1000:.0f}ms")
    print(f"WIN RATE  {wins}/{summary['matches']} = {summary['win_rate']:.1%}")
    print(f"median margin {summary['median_margin']:,.0f}"
          f" | candidate median {summary['median_candidate_score']:,.0f}"
          f" | opponent median {summary['median_opponent_score']:,.0f}")
    print(f"opponent went into debt in {summary['opponent_debt_matches']} matches,"
          f" finished in debt in {summary['opponent_final_debt_matches']}")
    for e in errors[:3]:
        print("  ERROR", e)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"summary": summary, "matches": rows}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
