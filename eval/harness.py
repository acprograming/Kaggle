"""Live-simulation evaluation for Kaggriculture agents.

No replay data required: games are generated from seeds by the official
interpreter. Scores are compared against a baseline agent on the SAME seeds,
which yields a paired uplift factor. That factor is then applied to the 784
recorded stale-benchmark matches to estimate win rate.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics as st
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "sim"))
from runner import Engine  # noqa: E402

_LOADED = 0


def load_agent(path: Path):
    global _LOADED
    _LOADED += 1
    name = f"_ag{_LOADED}_{Path(path).stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod.agent


def play(seed: int, agent_a, agent_b, config=None):
    """One full game. Returns (score_a, score_b, shops, max_call_seconds)."""
    engine = Engine(seed, config)
    worst = 0.0
    while not engine.done:
        obs = [engine.observation(0), engine.observation(1)]
        acts = []
        for idx, fn in ((0, agent_a), (1, agent_b)):
            t0 = time.perf_counter()
            try:
                act = fn(obs[idx], dict(engine.configuration))
            except Exception as exc:  # a crash is a real failure, not a skip
                return None, None, None, None, f"seat{idx}: {type(exc).__name__}: {exc}"
            worst = max(worst, time.perf_counter() - t0)
            acts.append(act)
        engine.step(acts)
    money = [engine.state[i].observation.farms[i]["money"] for i in range(2)]
    shops = list(engine.state[0].observation.town["unlocked_shops"])
    return money[0], money[1], shops, worst, None


def uplift_to_winrate(ratio: float, results_path: Path):
    """Apply a paired score ratio to the recorded stale-benchmark matches."""
    data = json.loads(results_path.read_text())
    out = {}
    for split in ("development", "validation", "final_test"):
        rows = [
            (s["candidate_score"], s["opponent_score"])
            for g in data["games"]
            if g["split"] == split
            for s in g["seats"]
            if s.get("status") == "valid"
        ]
        wins = sum(1 for c, o in rows if c * ratio > o)
        out[split] = {"n": len(rows), "wins": wins, "win_rate": wins / len(rows)}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", type=Path, required=True)
    ap.add_argument("--baseline", type=Path, default=ROOT / "baseline" / "tape_agent.py")
    ap.add_argument("--seeds", type=int, default=24)
    ap.add_argument("--seed-start", type=int, default=1000)
    ap.add_argument("--mode", choices=["vs-baseline", "self"], default="vs-baseline")
    ap.add_argument("--label", default="run")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()

    cand = load_agent(args.agent)
    base = load_agent(args.baseline)
    # Independent module instances: agents keep per-seat state keyed on player id,
    # so the same file loaded once cannot safely occupy both seats.
    base2 = load_agent(args.baseline)
    cand2 = load_agent(args.agent)

    rows, errors = [], []
    t0 = time.time()
    for i in range(args.seeds):
        seed = args.seed_start + i
        if args.mode == "self":
            a, b = cand, cand2
        else:
            a, b = cand, base
        s0, s1, shops, worst, err = play(seed, a, b)
        if err:
            errors.append({"seed": seed, "seat": 0, "error": err})
            continue
        # swap seats so seat bias cannot drive the result
        if args.mode == "self":
            c, d = cand, cand2
        else:
            c, d = base2, cand
        t0b, t1b, _, worst2, err2 = play(seed, c, d)
        if err2:
            errors.append({"seed": seed, "seat": 1, "error": err2})
            continue
        rows.append({
            "seed": seed,
            "cand_seat0": s0, "opp_seat1": s1,
            "cand_seat1": t1b, "opp_seat0": t0b,
            "shops": shops,
            "max_call_seconds": max(worst, worst2),
        })

    if not rows:
        print("NO COMPLETED GAMES")
        for e in errors[:5]:
            print("  ", e)
        return 1

    cand_scores = [r["cand_seat0"] for r in rows] + [r["cand_seat1"] for r in rows]
    opp_scores = [r["opp_seat1"] for r in rows] + [r["opp_seat0"] for r in rows]
    ratios = [c / o for c, o in zip(cand_scores, opp_scores) if o > 0]
    head_to_head = sum(1 for c, o in zip(cand_scores, opp_scores) if c > o)

    summary = {
        "label": args.label,
        "agent": str(args.agent),
        "mode": args.mode,
        "games": len(rows),
        "matches": len(cand_scores),
        "errors": len(errors),
        "cand_mean": st.mean(cand_scores),
        "cand_median": st.median(cand_scores),
        "cand_sd": st.pstdev(cand_scores) if len(cand_scores) > 1 else 0.0,
        "cand_min": min(cand_scores),
        "cand_max": max(cand_scores),
        "opp_mean": st.mean(opp_scores),
        "opp_median": st.median(opp_scores),
        "head_to_head_wins": head_to_head,
        "head_to_head_rate": head_to_head / len(cand_scores),
        "paired_ratio_median": st.median(ratios) if ratios else 0.0,
        "paired_ratio_mean": st.mean(ratios) if ratios else 0.0,
        "max_call_seconds": max(r["max_call_seconds"] for r in rows),
        "wall_seconds": time.time() - t0,
    }
    if args.mode == "vs-baseline":
        summary["estimated_stale_winrate"] = uplift_to_winrate(
            summary["paired_ratio_median"], ROOT / "reports" / "prior_per_game_results.json"
        )

    print(f"=== {args.label} ({args.agent.name}, mode={args.mode}) ===")
    print(f"games={summary['games']}  matches={summary['matches']}  errors={summary['errors']}"
          f"  wall={summary['wall_seconds']:.0f}s  max_call={summary['max_call_seconds']*1000:.0f}ms")
    print(f"candidate score : mean {summary['cand_mean']:>9,.0f}  median {summary['cand_median']:>9,.0f}"
          f"  sd {summary['cand_sd']:>8,.0f}  range {summary['cand_min']:,.0f}..{summary['cand_max']:,.0f}")
    print(f"opponent score  : mean {summary['opp_mean']:>9,.0f}  median {summary['opp_median']:>9,.0f}")
    print(f"head-to-head    : {head_to_head}/{len(cand_scores)} = {summary['head_to_head_rate']:.1%}")
    print(f"paired ratio    : median {summary['paired_ratio_median']:.4f}  mean {summary['paired_ratio_mean']:.4f}")
    if "estimated_stale_winrate" in summary:
        print("estimated stale-benchmark win rate (ratio applied to recorded matches):")
        for split, v in summary["estimated_stale_winrate"].items():
            print(f"    {split:12s} {v['wins']:3d}/{v['n']:3d} = {v['win_rate']:.1%}")
    if errors:
        print(f"ERRORS ({len(errors)}), first 3:")
        for e in errors[:3]:
            print("   ", e)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({"summary": summary, "games": rows, "errors": errors}, indent=1))
        print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
