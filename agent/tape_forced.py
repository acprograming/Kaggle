"""Tape agent with the production tape forced, for tape-selection experiments.

The shipped tape agent keys its whole 720-step plan on the FIRST store only.
That is a guess about which recorded MMPQ trajectory transfers best. This
variant lets any of the eight tapes be forced so their absolute scoring power
can be compared directly, instead of assumed.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("_tape_f", _ROOT / "baseline" / "tape_agent.py")
_tape = importlib.util.module_from_spec(_spec)
sys.modules["_tape_f"] = _tape
_spec.loader.exec_module(_tape)

FORCE = os.environ.get("TAPE_FORCE", "").strip()
_inner = _tape.agent


def agent(obs, configuration=None):
    if FORCE:
        seat = int(obs.get("player", 0))
        state = _tape._STATE.get(seat)
        if state is not None:
            state["key"] = FORCE
        result = _inner(obs, configuration)
        state = _tape._STATE.get(seat)
        if state is not None:
            state["key"] = FORCE
        return result
    return _inner(obs, configuration)
