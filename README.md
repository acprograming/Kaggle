# Kaggriculture agent: score-optimisation pass

Continuation of the MMPQ cloning work. The earlier project reached **46.8% win
rate** (73/156) on a sealed pool of recorded opponents. The goal here was to
push past 50%.

## Conclusion up front

The imitation framing was the wrong lever, and the diagnosis is arithmetic:
MMPQ itself scores a median ~108k, the existing agent already scores ~95k, and
the prior session measured a 49.2% ceiling on plan-library selection. Cloning
harder could not cross 50%.

What *does* matter is absolute score, because the win-rate curve is very steep
at the current operating point. Applying a flat score multiplier to the 784
recorded matches:

| Score multiplier | development | validation | final test |
|---|---:|---:|---:|
| 1.00 (shipped agent) | 38.3% | 42.9% | 46.8% |
| 1.05 | 49.4% | 56.4% | 53.8% |
| 1.08 | 54.0% | 59.6% | 60.9% |
| 1.20 | 69.7% | 75.6% | 76.9% |

So **+5-8% score crosses 50%**. That reframed the work from imitation accuracy
to score.

## Method

Games are generated from seeds by the official interpreter, so no replay data
is needed to measure score: a game runs in ~1.5s. Each candidate plays the
shipped tape agent on the same seeds, in both seats, giving a paired score
ratio. That ratio is then applied to the 784 recorded opponent scores to
estimate stale-benchmark win rate. `eval/harness.py` does this.

This is an estimate, not the real benchmark. Our score in a real match depends
on the opponent's own market activity (the within-game seat spread averaged
10,543), so the figure needs confirming against the recorded opponents.

## What the measurements said

Diagnostics on the shipped agent, over live self-play games:

* **Price realisation is 77-90% of base value**, losing ~35k/game at the low
  end. Milk realised 59% of base, fertilizer 54%, wool 71%.
* **Tile utilisation is fine**: 85-88% of unlocked tiles are productive from
  day 12. An apparent 60-empty-tile end state was an artefact of one-time
  crops being removed on harvest.
* **43.2% of unit-actions are movement**, near the floor for tile work (a unit
  sweeping n adjacent tiles needs n actions plus n-1 steps).
* **12.2% of unit-actions (925/game) are outright no-ops** and a further
  247/game are PASS. About 1,170 actions a game do nothing.
* ~13 animals a game are bought and never placed: roughly 5k of dead capital.

### Market structure worth knowing

Prices move on market inventory, and the town's consumption is what keeps a
product scarce. So the products the town drains sell *above* base, and the mix
should follow the shops. Selling 40 units in one order realises 82% of base for
strawberry and 74% for milk, but 98% for melon.

The `hinge` products have explosive upside when drained: tomato reaches $300/
unit at 400 below equilibrium and $900 at 600 below, against a $60 base. Carrot
reaches $267 and egg $416.

## What worked, and what did not

Each change was measured against the tape backbone on matched seeds.

| Change | Paired score ratio |
|---|---:|
| Tape backbone (reference) | 1.000 |
| **Substitute idle commands, in place only** | **1.031-1.044** |
| Idle substitution with 1-tile detours | 0.883 |
| Idle substitution with 2-tile detours | 0.893 |
| Substitution incl. resource-consuming actions | 0.977 |
| Substitution incl. PLANT and PLACE | 0.805 |
| Metered, reserve-price selling | 0.671 |
| Rewritten market head + capital discipline | 0.102 |
| From-scratch closed-loop optimiser | 0.244 |

Three findings explain that table:

1. **Do not move a unit.** The tape is an open-loop positional schedule. Moving
   an idle unit makes its route correction fire, which costs more than the work
   gains. Acting from the tile already occupied is free; detouring is not.
2. **Do not spend shared resources.** Re-using the tape's seeds, animals, wheat
   or fertilizer turns its own later commands into no-ops. The permitted
   substitutions (WATER, HARVEST, CARE, DIG) consume nothing. CARE alone
   accounts for essentially the whole gain: it banks a bonus paid on the
   animal's next yield, roughly doubling a goose's output and tripling a cow's,
   for one action and no resources.
3. **Do not touch the market head.** Purchases must land on schedule. A
   reserve-price seller raised unit prices (strawberry $209 vs $147, milk $120
   vs $78) but cut volume by 250 units for no net revenue, and starved
   reinvestment. A fuller rewrite left animals unplaced and 72 tiles empty.

The from-scratch optimiser is retained in `agent/v5.py` with its diagnosis. It
was abandoned at 0.244 of the backbone: each iteration gained ~0.05, so it
could not overtake the tape within the time budget. Its ceiling is genuinely
higher (a static allocation model estimates 126-145k of net revenue against the
backbone's ~110k realised) and the notes record what a further attempt needs:
plantings capped by watering capacity, hiring funded before seeds, stable
target shares, and zone-based routing.

## Layout

```
agent/v6.py             selected agent: backbone + in-place idle substitution
agent/v5.py             from-scratch optimiser (abandoned, diagnosis in module docstring)
agent/market_policy.py  reserve-price selling (measured as a net loss; kept for the record)
baseline/tape_agent.py  the previously shipped agent, used as the paired reference
eval/harness.py         seeded live-simulation evaluation and win-rate estimation
sim/                    official interpreter, config and supplied rules
build_submission.py     inlines the backbone into one standalone Kaggle main.py
submission/main.py      packaged submission
reports/                measurement outputs, incl. the prior 784-match results
```

## Reproduce

```bash
python3 eval/harness.py --agent agent/v6.py --seeds 24 --label v6
python3 build_submission.py
```

`V6_ALLOW`, `V6_FEED_URGENT`, `V6_PLACE_LATE` and `V6_PLANT_EXCESS` select the
substitution set during development; `build_submission.py` freezes the chosen
values as literals, since Kaggle has no environment to read.

## Open items

* The 392-game recorded-opponent benchmark was not re-run: this session's
  network policy denied `drive.google.com`, so the archives were unreachable,
  and the Drive MCP tool returns file bytes through the model's context
  (~10M tokens for one 30 MB archive). The win-rate figures here are therefore
  estimates from the paired score ratio, not measured match outcomes.
* `www.kaggle.com` was likewise denied, so the live submission's episodes could
  not be reviewed.
