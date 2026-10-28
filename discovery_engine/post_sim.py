"""Shared post-simulation Step family: Results, Analysis, Visualization, ReportCard.

``ResultsStep`` is the family's head: it reads a finished run's emitted-output
location and writes one ``ResultsHandle`` (emitter-agnostic, built on
``viva_emitters``) to the ``results`` store, so a composite shaped
``[emitter output] -> ResultsStep -> {AnalysisStep, ReportCardStep, ...}``
runs deterministically off one read of the run. ``AnalysisStep`` reads that
handle. ``Analysis`` is a genuinely separate, non-deprecated base for the
live-``conn`` surface v2ecoli's ``analysis_runner.py`` drives directly
(``conn``/``history_sql``/``sim_data``/... injected straight into ``state``,
bypassing ``ResultsHandle``) — the two bases share only their
``scale``/registry machinery, not their I/O contract.

Moved from v2ecoli (``v2ecoli/workflow/post_sim.py``,
``v2ecoli/workflow/analysis.py``, ``v2ecoli/workflow/report_cards/__init__.py``)
as the study Evaluate-stage substrate: any workspace can build post-simulation
analyses / visualizations / report cards on these bases without depending on
v2ecoli.

The v2ecoli bases subclassed ``v2ecoli.steps.base.V2Step`` — process-bigraph's
``Step`` plus an error-swallowing ``invoke()`` (catches exceptions from
``update()`` and returns an empty update, so one broken Step doesn't crash the
Composite's step cascade). Importing ``V2Step`` here would drag a v2ecoli
dependency into this shared package, so the bases below subclass pbg's
``Step`` directly and carry that same ``invoke()`` guard **verbatim, in the
same place the original had it**:

  - ``VisualizationStep`` / ``ReportCardStep`` never overrode ``invoke()`` in
    v2ecoli (they inherited ``V2Step``'s swallow-on-error behavior), so they
    define it explicitly here with the same try/except guard.
  - ``Analysis`` / ``AnalysisStep`` explicitly overrode ``V2Step.invoke()`` in
    v2ecoli to fail loudly (a broken ``analyze()`` must surface, not silently
    return ``{}``); that override is preserved here unchanged.

Registries: each base auto-registers named subclasses (``__init_subclass__``)
into its own kind-specific registry (``ANALYSIS_REGISTRY``,
``VISUALIZATION_REGISTRY``, ``REPORT_CARD_REGISTRY``) AND funnels into the
unified ``POST_SIM_REGISTRY`` so a single flush can discover every post-sim
output kind-tagged from one place. Abstract bases (subclasses that don't set
their own ``name``) register nowhere.
"""
from __future__ import annotations

import json
import math
import pickle
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

import viva_emitters
from process_bigraph.composite import Step, SyncUpdate

KINDS = ("analysis", "visualization", "test")

# legacy kind string -> canonical kind string, accepted anywhere a kind is
# passed in (register_post_sim / iter_post_sim).
_LEGACY_KINDS = {"report_card": "test"}


def _normalize_kind(kind: "str | None") -> "str | None":
    if kind is None:
        return None
    return _LEGACY_KINDS.get(kind, kind)


# name -> {"cls": <Step subclass>, "kind": <one of KINDS>}
POST_SIM_REGISTRY: dict[str, dict] = {}

# kind-specific registries. Populated by each base's __init_subclass__ for any
# subclass that defines its own ``name``.
ANALYSIS_REGISTRY: dict[str, type] = {}
VISUALIZATION_REGISTRY: dict[str, type] = {}

# canonical: TEST_REGISTRY. REPORT_CARD_REGISTRY is the SAME dict object,
# kept for back-compat (v2ecoli's cards + workbench still read this name).
TEST_REGISTRY: dict[str, type] = {}
REPORT_CARD_REGISTRY: dict[str, type] = TEST_REGISTRY

# scale name -> human description of the result slice it consumes
ANALYSIS_SCALES: dict[str, str] = {
    "single": "one cell's timeseries",
    "multidaughter": "sister cells from one division",
    "multigeneration": "cells across a lineage's generations",
    "multiseed": "cells across seeds of one variant",
    "multivariant": "cells across all variants",
}


