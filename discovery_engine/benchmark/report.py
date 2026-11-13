"""Run the ablation matrix (fresh scenario per config) and render it."""
from __future__ import annotations

from dataclasses import asdict, fields

from discovery_engine.benchmark.ablation import CONFIGS
from discovery_engine.benchmark.metrics import Metrics, score
from discovery_engine.benchmark.runner import run_benchmark
from discovery_engine.benchmark.scenario import build_scenario


def run_matrix(configs=CONFIGS, *, tmp_path_factory) -> dict:
    out = {}
    for name, cfg in configs.items():
        sc = build_scenario(tmp_path_factory.mktemp(f"bench_{name}"))
        out[name] = score(run_benchmark(sc, cfg, oracle=cfg.oracle(sc.ground_truth)),
                          sc.ground_truth)
    return out


def render_matrix(matrix) -> str:
    cols = [f.name for f in fields(Metrics)]
    w = max([len("config")] + [len(n) for n in matrix])
    lines = [f"{'config':<{w}}  " + "  ".join(cols)]
    for name, m in matrix.items():
        d = asdict(m)
        lines.append(f"{name:<{w}}  " + "  ".join(f"{str(d[c]):<{len(c)}}" for c in cols))
    return "\n".join(lines)
