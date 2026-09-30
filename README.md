# Kaggriculture agent: score-optimisation pass

Continuation of the MMPQ cloning work. The goal was to push the recorded-
opponent win rate past 50%.

## Result, stated plainly

**That target was not reached, and the change made is not distinguishable from
noise.** On the benchmark that defines the win rate:

| Split | Previously shipped agent | This agent |
|---|---:|---:|
| development (472 matches) | 181/472 = 38.3% | 186/472 = 39.4% |
| validation (156 matches) | 66/156 = 42.3% | 67/156 = 42.9% |
| final test (156 matches) | 72/156 = 46.2% | 73/156 = 46.8% |

Five extra wins on 472 matches is about half a standard deviation. The agent
matches the previous result rather than beating it.

What this pass does deliver is a trustworthy measuring instrument, a precise
size for the remaining gap, and seven dead ends closed with evidence.

## How big is the gap

Scaling the candidate's score against the recorded opponents, match by match:

| Score gain | development | final test |
|---|---:|---:|
| +0% (today) | 39.4% | 46.8% |
| +2% | 44.1% | 51.9% |
| **+5%** | **50.2%** | 53.8% |
| +10% | 58.5% | 64.7% |
| +20% | 72.7% | 79.5% |

**About +5% score is needed to cross 50% honestly.** Development is the largest
and hardest split; quoting final test alone would suggest +2% and flatter the
result. Every local lever found here is worth roughly +0.5% together, and only
5.5% of matches are lost by under 3,000 points, so there is no cluster of
near-misses to convert cheaply. Closing the gap needs a better production
policy, not further edits around the recorded trajectory.

## The measuring instrument

`bench/` reproduces the benchmark from the recorded games rather than trusting
a proxy. Two checks say it can be believed:

* Replaying both recorded seats reproduces the original final scores **exactly
  on 392/392 games**.
* Running the previously shipped agent through it returns **181/472 = 38.3%**
  on development, the same figure the earlier session reported, with the
  opponent entering debt in 151 matches and finishing in debt in 5 on final
  test, also matching.

The debt protocol is the earlier session's: the recorded opponent's commands
execute even when they push its cash negative, that debt counts against its
score, commands that fail for non-cash reasons become no-ops, and every game is
retained. Only the opponent is credited. The recorded store schedule is forced
after each transition, because store reveals and weed spawns draw from one RNG
stream, so a candidate leaving a different number of empty tiles would
otherwise face a different town than the opponent was recorded against.

`eval/harness.py` is the faster seeded live-simulation harness. **Its rankings
proved unreliable** and it should not be used to choose between candidates; see
below.

## The change that was kept

The agent follows the recorded MMPQ trajectory, and substitutes CARE for
commands that provably do nothing in the current game, but only where the unit
already stands. CARE banks a bonus paid on the animal's next yield, roughly
doubling a goose's output and tripling a cow's, for one action and no
resources.

The backbone wastes a lot: 43.2% of unit-actions are movement and a further
12.2% (925 per game) are no-ops — watering bare earth, caring for an animal
that is not there — because the recording came from a different game. With 247
PASS commands, about 1,170 actions a game do nothing.

Three constraints govern what may replace them, all measured:

1. **Never move the unit.** The backbone is an open-loop positional schedule.
   Detours of even one tile score 0.88x, because its route correction costs
   more than the extra work earns.
2. **Never spend shared resources.** Re-using its seeds, animals, wheat or
   fertilizer turns its own later commands into no-ops: -18pp on the real
   benchmark (24.4% against 42.9%).
3. **Never touch the market head.** Its purchases must land on schedule.
   Rewriting it left animals unplaced and 72 tiles empty.

## What was tried and rejected

All figures from the real benchmark unless marked.