def register_post_sim(cls, kind: str, name: "str | None" = None) -> None:
    """Register a post-sim Step subclass under ``name`` (default ``cls.name``)
    with its ``kind``. No-op when the resolved name is falsy (abstract bases).
    Accepts legacy kind strings (e.g. ``"report_card"`` -> ``"test"``).
    Raises ValueError for an unknown kind."""
    kind = _normalize_kind(kind)
    if kind not in KINDS:
        raise ValueError(f"unknown post-sim kind {kind!r}; expected one of {KINDS}")
    nm = name if name is not None else getattr(cls, "name", "")
    if not nm:
        return
    POST_SIM_REGISTRY[nm] = {"cls": cls, "kind": kind}


def iter_post_sim(kind: "str | None" = None) -> list:
    """[(name, cls), ...] sorted by name, optionally filtered to one kind.
    Accepts legacy kind strings (e.g. ``"report_card"`` -> ``"test"``)."""
    kind = _normalize_kind(kind)
    out = [(nm, e["cls"]) for nm, e in POST_SIM_REGISTRY.items()
           if kind is None or e["kind"] == kind]
    return sorted(out, key=lambda t: t[0])


def _detect_backend(path: str) -> str:
    """Guess a store's emitter backend from its path shape. ``.db``/``.sqlite``
    (SQLite, an existing file) -> ``"sqlite"``; a ``.zarr`` root -> ``"xarray"``;
    anything else (an existing or not-yet-materialized directory) -> ``"parquet"``
    (the hive-partitioned ``ParquetEmitter`` layout)."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in (".db", ".sqlite", ".sqlite3"):
        return "sqlite"
    if suffix == ".zarr":
        return "xarray"
    return "parquet"


def _load_records_sqlite(path: str, simulation_id: "str | None") -> list[dict]:
    sim_ids = [simulation_id] if simulation_id else [
        s["simulation_id"] for s in viva_emitters.list_simulations(path)
    ]
    records: list[dict] = []
    for sid in sim_ids:
        records.extend(viva_emitters.load_history(path, sid))
    return records


def _load_records_parquet(path: str) -> list[dict]:
    create_duckdb_conn = getattr(viva_emitters, "create_duckdb_conn", None)
    if create_duckdb_conn is None:
        raise RuntimeError(
            "ResultsHandle: reading a parquet store requires the "
            "'viva-emitters[parquet]' extra (duckdb/polars/pyarrow not "
            "importable from viva_emitters)")
    conn = create_duckdb_conn()
    try:
        rel = conn.sql(
            f"SELECT * FROM read_parquet('{path}/**/*.pq', union_by_name=true)")
        return rel.pl().to_dicts()
    finally:
        conn.close()


def _load_records(paths: "list[str]", simulation_id: "str | None") -> list[dict]:
    """Read every store in ``paths`` and concatenate their records. Backend is
    auto-detected per path (mixed backends across ``paths`` are allowed)."""
    records: list[dict] = []
    for path in paths:
        backend = _detect_backend(path)
        if backend == "sqlite":
            records.extend(_load_records_sqlite(path, simulation_id))
        elif backend == "parquet":
            records.extend(_load_records_parquet(path))
        else:
            raise NotImplementedError(
                f"ResultsHandle: {backend!r} backend not yet wired for "
                f"{path!r} (sqlite + parquet are supported)")
    return records


@dataclass
class ResultsHandle:
    """Typed, emitter-agnostic handle over a study run's emitted results —
    the object ``ResultsStep`` writes to the ``results`` store and every
    downstream post-sim Step (``AnalysisStep``, ``ReportCardStep``, ...)
    reads from.

    Reconstructs from ``{paths, sim_data_ref, simulation_id}`` via
    ``from_config``/``to_config`` — a JSON-able config, not the live object —
    so a handle produced in one process is file-rehydratable in another (e.g.
    a report renderer opening a finished run from disk long after the
    Composite that produced it exited).

    - ``records(scale=None) -> list[dict]``: the run's emitted rows, read
      once and cached. Opens SQLite (via ``viva_emitters.load_history``) or
      DuckDB-over-parquet (``viva_emitters.create_duckdb_conn`` +
      ``read_parquet``), whichever ``paths`` points at. ``scale`` is accepted
      for forward-compatibility with the ``ANALYSIS_SCALES`` slicing
      convention; this shared package does not itself implement per-scale
      cell-record slicing (that logic — keyed by seed/generation — is
      workspace-specific and stays out of ``discovery_engine``), so today
      every ``scale`` returns the same full record set.
    - ``conn()``: a lazy DuckDB connection (``viva_emitters.create_duckdb_conn``)
      with the handle's records registered as a view named ``"results"``, for
      analyses that want direct SQL instead of record dicts. Only imports
      duckdb/polars when actually called.
    - ``sim_data``: lazily resolved from ``sim_data_ref`` — a pickle path is
      unpickled on first access; anything else is returned as-is (opaque to
      this package, resolved by the caller that set it).
    - ``paths``: the store path(s) this handle was built from.
    """

    paths: "list[str]"
    sim_data_ref: Any = None
    simulation_id: "str | None" = None

    def __post_init__(self) -> None:
        self._records: "list[dict] | None" = None
        self._conn: Any = None
        self._sim_data: Any = None
        self._sim_data_loaded: bool = False

    @classmethod
    def from_config(cls, config: dict) -> "ResultsHandle":
        return cls(
            paths=list(config.get("paths") or []),
            sim_data_ref=config.get("sim_data_ref"),
            simulation_id=config.get("simulation_id") or None,
        )

    def to_config(self) -> dict:
        return {
            "paths": list(self.paths),
            "sim_data_ref": self.sim_data_ref,
            "simulation_id": self.simulation_id,
        }

    def records(self, scale: "str | None" = None) -> "list[dict]":
        if self._records is None:
            self._records = _load_records(self.paths, self.simulation_id)
        return self._records

    def conn(self):
        if self._conn is None:
            create_duckdb_conn = getattr(viva_emitters, "create_duckdb_conn", None)
            if create_duckdb_conn is None:
                raise RuntimeError(
                    "ResultsHandle.conn(): requires the 'viva-emitters[parquet]' "
                    "extra (duckdb not importable from viva_emitters)")
            self._conn = create_duckdb_conn()
            import polars as pl
            self._conn.register("results", pl.DataFrame(self.records()))
        return self._conn

    @property
    def sim_data(self):
        if not self._sim_data_loaded:
            ref = self.sim_data_ref
            if ref and isinstance(ref, (str, Path)) and Path(ref).suffix in (
                    ".pickle", ".pkl", ".cpickle"):
                with open(ref, "rb") as fh:
                    self._sim_data = pickle.load(fh)
            else:
                self._sim_data = ref
            self._sim_data_loaded = True
        return self._sim_data


class ResultsStep(Step):
    """The head of the post-sim Step family: reads a finished run's emitted
    results location (store path(s), and optionally a sim_data reference)
    from config or from a wired-in state input, and writes one
    ``ResultsHandle`` to the ``results`` store. Every downstream Step —
    ``AnalysisStep``, ``ReportCardStep``, etc. — reads from that single
    handle, so a composite shaped ``[emitter output] -> ResultsStep ->
    {AnalysisStep, ReportCardStep, ...}`` gives every post-sim Step the same
    view of the run, computed once (``ResultsHandle`` caches its own
    ``.records()``/``.conn()``, so fan-out doesn't re-read the store).

    Config keys (all optional): ``paths`` (list[str]), ``sim_data_ref``
    (any), ``simulation_id`` (str). A same-named, non-empty ``state`` input
    overrides the matching config key, so a composite can wire an upstream
    store (e.g. a run-finished notification carrying the real output path)
    instead of hard-coding a path in config.
    """

    # Deliberately empty (matches VisualizationStep/ReportCardStep/AnalysisStep
    # in this module): `paths`/`sim_data_ref`/`simulation_id` are read straight
    # off `self.config` without bigraph-schema validation, since the schema
    # system has no registered "opaque value" type that would let
    # `sim_data_ref` (an arbitrary python object) pass `core.fill()`.
    config_schema: dict = {}

    def inputs(self):
        return {"paths": "tree", "sim_data_ref": "tree", "simulation_id": "tree"}

    def outputs(self):
        return {"results": "tree"}

    def update(self, state, interval=None):
        cfg = dict(self.config or {})
        for key in ("paths", "sim_data_ref", "simulation_id"):
            val = state.get(key)
            if val:
                cfg[key] = val
        return {"results": ResultsHandle.from_config(cfg)}

    def invoke(self, state, interval=None):
        # Same swallow-on-error guard as VisualizationStep/ReportCardStep: a
        # run whose output isn't there yet (or a bad path) shouldn't crash
        # the step cascade — downstream Steps just see no `results`.
        try:
            update = self.update(state)
        except Exception:
            update = {}
        return SyncUpdate(update)


class AnalysisStep(Step):
    """Canonical base for post-sim analyses.

    Reads the ``ResultsHandle`` that ``ResultsStep`` wrote to the ``results``
    store and implements ``analyze(rows) -> dict`` over
    ``results.records(scale=self.scale)``. Subclasses that need direct SQL
    access instead of record dicts can pull ``results.conn()`` (lazy DuckDB)
    or ``results.sim_data`` from within ``analyze`` (the handle is available
    via the ``results`` input if a subclass overrides ``update`` to keep it).
    Subclasses set ``scale`` (one of ``ANALYSIS_SCALES``) + ``name``.

    ``Analysis`` (below) is a *separate*, genuinely distinct base for the
    live-``conn`` surface v2ecoli's ``analysis_runner.py`` drives directly —
    it only shares ``__init_subclass__``'s ``scale``/registry machinery with
    this class, not ``update()``/``inputs()``/``outputs()``. (An earlier
    revision collapsed ``Analysis`` into a thin alias of this class, which
    silently dropped the injected ``conn``/``history_sql``/``sim_data``
    kwargs and broke every v2ecoli ``Analysis`` subclass; do not repeat
    that — the two bases stay distinct.) For backward compatibility,
    ``update()`` here also accepts a plain list in the ``results`` store
    (the pre-``ResultsHandle`` contract) — used as-is, no handle required.
    """

    scale: str = "single"
    config_schema: dict = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.scale not in ANALYSIS_SCALES:
            raise ValueError(
                f"{cls.__name__}.scale={cls.scale!r} not in {sorted(ANALYSIS_SCALES)}")
        # Register concrete analyses (those declaring their own ``name``).
        if "name" in cls.__dict__:
            ANALYSIS_REGISTRY[cls.name] = cls
        if "name" in cls.__dict__:
            register_post_sim(cls, "analysis")

    def inputs(self):
        return {"results": "tree"}

    def outputs(self):
        return {"analysis": "tree"}

    def analyze(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        raise NotImplementedError

    def invoke(self, state, interval=None):
        # Analyses should fail loudly: unlike the simulation Steps (whose
        # error-swallowing invoke() keeps the step cascade alive), a broken
        # or unimplemented analyze() must surface, not silently return {}.
        return SyncUpdate(self.update(state))

    def update(self, state, interval=None):
        results = state.get("results")
        rows = results.records(self.scale) if hasattr(results, "records") else (results or [])
        return {"analysis": self.analyze(rows)}


class Analysis(AnalysisStep):
    """Live-``conn`` analysis base: reads sim output via a DuckDB connection +
    the ParCa ``sim_data``, and emits a rendered ``view`` (HTML) plus optional
    ``data`` (map). Faithful native ports of vEcoli's ``plot()`` analyses build
    on this base (cf. the record-based ``AnalysisStep`` for emitted-observable
    analyses). Subclasses set ``scale`` + ``name`` and implement ``analyze``.

    Live, non-serializable handles (``conn``, ``sim_data``) are injected by the
    runner into the state dict passed to ``update``; ``inputs()`` declares them
    for discoverability with a permissive ("any") type.

    Genuinely distinct from ``AnalysisStep`` (its parent only for the shared
    ``scale``/registry machinery via ``__init_subclass__``) — this is NOT a
    thin alias. v2ecoli's production ``analysis_runner.py::run_analyses()``
    drives ~30 concrete subclasses through exactly this surface (calling
    ``update()`` with ``conn``/``history_sql``/``sim_data``/... injected
    directly into ``state``, bypassing ``ResultsHandle`` entirely), so
    ``update()`` here must read those keys straight off ``state`` — it must
    NOT read a ``ResultsHandle`` from ``state["results"]`` the way
    ``AnalysisStep.update()`` does. (A prior collapse of this base into
    ``AnalysisStep`` silently dropped the injected conn/history_sql/sim_data
    kwargs, breaking all ~30 subclasses; restored here to the original
    pre-collapse behavior.)
    """

    def inputs(self):
        return {
            "conn": "any", "history_sql": "string",
            "config_sql": "string", "success_sql": "string",
            "sim_data": "any", "validation_data": "any",
            "variant_metadata": "any",
        }

    def outputs(self):
        return {"view": "string", "data": "map"}

    def analyze(self, *, conn, history_sql, sim_data, **ctx) -> dict:
        """Return {"view": <html str>, "data": <map>} (either key optional)."""
        raise NotImplementedError

    def invoke(self, state, interval=None):
        # Fail loudly (like AnalysisStep): a broken analyze() must surface.
        return SyncUpdate(self.update(state))

    def update(self, state, interval=None):
        kwargs = {k: state.get(k) for k in self.inputs()}
        out = self.analyze(**kwargs) or {}
        return {"view": out.get("view", ""), "data": out.get("data", {})}


class VisualizationStep(Step):
    """A post-sim visualization Step: emits a rendered ``view`` (HTML) + ``data``
    (map), like ``Analysis`` but tagged ``kind="visualization"`` so the flush can
    route it distinctly. Subclasses set ``name`` and implement
    ``render(study) -> (html, data) | None``. Inputs default to a StudyContext;
    override ``inputs()`` to consume the run extraction instead."""

    name: str = ""
    config_schema: dict = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.__dict__.get("name"):
            VISUALIZATION_REGISTRY[cls.name] = cls
            register_post_sim(cls, "visualization")

    def inputs(self):
        return {"study": "tree"}

    def outputs(self):
        return {"view": "string", "data": "tree"}

    def render(self, study) -> "tuple[str, dict] | None":
        raise NotImplementedError

    def update(self, state, interval=None):
        study = state.get("study")
        res = self.render(study)
        if not res:
            return {"view": "", "data": {}}
        view, data = res
        return {"view": view, "data": data}

    def invoke(self, state, interval=None):
        # v2ecoli's V2Step guard, carried verbatim: catch errors from missing
        # data so one broken visualization doesn't crash the step cascade.
        try:
            update = self.update(state)
        except Exception:
            update = {}
        return SyncUpdate(update)


class TestStep(Step):
    """A test (formerly "report card") as a visualization-like Step (sibling of
    ``Analysis`` and ``VisualizationStep``): emits ``view`` (HTML) + ``data``
    (verdict map). Unlike ``Analysis`` — which consumes a live DuckDB sim-output
    connection — a test's input is a ``StudyContext`` (the study's spec + dir),
    so tests grade run-free. Subclasses set ``name`` and implement
    ``applies(study)`` + ``build(study) -> (verdict_dict, html) | None``. A named
    subclass auto-registers in ``TEST_REGISTRY`` and, kind-tagged, in
    ``POST_SIM_REGISTRY``.

    Gating convention: a test's verdict dict is free-form (existing tests are
    not required to change), but a test intended to gate a study's Evaluate
    stage should shape its verdict as ``{"status": "pass"|"fail"|"warn",
    "checks": [...], "summary": <str>}`` so a generic gate can read
    ``data["status"]`` without knowing the test. ``checks`` is a list of
    per-check detail dicts (test-defined shape); ``summary`` is a short
    human-readable readout of the verdict.
    """

    name: str = ""
    config_schema: dict = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.__dict__.get("name"):
            TEST_REGISTRY[cls.name] = cls
            register_post_sim(cls, "test")

    def inputs(self):
        return {"study": "tree"}

    def outputs(self):
        return {"view": "string", "data": "tree"}

    def applies(self, study) -> bool:
        return True

    def build(self, study) -> "tuple[dict, str] | None":
        """Return ``(verdict_json_dict, html_str)`` or None. Subclasses override."""
        raise NotImplementedError

    def update(self, state, interval=None):
        study = state.get("study")
        res = self.build(study) if study is not None else None
        if not res:
            return {"view": "", "data": {}}
        verdict, html = res
        return {"view": html, "data": verdict}

    def invoke(self, state, interval=None):
        # v2ecoli's V2Step guard, carried verbatim: catch errors from missing
        # data so one broken card doesn't crash the step cascade.
        try:
            update = self.update(state)
        except Exception:
            update = {}
        return SyncUpdate(update)


# Back-compat alias: same class object, old name.
ReportCardStep = TestStep


def _sanitize(obj: Any) -> Any:
    """Replace non-finite floats with None, recursively (bundle JSON.parse safe)."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


