"""Stream recorded replays into a compact cache.

Each replay JSON is ~37 MB, and there are ~392 of them, so nothing is extracted
to disk and only one is held in memory at a time. What the benchmark actually
needs per game is small: the episode seed, the configuration, both seats'
recorded actions, and the store schedule the original game produced.

The action stored at steps[t] was chosen from the observation at steps[t-1] and
is the action applied by transition t, which is how it is replayed.
"""
from __future__ import annotations

import argparse
import gzip
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _town(step):
    for seat in step:
        obs = seat.get("observation") or {}
        town = obs.get("town")
        if town is not None:
            return list(town.get("unlocked_shops", []))
    return None


def extract(replay):
    steps = replay["steps"]
    info = replay.get("info") or {}
    actions = []
    shops = []
    for t in range(1, len(steps)):
        step = steps[t]
        actions.append([
            (step[0].get("action") if len(step) > 0 else None),
            (step[1].get("action") if len(step) > 1 else None),
        ])
        shops.append(_town(step))
    config = dict(replay.get("configuration") or {})
    seed = info.get("seed", config.get("seed"))
    return {
        "episode_id": info.get("EpisodeId") or replay.get("id"),
        "seed": seed,
        "configuration": config,
        "team_names": list(info.get("TeamNames") or []),
        "rewards": list(replay.get("rewards") or []),
        "statuses": replay.get("statuses"),
        "n_steps": len(steps),
        "actions": actions,
        "shops": shops,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--out", type=Path, default=ROOT / "cache")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    seen = {}
    written = skipped = failed = 0
    for archive in sorted(args.data.glob("*.zip")):
        try:
            zf = zipfile.ZipFile(archive)
        except zipfile.BadZipFile:
            print(f"  !! {archive.name}: not a zip, skipping")
            continue
        names = [n for n in zf.namelist()
                 if n.endswith(".json") and "__MACOSX" not in n and not Path(n).name.startswith("._")]
        for name in names:
            try:
                with zf.open(name) as fh:
                    replay = json.load(fh)
                record = extract(replay)
                del replay
            except Exception as exc:
                failed += 1
                print(f"  !! {archive.name}:{name}: {type(exc).__name__}: {exc}")
                continue
            key = str(record["episode_id"])
            if key in seen:
                skipped += 1
                continue
            seen[key] = archive.name
            target = args.out / f"{key}.json.gz"
            with gzip.open(target, "wt") as fh:
                json.dump(record, fh)
            written += 1
        print(f"{archive.name}: {len(names)} json -> cached {written} total, {skipped} dup")
    manifest = {
        "games": written, "duplicates": skipped, "failures": failed,
        "episode_to_archive": seen,
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"\ncached {written} unique games, {skipped} duplicates, {failed} failures")


if __name__ == "__main__":
    raise SystemExit(main())
