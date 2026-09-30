# Four uploaded candidates: which is strongest

Quick screen, live seeded simulation, 6 seeds (1000-1005), every pair played in
both seat orders: 72 games, 0 crashes, worst agent call 118 ms.

## The four uploads are only two agents

| Upload | SHA256 (first 8) | Distinct? |
|---|---|---|
| `market_snipe_v1_main.py` | `020be9d5` | agent **A** |
| `a-song-of-ice-and-fire-fixed-flexible.ipynb` | `020be9d5` | **byte-identical to A** |
| `marketshock_m1_wr1_main.py` | `853472b4` | agent **B** |
| `kaggriculture.zip` | — | docs only (README + AGENTS.md), no agent |

The notebook's last cell unpacks a gzip+base64 payload whose embedded
`EXPECTED_SHA256` matches what it ships, so the archive is intact — but that
payload is `market_snipe`, not the MarketShock build its own markdown claims
("The last cell now submits MarketShock-M1-WR1K"). **The notebook prose is stale
against its payload.** Anyone submitting the notebook is submitting market_snipe.

So the contest is market_snipe vs marketshock, plus our own two players.

## Standings

| Player | W | L | win% | mean score | median | min | max |
|---|---:|---:|---:|---:|---:|---:|---:|
| **`submission/main.py` (ours)** | 32 | 4 | **88.9%** | 110,551 | 118,753 | 68,761 | 160,091 |
| `market_snipe` (upload A) | 18 | 18 | 50.0% | 98,082 | 106,446 | 53,271 | 117,243 |
| `baseline/tape_agent.py` (ours) | 18 | 18 | 50.0% | 100,327 | 113,650 | 42,281 | 133,588 |
| `marketshock` (upload B) | 4 | 32 | 11.1% | 97,698 | 106,064 | 54,013 | 115,624 |

Head-to-head, out of 12 games per cell:

| | packaged_v6 | market_snipe | tape_baseline | marketshock |
|---|---|---|---|---|
| **packaged_v6** | – | 10-2 | 12-0 | 10-2 |
| **market_snipe** | 2-10 | – | 4-8 | 12-0 |
| **tape_baseline** | 0-12 | 8-4 | – | 10-2 |
| **marketshock** | 2-10 | 0-12 | 2-10 | – |

## Findings

1. **Of the uploads, `market_snipe` is the stronger one, and it is not close:
   12-0 against `marketshock` head to head.** Their mean scores differ by only
   0.4% (98,082 vs 97,698), so the sweep is not about raw output — it is that
   market_snipe's Pipe-16 HybridOpening reliably lands slightly ahead in the
   shared market whenever the two meet. marketshock is the weakest of the four.
2. **Neither upload beats what we already ship.** `submission/main.py` takes
   10-2 off market_snipe and 12-0 off our own tape baseline. Adopting either
   upload would be a downgrade on this measure.
3. **The opponent matters here.** marketshock scores 76,638 against market_snipe
   but 99,779 against the tape baseline on the same seed 1000, so these agents
   do interact through market prices; this is not a pure solo score race.
   Seat order, by contrast, changed no score at all in 36 swapped pairs — the
   game is symmetric under seat swap and all four agents are deterministic, so
   half of this run was redundant. Drop `order` from future sweeps.
4. **Non-transitive.** Our tape baseline beats market_snipe 8-4 while losing
   0-12 to the packaged agent. Rankings from a single opponent would mislead.

## What this does not establish

`eval/harness.py`'s live rankings are the instrument this repo already flagged
as unreliable for candidate selection (see README), and 6 seeds is a screen, not
a verdict. The trustworthy instrument is `bench/debt_benchmark.py` against the
recorded ladder opponents, which needs the `cache/` that `bench/cache_replays.py`
builds from `data/*.zip`. That cache is gitignored and absent here.

The user's Drive folder holds 13 zips (~380 MB) plus a README, which match what
`cache_replays.py --data` expects. Dropping them into `data/` and rebuilding the
cache would let both uploads be scored on the real benchmark — the next step if
either candidate is actually under consideration.