@dataclass
class StudyContext:
    study_name: str
    study_dir: Path
    spec: dict
    ws_root: Path

    @classmethod
    def load(cls, ws_root: Path, study_name: str) -> "StudyContext":
        sd = ws_root / "workspace" / "studies" / study_name
        spec_path = sd / "study.yaml"
        spec = {}
        if spec_path.is_file():
            spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
        return cls(study_name=study_name, study_dir=sd, spec=spec, ws_root=ws_root)

    def run_zarr_paths(self) -> list[Path]:
        return sorted(self.study_dir.glob("runs.*.zarr"))

    @property
    def card_dir(self) -> Path:
        return self.study_dir / "viz" / "report_card"


def write_test(ctx: StudyContext, name: str, verdict: dict, html: str) -> Path:
    """Write <test>.html + <test>.verdict.json into the study's report_card dir.
    Returns the html path. Verdict is sanitized + written with allow_nan=False."""
    d = ctx.card_dir
    d.mkdir(parents=True, exist_ok=True)
    html_path = d / f"{name}.html"
    html_path.write_text(html, encoding="utf-8")
    (d / f"{name}.verdict.json").write_text(
        json.dumps(_sanitize(verdict), indent=1, allow_nan=False) + "\n",
        encoding="utf-8")
    return html_path