| Approach | Result | Why |
|---|---:|---|
| Forced production tape (all 8) | best 42.3% vs 42.9% | The shipped store-keyed rule beats every forced tape. Worst is 20.5%. |
| Late-animal purchase cutoff | 42.9% at every threshold | No effect. The ~13 unplaced animals per game were a self-play artefact. |
| Resource-consuming substitutions | 24.4% | Steals from the backbone's exact purchase targets. |
| Rescue-on-move, ungated | 9.6% | A fresh seed starts at `consecutive_unwatered = 1`, so "dies tonight" is true of every new planting; it hijacked nearly all movement. |
| Rescue-on-move, hour-gated | 42.9%, unchanged | Never fires. |
| Reserve-price metered selling | 0.671x score (sim) | Higher unit prices, 250 fewer units sold, no net revenue, and held stock starved reinvestment. |
| Rewritten market head | 0.102x score (sim) | Desynchronised the backbone's purchase schedule. |
| From-scratch closed-loop optimiser | 0.244x score (sim) | Abandoned; ~0.05 gain per iteration could not overtake the backbone in budget. Diagnosis in `agent/v5.py`. |

## The live-simulation harness is not a valid selector

Worth recording, because it cost most of a session. `eval/harness.py` scores a
candidate against the shipped agent on matched seeds. It ranked forcing the
PIZZA_SHOP tape a clear winner (+7.6% score, an estimated 60.9%) on both tuning
and held-out seeds. On the real benchmark that configuration is **worse**:
44.9% against 46.8%.

The cause is structural, not sampling noise. Both players trade into one
market, so a candidate's own selling moves the opponent's realised prices too.
Forcing that tape raised our median score on final test (98,740 against 98,124)
while worsening the median margin (-2,469 against -1,318). **Absolute score and
score margin are different objectives here**, and a harness that scores against
a baseline which is itself moving measures the wrong one. SMOOTHIE_SHOP ranked
first of all nine options in simulation and eighth of nine on the benchmark.

## Layout

```
agent/v6.py             the agent: backbone + in-place CARE substitution
agent/v5.py             from-scratch optimiser (abandoned; diagnosis in the docstring)
agent/market_policy.py  reserve-price selling (measured as a net loss; kept for the record)
baseline/tape_agent.py  the previously shipped agent, used as the reference
bench/cache_replays.py  streams 392 replays out of their zips into a compact cache
bench/debt_benchmark.py the recorded-opponent benchmark and its fidelity check
eval/harness.py         seeded live simulation (fast, but NOT a valid selector)
sim/                    official interpreter, config and supplied rules
build_submission.py     inlines the backbone into one standalone Kaggle main.py
submission/main.py      packaged submission (417 KiB, worst turn 4 ms)
reports/experiments.md  every variant measured, including the ones that lost
```

## Reproduce

```bash
python3 bench/cache_replays.py                      # needs data/*.zip from Drive
python3 bench/debt_benchmark.py --verify            # must be 392/392
python3 bench/debt_benchmark.py --agent agent/v6.py --split final_test
python3 build_submission.py --config submission/config.json
```

`V6_ALLOW`, `V6_RESCUE`, `V6_NO_ANIMALS_FROM` and `TAPE_FORCE` select behaviour
during development; `build_submission.py` freezes the chosen values as literals,
since Kaggle has no environment to read, and refuses to emit a file that still
reads one.

## Where to go next

The gap is +5% score, and it is in production, not in trading or execution
around the tape. The closed-loop agent is the honest route, and `agent/v5.py`
records what a further attempt needs: plantings capped by watering capacity
(it planted beyond what its workforce could water and lost 36 tiles to weeds by
day 6), hiring funded before seeds (it spent every dollar on seeds, so it could
not hire, so it could not water), stable target shares (they thrashed, building
26 pastures that were never filled), and zone-based routing (68% of its actions
were movement).

Two facts from the price curves that a production policy should exploit, and
the current one does not: the products the town drains stay scarce and sell
*above* base, so the mix should follow `unlocked_shops`; and the `hinge`
products spike hard when drained — tomato reaches $300/unit at 400 below
equilibrium and $900 at 600 below, against a $60 base.
