# Experiment log

All figures are measured, not estimated, unless the row says otherwise.

## Live-simulation results (paired score ratio vs the shipped tape agent)

Games are generated from seeds by the official interpreter; each candidate
plays the tape agent on the same seeds in both seats. Ratio is the median of
per-match `candidate / opponent`.

### Substitution layer, tuning seeds 1000+

| Variant | 16 seeds | 24 seeds |
|---|---:|---:|
| Tape backbone (reference) | 1.000 | 1.000 |
| PASS -> in-place work (`RADIUS=0`) | 1.031 | — |
| PASS -> work, 1-tile detours | 0.883 | — |
| PASS -> work, 2-tile detours | 0.893 | — |
| + no-op substitution, WATER/HARVEST/CARE/DIG | 1.044 | 1.031 |
| + no-op substitution, CARE only | 1.043 | — |
| + also FEED/COLLECT_FERTILIZER/FERTILIZE | 0.977 | — |
| + also PLANT/PLACE | 0.805 | — |

CARE alone captures essentially the whole gain. The resource-consuming
substitutions lose because the tape buys seeds, animals and wheat to exact
targets, so borrowing from those pools turns its own later commands into
no-ops.

### Narrow re-admissions of the losing substitutions (24 seeds)

| Variant | Ratio |
|---|---:|
| WATER/HARVEST/CARE/DIG (base) | 1.031 |
| + FEED only when the animal escapes tonight | 1.031 (never fires) |
| + PLACE only after day 18 | 1.031 (never fires) |
| + PLANT only from seeds beyond the tape's target | 0.975 |
| + all three | 0.975 |

None help. FEED and PLACE never fire: the tape already feeds reliably
(`FEED:no-wheat` occurs 4 times a game) and idle units rarely hold an animal.

### Forced production tape (16 tuning seeds, then 48 held-out seeds)

| Tape | Tuning | Held out |
|---|---:|---:|
| store-keyed (shipped rule) | 1.031 | 1.027 |
| PIZZA_SHOP | 1.063 | 1.076 |
| SMOOTHIE_SHOP | 1.013 | 1.096 |
| PET_CAFE | 1.031 | 1.033 |
| FARMERS_MARKET | 1.000 | — |
| BAKERY | 0.992 | — |
| ICE_CREAM_SHOP | 0.846 | — |
| BRUNCH_SPOT | 0.813 | — |
| YARN_STORE | 0.721 | — |

PIZZA_SHOP and SMOOTHIE_SHOP swap places between the two seed sets, so the gap
between the leading tapes is inside the noise at this sample size. Tape choice
is therefore decided on the recorded-game validation split instead, and only
the chosen tape is reported on final test.

## Rejected approaches

| Approach | Ratio | Why it failed |
|---|---:|---|
| Reserve-price metered selling | 0.671 | Unit prices rose (strawberry $209 vs $147, milk $120 vs $78) but volume fell 250 units for no net revenue, and held stock starved reinvestment. The market is already near its absorption limit for this production mix. |
| Rewritten market head + capital discipline | 0.102 | The tape is an open-loop schedule whose purchases must land on time. Deferring animal buys desynchronised its pickup/place steps: animals piled up in the shed and 72 tiles sat empty. |
| From-scratch closed-loop optimiser | 0.244 | Abandoned. Five iterations gained ~0.05 each, so it could not overtake the backbone in budget. Diagnosis in `agent/v5.py`. |

## Diagnostics on the shipped agent

Per seat per game, averaged over live self-play games:

* 7,559 unit-commands, of which 43.2% movement and 12.2% (925) no-ops.
* 247 PASS commands; 227 of them within three steps of real work.
* Price realisation 89.6% of base value on 1,573 units sold.
* Tile utilisation 85-88% of unlocked tiles from day 12 onward.
* ~13 animals bought and never placed (~5k of idle capital).

Market structure, from the official price curves:

* Selling 40 units in one order realises 82% of base for strawberry, 74% for
  milk, but 98% for melon.
* The `hinge` products spike hard when the town drains them: tomato $300/unit
  at 400 below equilibrium and $900 at 600 below, against a $60 base; carrot
  $267, egg $416.

## Recorded-opponent benchmark

Fidelity: replaying both recorded seats reproduces the original final scores
exactly (see `reports/fidelity_full.txt`). This is what licenses the rest.

On the 50 games of `Archive.zip` (MMPQ's own games, so half of the 100 matches
face MMPQ itself and the subset is not representative):

| Candidate | Win rate | Median margin | Candidate median |
|---|---:|---:|---:|
| tape backbone | 24/100 = 24.0% | -11,308 | 93,024 |
| + substitution, store-keyed | 25/100 = 25.0% | -11,086 | 93,008 |
| + substitution, PIZZA_SHOP | 22/100 = 22.0% | -8,189 | 94,232 |

Split results over the full 392-game cache are in `reports/bench_*.json`.