def write_card(ctx: StudyContext, name: str, verdict: dict, html: str) -> Path:
    """Deprecated alias for ``write_test``."""
    warnings.warn("write_card is renamed to write_test", DeprecationWarning, stacklevel=2)
    return write_test(ctx, name, verdict, html)


def prune(ctx: StudyContext, keep: set[str]) -> list[str]:
    """Delete <card>.html (+ sibling .verdict.json) under the study's report_card
    dir whose stem is not in `keep`. Returns pruned stems. Touches only that dir."""
    d = ctx.card_dir
    pruned: list[str] = []
    if not d.is_dir():
        return pruned
    for html in sorted(d.glob("*.html")):
        stem = html.name[: -len(".html")]
        if stem not in keep:
            html.unlink()
            vf = html.with_name(stem + ".verdict.json")
            if vf.is_file():
                vf.unlink()
            pruned.append(stem)
    return pruned


def applicable(ctx: StudyContext, core, only: "str | None" = None) -> list:
    """Instantiated test Steps to emit for a study. If the study spec lists
    `report_cards:`, only those names are eligible; otherwise every registered test
    is eligible. A test is emitted when eligible AND its applies(ctx) is True.
    `only` (a name, or None/'all') narrows to a single test. `core` is a
    bigraph-schema core (built once by the caller) used to instantiate Steps."""
    declared = ctx.spec.get("report_cards")
    want = None if (only in (None, "all")) else {only}
    out = []
    for nm, cls in TEST_REGISTRY.items():
        if want is not None and nm not in want:
            continue
        if declared is not None and nm not in declared:
            continue
        try:
            step = cls({}, core=core)
            if step.applies(ctx):
                out.append(step)
        except Exception:  # noqa: BLE001 — one broken card never aborts selection
            continue
    return out


