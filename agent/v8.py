"""V8: melon-rush opening, then hand the farm to the recorded trajectory.

Two measured facts, pulling in opposite directions:

* The melon opening wins its race decisively. Melon is the one product the town
  barely drains, and its quadratic glut curve means only the first ~150 units
  market-wide are worth anything. Planting the starting quadrant with melons
  next to the shed, harvesting at the top of day 10 and selling from hour 2
  yields 135 units for 21,036 against the backbone's 84 for 8,640 -- about
  +12,400 of margin where arriving together is worth +760.
* The from-scratch midgame is far weaker than the recorded trajectory. It built
  strawberry monocultures and left the board full of weeds; the backbone
  reliably reaches ~93k.

So take the opening from one and the rest from the other. The handover is
plausible because the backbone's market head buys toward per-step targets
rather than from a fixed budget, and day 10 hands it ~21k in cash, so those
purchases all clear at once instead of being rationed.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

_tspec = importlib.util.spec_from_file_location("_tape_v8", _ROOT / "baseline" / "tape_agent.py")
_tape = importlib.util.module_from_spec(_tspec)
sys.modules["_tape_v8"] = _tape
_tspec.loader.exec_module(_tape)

_vspec = importlib.util.spec_from_file_location("_v7_v8", _ROOT / "agent" / "v7.py")
_v7 = importlib.util.module_from_spec(_vspec)
sys.modules["_v7_v8"] = _v7
_vspec.loader.exec_module(_v7)

# Day on which control passes to the recorded trajectory. Melons cannot be
# harvested before age 10 (the engine refuses HARVEST before first_yield_day),
# and the sale runs through day 10, so 11 is the first clean handover.
HANDOVER_DAY = int(os.environ.get("V8_HANDOVER", "11"))
# Melon tiles to open with. The starting quadrant is 25 tiles.
MELON_TILES = int(os.environ.get("V8_MELON_TILES", "24"))
_v7.CFG["melon_tiles"] = MELON_TILES


def agent(obs, configuration=None):
    if obs["day"] < HANDOVER_DAY:
        return _v7.agent(obs, configuration)
    return _tape.agent(obs, configuration)
