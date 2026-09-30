"""Small harness around the unmodified official Kaggriculture interpreter.

This is not the Kaggle execution sandbox: subprocess isolation, time budgets,
and JSON-schema enforcement remain a separate deployment check. The underlying
game transitions are checked against complete supplied replays by verify_replays.
Only runnable callables are accepted by run_match; replay action playback is
exposed solely by the verification script, never as a claimed live opponent.
"""
from __future__ import annotations

import copy
import importlib.util
import inspect
import json
from pathlib import Path
import sys
import time
from typing import Callable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import kaggriculture as rules


class AttrDict(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


def _defaults():
    spec = json.loads((HERE / "kaggriculture.json").read_text())
    result = {"episodeSteps": 720, "actTimeout": 1, "runTimeout": 1200}
    for key, value in spec["configuration"].items():
        result[key] = value.get("default") if isinstance(value, dict) else value
    return result


class Engine:
    """Deterministic game state; agents only receive observation(player)."""
    def __init__(self, seed: int, configuration: dict | None = None):
        self.configuration = AttrDict(_defaults())
        self.configuration.update(copy.deepcopy(configuration or {}))
        self.configuration["seed"] = int(seed)
        self.info = {}
        self.done = False
        self.transitions = 0
        self.state = [AttrDict(action=None, observation=AttrDict(step=0, player=i),
                               status="ACTIVE", reward=0, info={}) for i in range(2)]
        rules.interpreter(self.state, self)

    def observation(self, player: int):
        obs = copy.deepcopy(self.state[player].observation)
        obs["step"] = self.transitions
        obs["remainingOverageTime"] = 60
        return obs

    def step(self, actions: list[dict]):
        if self.done:
            raise RuntimeError("Episode already finished")
        if len(actions) != 2 or any(not isinstance(a, dict) for a in actions):
            raise TypeError("Exactly two action dictionaries are required")
        for state, action in zip(self.state, actions):
            state.action = copy.deepcopy(action)
        rules.interpreter(self.state, self)
        self.transitions += 1
        self.state[0].observation.step = self.transitions
        if self.transitions >= self.configuration.episodeSteps - 1:
            for state in self.state:
                if state.status in {"ACTIVE", "INACTIVE"}:
                    state.status = "DONE"
        self.done = all(s.status not in {"ACTIVE", "INACTIVE"} for s in self.state)
        return self.state

    @property
    def rewards(self):
        return [s.reward for s in self.state]


def _wrap_callable(agent: Callable):
    if not callable(agent):
        raise TypeError("Expected a callable agent; recorded replay actions are not live opponents")
    parameters = inspect.signature(agent).parameters.values()
    positional = [p for p in parameters if p.kind in {p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD}]
    takes_config = len(positional) >= 2
    return lambda obs, config: agent(obs, config) if takes_config else agent(obs)


def run_match(agent0: Callable, agent1: Callable, *, seed: int,
              configuration: dict | None = None):
    """Play fresh callables on a seed. Callers must reset state between matches.

    Returns actual end-game coin rewards, statuses, and measured invocation
    times. Exceptions propagate and must count as failures in tournament code.
    This harness reports runtime but does not enforce Kaggle resource limits.
    """
    agents = [_wrap_callable(agent0), _wrap_callable(agent1)]
    env = Engine(seed=seed, configuration=configuration)
    max_seconds = [0.0, 0.0]
    total_seconds = [0.0, 0.0]
    while not env.done:
        observations = [env.observation(0), env.observation(1)]
        actions = []
        for i in range(2):
            started = time.perf_counter()
            action = agents[i](observations[i], copy.deepcopy(env.configuration))
            duration = time.perf_counter() - started
            max_seconds[i] = max(max_seconds[i], duration)
            total_seconds[i] += duration
            actions.append(action)
        env.step(actions)
    return {"seed": seed, "rewards": env.rewards,
            "statuses": [s.status for s in env.state],
            "transitions": env.transitions, "max_call_seconds": max_seconds,
            "total_call_seconds": total_seconds}


def load_agent(path: str | Path, module_name: str):
    """Load trusted Python source in a fresh module to isolate globals per match.

    This executes source. Only use code whose origin/license has been reviewed.
    Agents must define `agent`; use a distinct module_name for every loaded seat.
    """
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load agent: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    agent = getattr(module, "agent", None)
    if not callable(agent):
        raise TypeError(f"{path} does not define callable agent")
    return agent