# ---------------------------------------------------------------------------
# TestReportStep: the Evaluate-tail Step that aggregates every TestStep's
# verdict into one run report (report.json) and diffs it against the
# previous run (diff.json), with a bounded on-disk history/ of prior reports.
# ---------------------------------------------------------------------------
from discovery_engine.test_vocab import worst as _worst  # noqa: E402
from discovery_engine.test_diff import diff_reports as _diff_reports  # noqa: E402
from discovery_engine.test_contract import sanitize as _tc_sanitize  # noqa: E402
from discovery_engine.study_verdict import severity_gate as _severity_gate  # noqa: E402

HISTORY_KEEP = 10


def tests_dir(ctx: StudyContext) -> Path:
    return ctx.study_dir / "viz" / "tests"


def history_dir(ctx: StudyContext) -> Path:
    return tests_dir(ctx) / "history"


def build_report(study_name: str, run_id, cards: dict) -> dict:
    """Aggregate a ``{name: verdict_doc}`` map into a ``test_report/v1`` doc.
    ``overall`` is the worst verdict over every card's ``overall``. ``counts``
    tallies cards, total axes, and per-verdict axis counts (plus
    ``hard_mismatch``, axes verdict ``mismatch`` with ``severity != "soft"``)."""
    counts = {"cards": len(cards), "axes": 0, "within_tol": 0, "drift": 0,
              "mismatch": 0, "ungraded": 0, "hard_mismatch": 0}
    overalls = []
    for doc in cards.values():
        overalls.append(doc.get("overall", "ungraded"))
        for grp in (doc.get("groups") or {}).values():
            for ax in grp.get("axes") or []:
                counts["axes"] += 1
                v = ax.get("verdict", "ungraded")
                if v in counts:
                    counts[v] += 1
                if v == "mismatch" and ax.get("severity", "hard") == "hard":
                    counts["hard_mismatch"] += 1
    return _tc_sanitize({
        "schema": "test_report/v1", "study": study_name, "run_id": run_id,
        "overall": _worst(overalls), "counts": counts, "cards": cards,
    })


