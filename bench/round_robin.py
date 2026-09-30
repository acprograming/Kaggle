"""Round-robin: uploaded candidates vs our folder players, seat-swapped."""
import sys, time, json, itertools, statistics as st
from pathlib import Path
ROOT = Path('/home/user/Kaggle')
sys.path.insert(0, str(ROOT/'sim')); sys.path.insert(0, str(ROOT/'eval'))
from harness import load_agent, play

S = Path('/tmp/claude-0/-home-user-Kaggle/144ecf55-1d0e-5471-9851-02de0cdcdaeb/scratchpad/agents')
PLAYERS = {
    'marketshock':  S/'marketshock.py',
    'market_snipe': S/'market_snipe.py',      # byte-identical to the notebook
    'packaged_v6':  ROOT/'submission'/'main.py',
    'tape_baseline':ROOT/'baseline'/'tape_agent.py',
}
SEEDS = [int(x) for x in sys.argv[1].split(',')] if len(sys.argv) > 1 else list(range(1000, 1006))

# two independent module instances per player: agents hold per-seat state
INST = {}
for name, path in PLAYERS.items():
    INST[name] = (load_agent(path), load_agent(path))
    print(f'loaded {name}', flush=True)

games, errors = [], []
t_start = time.time()
for a, b in itertools.combinations(PLAYERS, 2):
    for seed in SEEDS:
        for order in (0, 1):
            # order 0: a in seat0; order 1: a in seat1. Use distinct instances.
            if order == 0:
                s0n, s1n = a, b
                f0, f1 = INST[a][0], INST[b][1]
            else:
                s0n, s1n = b, a
                f0, f1 = INST[b][0], INST[a][1]
            t0 = time.time()
            m0, m1, shops, worst, err = play(seed, f0, f1)
            if err:
                errors.append({'seed': seed, 'pair': [s0n, s1n], 'error': err})
                print(f'ERR {s0n} vs {s1n} seed{seed}: {err}', flush=True)
                continue
            games.append({'seed': seed, 'seat0': s0n, 'seat1': s1n,
                          'score0': m0, 'score1': m1, 'shops': shops,
                          'max_call_s': worst})
            print(f'{s0n:>14} {m0:>9,.0f} | {s1n:<14} {m1:>9,.0f}  seed{seed} '
                  f'({time.time()-t0:.0f}s)', flush=True)

out = Path('/tmp/claude-0/-home-user-Kaggle/144ecf55-1d0e-5471-9851-02de0cdcdaeb/scratchpad/rr_results.json')
out.write_text(json.dumps({'games': games, 'errors': errors,
                           'seeds': SEEDS, 'wall': time.time()-t_start}, indent=1))
print(f'\n{len(games)} games, {len(errors)} errors, {time.time()-t_start:.0f}s -> {out}', flush=True)
