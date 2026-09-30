"""Package the selected agent into one self-contained Kaggle main.py.

The development agents load the tape backbone through importlib so the two can
be measured separately. A Kaggle submission must be a single module with no
local imports and no environment lookups, so this inlines the backbone, drops
the import machinery, and freezes the tuned configuration as literals.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent

HEADER = '''"""Kaggriculture submission.

Production follows a recorded MMPQ trajectory ("the tape"), selected by the
first store to unlock. On top of that, every tape command that provably does
nothing in the current game is replaced with useful work the unit can perform
from the tile it already occupies.

Why that shape, from measurements on the official interpreter:

* The tape spends 43.2% of its unit-actions on movement and a further 12.2%
  (about 925 per game) on outright no-ops -- watering bare earth, caring for an
  animal that is not there -- because the recording came from a different game.
  With the 247 PASS commands per game, roughly 1,170 actions a game are idle.
* Substitutions must not move the unit. Allowing detours of even two tiles
  scored 0.88x the backbone, because moving desynchronises the tape's
  positional schedule and its route correction costs more than the work earns.
* Substitutions must not consume shared resources. Re-using the tape's seeds,
  animals, wheat or fertilizer turns its own scheduled commands into no-ops and
  scored 0.80x. The permitted actions therefore consume nothing.
* The tape's market head is load-bearing: it is an open-loop schedule whose
  purchases must land on time, and rewriting its selling logic left animals
  unplaced and 72 tiles empty. It is left untouched.
* CARE is the single most valuable free action. It banks a bonus paid on the
  animal's next yield, roughly doubling a goose's output and tripling a cow's,
  for one action and no resources.
"""
'''


def build(agent_path: Path, tape_path: Path, out: Path, config: dict) -> dict:
    tape_src = tape_path.read_text()
    agent_src = agent_path.read_text()

    # Drop the development-only preamble: module docstring, imports, and the
    # importlib bootstrap that pulled in the tape.
    agent_src = re.sub(r'^""".*?"""\n', "", agent_src, count=1, flags=re.S)
    agent_src = re.sub(
        r"from __future__ import annotations\n|"
        r"^import importlib\.util\n|^import os\n|^import sys\n|"
        r"^from pathlib import Path\n",
        "", agent_src, flags=re.M)
    agent_src = re.sub(
        r"^_ROOT = .*?\n_spec\.loader\.exec_module\(_tape\)\n", "", agent_src, flags=re.M | re.S)
    # Freeze the tuned configuration; Kaggle has no environment to read.
    for key, value in config.items():
        if key == "ALLOW" and isinstance(value, list):
            value = set(value)
        agent_src = re.sub(
            rf'^{key} = .*$', f"{key} = {value!r}", agent_src, count=1, flags=re.M)
    # Rewire the layer onto the renamed backbone entry point BEFORE stripping
    # the module prefix, otherwise `_tape.agent(...)` collapses into a
    # self-recursive `agent(...)`.
    agent_src = agent_src.replace("_tape.agent(", "_tape_agent(")
    agent_src = agent_src.replace("_tape.", "")
    agent_src = re.sub(r"^\s*$\n(\s*$\n)+", "\n\n", agent_src, flags=re.M)

    # Rename the backbone's entry point so the layer below can own `agent`.
    tape_src = tape_src.replace("# Independent MMPQ tape candidate. Train80 only. See sibling build manifest.\n", "")
    if "def agent(obs,configuration=None):" not in tape_src:
        raise SystemExit("backbone entry point not found; cannot rename safely")
    tape_src = tape_src.replace("def agent(obs,configuration=None):", "def _tape_agent(obs,configuration=None):")
    tape_src = tape_src.replace("agent.telemetry=_TELEMETRY", "_tape_agent.telemetry=_TELEMETRY")

    parts = [
        HEADER,
        "# " + "-" * 74 + "\n# Recorded-trajectory backbone (verbatim, entry point renamed).\n# " + "-" * 74 + "\n",
        tape_src,
        "\n# " + "-" * 74 + "\n# Idle-command substitution layer.\n# " + "-" * 74 + "\n",
        agent_src,
    ]
    # Guard against name collisions: the two sources share one namespace once
    # merged, and a clash silently breaks the backbone (its _TELEMETRY was
    # shadowed this way). `agent` is the one intended override.
    def toplevel(src):
        names = set()
        for line in src.splitlines():
            m = re.match(r"(?:def|class)\s+(\w+)", line) or re.match(r"(\w+)\s*(?::[^=]+)?=[^=]", line)
            if m:
                names.add(m.group(1))
        return names

    clash = toplevel(tape_src) & toplevel(agent_src)
    if clash:
        raise SystemExit(f"name collision between backbone and agent layer: {sorted(clash)}")

    merged = "".join(parts)
    # The substitution layer calls the backbone through this name.
    merged = merged.replace("def agent(obs, configuration=None):\n    action = agent(obs, configuration)",
                            "def agent(obs, configuration=None):\n    action = _tape_agent(obs, configuration)")
    leftover = [name for name in ("os.environ", "importlib", "_tape.") if name in merged]
    if leftover:
        raise SystemExit(f"submission is not self-contained, found: {leftover}")
    out.write_text(merged)
    return {
        "output": str(out),
        "bytes": len(merged.encode()),
        "sha256": hashlib.sha256(merged.encode()).hexdigest(),
        "config": config,
        "source_agent": str(agent_path),
        "source_tape": str(tape_path),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", type=Path, default=ROOT / "agent" / "v6.py")
    ap.add_argument("--tape", type=Path, default=ROOT / "baseline" / "tape_agent.py")
    ap.add_argument("--output", type=Path, default=ROOT / "submission" / "main.py")
    ap.add_argument("--config", type=Path, help="JSON of frozen constants")
    args = ap.parse_args()
    config = json.loads(args.config.read_text()) if args.config else {
        "REPLACE_NOOPS": True,
        "ALLOW": {"WATER", "HARVEST", "CARE", "DIG"},
        "FEED_URGENT": False,
        "PLACE_LATE_DAY": -1,
        "PLANT_EXCESS": False,
        "TAPE_FORCE": "",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    manifest = build(args.agent, args.tape, args.output, config)
    (args.output.parent / "build_manifest.json").write_text(json.dumps(manifest, indent=1, default=str))
    print(json.dumps({k: v for k, v in manifest.items() if k != "config"}, indent=1))


if __name__ == "__main__":
    raise SystemExit(main())