def write_report(ctx: StudyContext, report: dict) -> Path:
    """Write ``tests_dir(ctx)/report.json`` (sanitized, allow_nan=False).
    Returns the path."""
    d = tests_dir(ctx)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "report.json"
    p.write_text(json.dumps(_tc_sanitize(report), indent=1, allow_nan=False) + "\n",
                 encoding="utf-8")
    return p


def _latest_history(ctx: StudyContext):
    hd = history_dir(ctx)
    if not hd.is_dir():
        return None
    files = sorted(hd.glob("*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        return None


def _rotate_history(ctx: StudyContext, report: dict, run_id) -> None:
    hd = history_dir(ctx)
    hd.mkdir(parents=True, exist_ok=True)
    name = f"{run_id}.json" if run_id else f"{len(list(hd.glob('*.json'))):06d}.json"
    (hd / name).write_text(json.dumps(_tc_sanitize(report), allow_nan=False), encoding="utf-8")
    files = sorted(hd.glob("*.json"))
    for old in files[:-HISTORY_KEEP]:
        old.unlink()


class TestReportStep(Step):
    """Evaluate-tail Step: aggregate the run's TestStep verdicts into
    report.json and diff against the previous run (diff.json). Emits
    ``{report, diff, gate}``. ``run_id`` is never generated internally — it
    arrives via config/state (workflow-engine ``Date.now()``-free rule)."""

    config_schema: dict = {}

    def _ctx(self, state) -> StudyContext:
        if self.config.get("ws_root") and self.config.get("study_name"):
            return StudyContext.load(Path(self.config["ws_root"]), self.config["study_name"])
        return state.get("study")

    def inputs(self):
        return {"cards": "tree", "run_id": "tree"}

    def outputs(self):
        return {"report": "tree", "diff": "tree", "gate": "string"}

    def update(self, state, interval=None):
        ctx = self._ctx(state)
        cards = state.get("cards") or self.config.get("cards") or {}
        run_id = state.get("run_id") or self.config.get("run_id")
        report = build_report(getattr(ctx, "study_name", ""), run_id, cards)
        # Severity-aware gate: only hard-severity axis mismatches fail; soft/drift
        # warn; directional never gates. Injected into report.json so consumers
        # read the gate + its actionable ``gated_by`` list.
        report["gate"] = _severity_gate(report)
        write_report(ctx, report)
        prev = _latest_history(ctx)
        prev_cards = (prev or {}).get("cards") or {}
        diff = _diff_reports(prev_cards, cards)
        (tests_dir(ctx) / "diff.json").write_text(
            json.dumps(_tc_sanitize(diff), indent=1, allow_nan=False) + "\n", encoding="utf-8")
        _rotate_history(ctx, report, run_id)
        return {"report": report, "diff": diff, "gate": report["gate"]["status"]}

    def invoke(self, state, interval=None):
        # Same swallow-on-error guard as the other post-sim bases: a broken
        # report never crashes the step cascade.
        try:
            update = self.update(state)
        except Exception:
            update = {}
        return SyncUpdate(update)
