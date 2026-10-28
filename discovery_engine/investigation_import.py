"""Select which investigations an importing workspace carries from an upstream repo.

A workspace that imports another repo as a dependency (e.g. sms-ecoli importing
v2ecoli) often carries a **subset** of that repo's investigations under its own
investigations dir — seeded from upstream, then hand-maintained. This module makes
that selection explicit and enforceable, so a new upstream investigation does not
silently flow in.

Selection lives in ``workspace.yaml``:

  imported_investigations:   allowlist of upstream investigation slugs to carry
  native_investigations:     investigations authored in THIS workspace

Two accepted shapes for ``imported_investigations``:

    # minimal — guard only (no sync source):
    imported_investigations:
      - colonies
      - metabolism-overflow

    # full — guard + additive sync:
    imported_investigations:
      from: v2ecoli                                 # uv.lock package whose pinned rev is the source
      git: https://github.com/vivarium-collective/v2ecoli.git
      subtree: workspace/investigations             # where investigations live upstream (default)
      allow:
        - colonies
        - metabolism-overflow

    native_investigations:
      - cd1-review-comparison

Two operations:

  * ``check(ws_root)`` — conformance guard. Every investigation present on disk must
    be declared (``imported ∪ native``) and the two lists must be disjoint. A stray
    upstream investigation (one deliberately left off the allowlist) fails here.
    ``check`` ALSO verifies **completeness** of each imported investigation: every
    member study it declares must be present on disk, so a partial sync (the
    investigation.yaml copied but its member studies dropped) fails here instead of
    surfacing as a broken import later. Wire it into CI via ``assert_selection_ok``
    (a two-line pytest) or the ``check`` CLI.
  * ``sync(ws_root)`` — additive import. Copy any allowlisted investigation that is
    **missing** locally from the **pinned** upstream rev, never overwriting a local
    (divergent) copy. Only allowlisted slugs are ever pulled; the source is the exact
    commit pinned in ``uv.lock`` (not upstream ``main``), so the import is reproducible.

Importing an investigation brings its **complete dependencies**, not just its
``investigation.yaml``:

  1. **Member studies.** After (re)materializing an investigation, ``sync`` resolves
     its member studies from ``investigation.yaml`` (``members:``, plus the studies
     named in ``acceptance_criteria`` / ``at_a_glance``) and copies each missing one
     from the SAME pinned rev — whether the study lives nested under
     ``<investigations>/<inv>/studies/<slug>/`` or flat under ``<studies>/<slug>/``
     upstream. The studies dir is resolved through the workspace ``layout:`` map, never
     hardcoded. Additive: a study already present locally is left untouched. This is
     the fix for the real bug where a flat member study (``ketchup-exchange-comparison``)
     was silently dropped because ``sync`` only walked the ``investigations`` subtree.

  2. **Package dependencies.** ``sync`` scans the copied member studies' ``study.yaml``
     composite/process addresses (e.g.
     ``viva_ketchup.composites.estimation.ketchup_baseline``), maps each to its
     top-level package, and checks that package is satisfied in the importing
     ``workspace.yaml`` (its ``imports:`` map and/or ``dashboard.registry.include``).
     Mapping is **alias-aware**: ``pbg_<x>`` and ``viva_<x>`` are the same dependency
     (normalized on the stem after the prefix), so an ``imports: viva_ketchup`` already
     satisfies a study that addresses ``pbg_ketchup`` — and vice versa; the preferred
     spelling reported is the ``viva_`` one. The source package itself (``from:``) and
     the importing workspace's own packages are never reported as missing.

     DESIGN DECISION — **report by default, apply on opt-in (option b).** Auto-editing
     a real, comment-rich ``workspace.yaml`` on every ``sync`` is surprising and risks
     clobbering hand-written comments, so ``sync``/``check`` DETECT and clearly REPORT
     a missing package dependency ("investigation X needs package Y — add it to
     imports") by default and change nothing. Passing ``sync --add-deps`` (alias
     ``--fix``) applies them: for each missing package it copies the **canonical import
     block** from the source workspace's own ``imports:`` at the pinned rev (mirroring
     how upstream declares ``source:``/``ref:``/``description:``) into the downstream
     ``imports:`` and adds the package to ``dashboard.registry.include``, preserving
     existing comments (ruamel round-trip). A study that references a package the
     SOURCE workspace does not itself declare is reported as *unresolved* and never
     fabricated.

Excluding a future upstream investigation is then just: leave it off the allowlist;
``check`` keeps it out for good.
"""
from __future__ import annotations

import argparse
import copy
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .paths import find_workspace_root

DEFAULT_SUBTREE = "workspace/investigations"


# ---------------------------------------------------------------------------
# Selection model
# ---------------------------------------------------------------------------


@dataclass
class Source:
    """Where allowlisted investigations are imported from (for ``sync``)."""

    package: str | None = None  # uv.lock package name whose pinned rev is the source
    git: str | None = None      # git URL (used to fetch/clone when no local checkout)
    subtree: str = DEFAULT_SUBTREE  # dir under the upstream repo holding investigations

    @property
    def rev_token(self) -> str | None:
        """The token to anchor the uv.lock rev search on (package or git basename)."""
        if self.package:
            return self.package
        if self.git:
            return self.git.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        return None


@dataclass
class Selection:
    allow: list[str] = field(default_factory=list)   # imported allowlist
    native: list[str] = field(default_factory=list)  # authored-here
    source: Source | None = None

    @property
    def declared(self) -> set[str]:
        return set(self.allow) | set(self.native)


@dataclass
class Problem:
    severity: str  # "error" | "warning"
    message: str


@dataclass
class DepFinding:
    """A package an imported investigation's studies depend on."""

    package: str            # preferred (viva_) spelling for humans
    stem: str               # alias-normalized stem (pbg_/viva_ stripped)
    referenced_by: list[str]  # study slugs that address it
    satisfied: bool         # already declared in the importing workspace.yaml


@dataclass
class SyncResult:
    rev: str
    allow: list[str]
    already_local: list[str]
    added: list[str]
    missing_upstream: list[str]
    dry_run: bool
    # New: dependency propagation.
    studies_added: dict[str, list[str]] = field(default_factory=dict)   # inv -> [study slugs]
    studies_missing_upstream: dict[str, list[str]] = field(default_factory=dict)
    dep_findings: list[DepFinding] = field(default_factory=list)
    deps_added: list[str] = field(default_factory=list)                 # package keys written
    deps_unresolved: list[str] = field(default_factory=list)            # not declared in source


# ---------------------------------------------------------------------------
# Reading the selection
# ---------------------------------------------------------------------------


def _load_ws_yaml(ws_root: Path) -> dict:
    """Parse ``<ws_root>/workspace.yaml`` (empty dict if absent/empty)."""
    wf = ws_root / "workspace.yaml"
    if not wf.is_file():
        return {}
    return yaml.safe_load(wf.read_text(encoding="utf-8")) or {}


def load_selection(ws_root: Path) -> Selection:
    """Parse ``imported_investigations`` / ``native_investigations`` from workspace.yaml."""
    data = _load_ws_yaml(ws_root)
    imported = data.get("imported_investigations")
    native = list(data.get("native_investigations") or [])

    allow: list[str] = []
    source: Source | None = None
    if isinstance(imported, list):
        allow = [str(s) for s in imported]
    elif isinstance(imported, dict):
        allow = [str(s) for s in (imported.get("allow") or [])]
        source = Source(
            package=imported.get("from"),
            git=imported.get("git"),
            subtree=str(imported.get("subtree") or DEFAULT_SUBTREE),
        )
    elif imported is not None:
        raise ValueError(
            "workspace.yaml: `imported_investigations` must be a list of slugs or a "
            "mapping with an `allow:` list."
        )
    return Selection(allow=allow, native=[str(s) for s in native], source=source)


def investigations_dir(ws_root: Path) -> Path:
    """The workspace's investigations directory, honoring the ``layout:`` override.

    Kept dependency-light on purpose (reads ``layout.investigations`` from
    workspace.yaml directly, defaulting to the flat ``investigations/``), so the
    guard runs in an importing repo's CI without the full workspace_paths stack.
    """
    data = _load_ws_yaml(ws_root)
    rel = ((data.get("layout") or {}).get("investigations")) or "investigations"
    return ws_root / rel


def studies_dir(ws_root: Path) -> Path:
    """The workspace's (flat) studies directory, honoring the ``layout:`` override.

    Same dependency-light contract as :func:`investigations_dir` (reads
    ``layout.studies`` directly, default flat ``studies/``). Studies may also live
    NESTED under ``<investigations>/<inv>/studies/``; this returns only the flat root
    (the canonical write location for a study whose upstream copy is flat).
    """
    data = _load_ws_yaml(ws_root)
    rel = ((data.get("layout") or {}).get("studies")) or "studies"
    return ws_root / rel


def present_slugs(ws_root: Path) -> set[str]:
    """Investigation slugs present on disk (layout-aware investigations dir)."""
    inv = investigations_dir(ws_root)
    if not inv.is_dir():
        return set()
    return {p.name for p in inv.iterdir() if (p / "investigation.yaml").is_file()}


# ---------------------------------------------------------------------------
# Member studies of an investigation
# ---------------------------------------------------------------------------


def investigation_members(inv_yaml: Path) -> list[str]:
    """Member study slugs declared by an ``investigation.yaml`` (dedup, order-preserved).

    Draws from every place the canonical investigation shape names a member study:
      * ``members:``               — the authoritative membership list;
      * ``acceptance_criteria[].study`` — gated studies (robustness fallback);
      * ``at_a_glance.studies[]``  — the summary list (single-key dicts or strings).
    Unioning them means a member is recovered even from an older investigation.yaml
    that predates the ``members:`` list.
    """
    if not inv_yaml.is_file():
        return []
    data = yaml.safe_load(inv_yaml.read_text(encoding="utf-8")) or {}
    out: list[str] = []

    for m in (data.get("members") or []):
        if isinstance(m, str):
            out.append(m)
        elif isinstance(m, dict):  # tolerate {slug: ...} shape
            out.extend(str(k) for k in m.keys())

    for ac in (data.get("acceptance_criteria") or []):
        if isinstance(ac, dict) and ac.get("study"):
            out.append(str(ac["study"]))

    # at_a_glance is EITHER a mapping with a `studies:` list, OR a bare list of
    # per-study rows (each `{study: <slug>, role: ...}` — the cd2 shape). Handle
    # both, and for a `{study: ...}` row take the value, not the keys.
    ag_raw = data.get("at_a_glance")
    if isinstance(ag_raw, dict):
        ag = ag_raw.get("studies") or []
    elif isinstance(ag_raw, list):
        ag = ag_raw
    else:
        ag = []
    for item in ag:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            if item.get("study"):
                out.append(str(item["study"]))
            else:  # single-key `{slug: ...}` shape
                out.extend(str(k) for k in item.keys())

    return list(dict.fromkeys(str(s) for s in out))


def _local_study_dir(ws_root: Path, slug: str) -> Path | None:
    """Return an existing local study dir for ``slug`` (flat OR nested), else None."""
    flat = studies_dir(ws_root) / slug
    if (flat / "study.yaml").is_file():
        return flat
    inv_root = investigations_dir(ws_root)
    if inv_root.is_dir():
        for inv in inv_root.iterdir():
            cand = inv / "studies" / slug
            if (cand / "study.yaml").is_file():
                return cand
    return None


# ---------------------------------------------------------------------------
# Package-dependency detection (alias-aware)
# ---------------------------------------------------------------------------

# Registry-only utility packages a study may reference in an address but which are
# never a *workspace* import to propagate (the framework core / substrate).
_FRAMEWORK_STEMS = {"core", "process_bigraph", "bigraph_schema"}


def _pkg_stem(name: str) -> str:
    """Alias-normalized stem: lowercase, dashes→underscores, ``pbg_``/``viva_`` stripped.

    ``pbg_ketchup``, ``viva_ketchup`` and ``viva-ketchup`` all collapse to ``ketchup``,
    so they count as one dependency regardless of spelling.
    """
    n = name.strip().lower().replace("-", "_")
    for pre in ("pbg_", "viva_"):
        if n.startswith(pre):
            return n[len(pre):]
    return n


def _preferred_spelling(name: str) -> str:
    """Human-facing package name: prefer ``viva_<stem>`` for aliasable pbg_/viva_ packages."""
    n = name.strip().replace("-", "_")
    low = n.lower()
    if low.startswith("pbg_") or low.startswith("viva_"):
        return "viva_" + _pkg_stem(n)
    return n


def _composite_addresses(study_data: object) -> set[str]:
    """Every dotted ``composite:`` address anywhere in a parsed study.yaml.

    Walks the whole document so it catches ``conditions.baseline.composite``,
    ``conditions.variants[].composite`` and ``runs[].composite`` alike.
    """
    found: set[str] = set()

    def walk(obj: object) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "composite" and isinstance(v, str) and "." in v:
                    found.add(v)
                else:
                    walk(v)
        elif isinstance(obj, list):
            for it in obj:
                walk(it)

    walk(study_data)
    return found


def _top_package(address: str) -> str:
    """Top-level package of a dotted address (``a.b.c`` -> ``a``)."""
    return address.split(".", 1)[0]


def _declared_dep_stems(ws_data: dict) -> set[str]:
    """Alias-normalized stems already declared in the importing workspace.yaml.

    Union of the ``imports:`` map keys and ``dashboard.registry.include`` — either
    channel counts as "the package is available here".
    """
    stems: set[str] = set()
    imports = ws_data.get("imports") or {}
    if isinstance(imports, dict):
        stems.update(_pkg_stem(str(k)) for k in imports)
    reg = ((ws_data.get("dashboard") or {}).get("registry") or {}).get("include") or []
    stems.update(_pkg_stem(str(k)) for k in reg)
    return stems


def _own_package_stems(ws_data: dict) -> set[str]:
    """Stems of the importing workspace's OWN packages (never a missing import)."""
    stems: set[str] = set()
    pp = ws_data.get("package_path")
    if pp:
        stems.add(_pkg_stem(Path(str(pp)).name))
    if ws_data.get("name"):
        stems.add(_pkg_stem(str(ws_data["name"])))
    for p in (ws_data.get("workspace_packages") or []):
        stems.add(_pkg_stem(str(p)))
    return stems


def detect_missing_deps(ws_root: Path, inv_slugs: list[str]) -> list[DepFinding]:
    """Package dependencies of the given imported investigations' local member studies.

    Fully offline: reads only local ``investigation.yaml`` + ``study.yaml`` and the
    importing ``workspace.yaml``. Excludes the source package (``from:``) and the
    workspace's own packages. Returns one :class:`DepFinding` per referenced package
    (satisfied or not), so callers can report the whole picture.
    """
    ws_data = _load_ws_yaml(ws_root)
    declared = _declared_dep_stems(ws_data)
    excluded = _own_package_stems(ws_data) | set(_FRAMEWORK_STEMS)
    sel = load_selection(ws_root)
    if sel.source and sel.source.rev_token:
        excluded.add(_pkg_stem(sel.source.rev_token))

    refs: dict[str, set[str]] = {}   # stem -> study slugs
    pref: dict[str, str] = {}        # stem -> preferred spelling
    for inv in inv_slugs:
        iy = investigations_dir(ws_root) / inv / "investigation.yaml"
        for slug in investigation_members(iy):
            sd = _local_study_dir(ws_root, slug)
            if sd is None:
                continue
            data = yaml.safe_load((sd / "study.yaml").read_text(encoding="utf-8")) or {}
            for addr in _composite_addresses(data):
                stem = _pkg_stem(_top_package(addr))
                refs.setdefault(stem, set()).add(slug)
                pref.setdefault(stem, _preferred_spelling(_top_package(addr)))

    findings: list[DepFinding] = []
    for stem, studies in sorted(refs.items()):
        if stem in excluded:
            continue
        findings.append(
            DepFinding(
                package=pref[stem],
                stem=stem,
                referenced_by=sorted(studies),
                satisfied=stem in declared,
            )
        )
    return findings


# ---------------------------------------------------------------------------
# check — the conformance guard
# ---------------------------------------------------------------------------


def _resolve_upstream_for_check(
    ws_root: Path, sel: "Selection", upstream_src: Path | None = None, rev: str | None = None
) -> "tuple[Path, str, str, str] | None":
    """Best-effort ``(src, rev, subtree, studies_subtree)`` for check's completeness probe.

    Returns ``None`` when there is no sync source, or the pinned rev / source can't be
    resolved (e.g. offline). Callers then degrade a missing-member finding to an
    "unverified" warning instead of a hard error, so an offline ``check`` never
    false-fails while an online ``check``/``sync`` still catches a real partial sync.
    ``upstream_src``/``rev`` override the resolved source/rev (tests, local checkouts).
    """
    if sel.source is None:
        return None
    try:
        token = sel.source.rev_token
        rev = rev or (pinned_rev(ws_root, token) if token else None)
        if not rev:
            return None
        src = _ensure_source(rev, git=sel.source.git, src=upstream_src, cache_key=token or "upstream")
        subtree = sel.source.subtree
        return (src, rev, subtree, _source_studies_subtree(src, rev, subtree))
    except Exception:  # noqa: BLE001 — best-effort; offline/no-source degrades to a warning
        return None


def check(ws_root: Path, *, upstream_src: Path | None = None, rev: str | None = None) -> list[Problem]:
    """Return conformance problems for the workspace's investigation selection.

    Empty list == conformant. Problems:
      * (error) an investigation present on disk that is in neither list (undeclared);
      * (error) a slug in both `imported_investigations` and `native_investigations`;
      * (error) a member study of an imported investigation that is missing on disk AND
        exists upstream at the pinned rev — the incompleteness a partial sync leaves
        behind (`sync` fixes it);
      * (warning) a declared member study that does NOT exist upstream — over-declared /
        design-spec, not a sync gap; or one that couldn't be verified (offline / no
        sync source);
      * (warning) a package an imported investigation's studies depend on that is not
        declared in this workspace's `imports:`/`registry.include`.

    Warnings never block ``assert_selection_ok`` (errors only); the CLI prints them.
    """
    sel = load_selection(ws_root)
    problems: list[Problem] = []

    overlap = sorted(set(sel.allow) & set(sel.native))
    if overlap:
        problems.append(
            Problem(
                "error",
                f"investigation(s) declared BOTH imported and native: {overlap} — each "
                "is either imported from upstream or authored here, not both.",
            )
        )

    present = present_slugs(ws_root)
    undeclared = sorted(present - sel.declared)
    if undeclared:
        problems.append(
            Problem(
                "error",
                f"undeclared investigation(s) present under {DEFAULT_SUBTREE}/: "
                f"{undeclared}. Add each to `imported_investigations` (if pulled from "
                "upstream) or `native_investigations` (if authored here) in "
                "workspace.yaml — or remove it. This guard is what keeps a "
                "deliberately-excluded upstream investigation from silently appearing.",
            )
        )

    # Completeness: a member study of a PRESENT imported investigation that is missing
    # on disk is an ERROR only when it EXISTS upstream at the pinned rev (a real partial
    # sync that `sync` can fix). A member that does NOT exist upstream is over-declared /
    # aspirational (a design-spec study never built upstream) — a WARNING, not a hard
    # failure, so it doesn't break CI. If upstream can't be reached, we can't tell, so we
    # warn ("unverified") rather than hard-fail.
    imported_present = sorted(present & set(sel.allow))
    up = _resolve_upstream_for_check(ws_root, sel, upstream_src=upstream_src, rev=rev)
    for inv in imported_present:
        iy = investigations_dir(ws_root) / inv / "investigation.yaml"
        missing = [s for s in investigation_members(iy) if _local_study_dir(ws_root, s) is None]
        if not missing:
            continue
        if up is None:
            problems.append(
                Problem(
                    "warning",
                    f"imported investigation '{inv}' declares member study(ies) {missing} "
                    "not present on disk; could not verify against upstream (no reachable "
                    "sync source). Run `sync` where the pinned rev is reachable to "
                    "materialize any that exist upstream.",
                )
            )
            continue
        src, rev, subtree, studies_subtree = up
        exists_up = [
            s for s in missing
            if _tree_has(src, rev, f"{subtree}/{inv}/studies/{s}")
            or _tree_has(src, rev, f"{studies_subtree}/{s}")
        ]
        aspirational = [s for s in missing if s not in exists_up]
        if exists_up:
            problems.append(
                Problem(
                    "error",
                    f"imported investigation '{inv}' is INCOMPLETE: member study(ies) "
                    f"{exists_up} exist upstream@{rev[:12]} but are missing on disk "
                    "(partial sync). Run `python -m discovery_engine.investigation_import "
                    "sync` to materialize them.",
                )
            )
        if aspirational:
            problems.append(
                Problem(
                    "warning",
                    f"imported investigation '{inv}' declares member study(ies) "
                    f"{aspirational} that do not exist upstream@{rev[:12]} — over-declared "
                    "/ design-spec, not a sync gap. Consider trimming the investigation's "
                    "membership upstream.",
                )
            )

    # Advisory: package deps not satisfied here.
    for dep in detect_missing_deps(ws_root, imported_present):
        if not dep.satisfied:
            problems.append(
                Problem(
                    "warning",
                    f"imported investigation study(ies) {dep.referenced_by} depend on "
                    f"package '{dep.package}' but it is not declared in this workspace's "
                    "`imports:`/`dashboard.registry.include`. Add it (or run "
                    "`sync --add-deps` to mirror the source's import block).",
                )
            )

    return problems


def assert_selection_ok(
    ws_root: Path | str, *, upstream_src: Path | None = None, rev: str | None = None
) -> None:
    """Raise AssertionError if the workspace's investigation selection is non-conformant.

    Only *error*-severity problems raise; advisory dep warnings do not. Intended for a
    two-line guard test in an importing workspace::

        from discovery_engine.investigation_import import assert_selection_ok
        def test_investigation_selection():
            assert_selection_ok(WORKSPACE_ROOT)
    """
    errors = [
        p.message
        for p in check(Path(ws_root), upstream_src=upstream_src, rev=rev)
        if p.severity == "error"
    ]
    if errors:
        raise AssertionError("\n".join(errors))


# ---------------------------------------------------------------------------
# sync — additive import from the pinned upstream rev
# ---------------------------------------------------------------------------


def _git(*args: str, cwd: Path | None = None) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{r.stderr.strip()}")
    return r.stdout


def pinned_rev(ws_root: Path, token: str) -> str | None:
    """Extract the pinned commit for ``token`` from uv.lock.

    Matches ``…<token>.git?...#<40-hex>`` — the git-source form uv writes for both the
    inline and the ``[[package]] source = { git = … }`` layouts.
    """
    lock = ws_root / "uv.lock"
    if not lock.is_file():
        return None
    m = re.search(
        re.escape(token) + r"\.git[^#\"]*#([0-9a-f]{40})",
        lock.read_text(encoding="utf-8"),
    )
    return m.group(1) if m else None


def _ensure_source(rev: str, *, git: str | None, src: Path | None, cache_key: str) -> Path:
    """Return a git checkout that has ``rev`` available (for ``git archive``)."""
    if src is not None:
        src = src.expanduser().resolve()
        if not (src / ".git").exists():
            raise RuntimeError(f"--upstream-src {src} is not a git checkout")
        try:
            _git("cat-file", "-e", f"{rev}^{{commit}}", cwd=src)
        except RuntimeError:
            _git("fetch", "origin", rev, cwd=src)
        return src
    if not git:
        raise RuntimeError(
            "no upstream git URL to clone from — declare `imported_investigations.git` "
            "in workspace.yaml or pass --upstream-src PATH."
        )
    cache = Path.home() / ".cache" / "discovery-engine" / "investigation-import" / cache_key
    if not (cache / ".git").exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--filter=blob:none", git, str(cache))
    try:
        _git("cat-file", "-e", f"{rev}^{{commit}}", cwd=cache)
    except RuntimeError:
        _git("fetch", "origin", rev, cwd=cache)
    return cache


def _upstream_slugs(src: Path, rev: str, subtree: str) -> set[str]:
    out = _git("ls-tree", "-d", "--name-only", rev, f"{subtree}/", cwd=src)
    return {Path(line).name for line in out.splitlines() if line.strip()}


def _tree_has(src: Path, rev: str, tree_path: str) -> bool:
    """True if ``<rev>:<tree_path>`` exists (a tree or blob) in the upstream repo."""
    r = subprocess.run(
        ["git", "cat-file", "-e", f"{rev}:{tree_path}"],
        cwd=src, capture_output=True,
    )
    return r.returncode == 0


def _extract_subtree(src: Path, rev: str, tree_path: str, dest: Path) -> None:
    """Extract ``<tree_path>`` at ``rev`` into ``dest`` (which must not yet exist)."""
    with tempfile.TemporaryDirectory() as td:
        tar_path = Path(td) / "sub.tar"
        with open(tar_path, "wb") as fh:
            subprocess.run(
                ["git", "archive", rev, tree_path],
                cwd=src, check=True, stdout=fh,
            )
        with tarfile.open(tar_path) as tf:
            # `filter="data"` (Python 3.12+) is the safe extraction filter and the
            # future default; fall back for older interpreters.
            if hasattr(tarfile, "data_filter"):
                tf.extractall(td, filter="data")
            else:  # pragma: no cover - older Python
                tf.extractall(td)  # yields <td>/<tree_path>/...
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(Path(td) / tree_path, dest)


def _copy_investigation(src: Path, rev: str, slug: str, subtree: str, dest_dir: Path) -> None:
    """Extract ``<subtree>/<slug>`` at ``rev`` into ``dest_dir/<slug>``."""
    _extract_subtree(src, rev, f"{subtree}/{slug}", dest_dir / slug)


def _source_workspace_yaml(src: Path, rev: str) -> dict:
    """Parse the SOURCE repo's ``workspace.yaml`` at ``rev`` (empty dict if absent)."""
    r = subprocess.run(
        ["git", "show", f"{rev}:workspace.yaml"],
        cwd=src, capture_output=True, text=True,
    )
    if r.returncode != 0:
        return {}
    return yaml.safe_load(r.stdout) or {}


def _source_studies_subtree(src: Path, rev: str, inv_subtree: str) -> str:
    """Where flat studies live in the SOURCE repo at ``rev`` (layout-aware, with fallback).

    Reads the source ``layout.studies``; if absent, derives the sibling ``studies``
    dir of the investigations subtree (``workspace/investigations`` -> ``workspace/studies``).
    """
    layout = (_source_workspace_yaml(src, rev).get("layout") or {})
    studies = layout.get("studies")
    if studies:
        return str(studies)
    if inv_subtree.endswith("investigations"):
        return inv_subtree[: -len("investigations")] + "studies"
    return "studies"


def _copy_member_studies(
    src: Path,
    rev: str,
    inv: str,
    ws_root: Path,
    inv_subtree: str,
    studies_subtree: str,
) -> tuple[list[str], list[str]]:
    """Copy an investigation's missing member studies from ``rev``.

    Returns ``(added, missing_upstream)``. A member already present locally (flat or
    nested) is skipped (additive). A member found NEITHER nested nor flat upstream is
    reported as missing_upstream, not copied.
    """
    iy = investigations_dir(ws_root) / inv / "investigation.yaml"
    added: list[str] = []
    missing_upstream: list[str] = []
    for slug in investigation_members(iy):
        if _local_study_dir(ws_root, slug) is not None:
            continue
        nested_tree = f"{inv_subtree}/{inv}/studies/{slug}"
        flat_tree = f"{studies_subtree}/{slug}"
        if _tree_has(src, rev, nested_tree):
            dest = investigations_dir(ws_root) / inv / "studies" / slug
            _extract_subtree(src, rev, nested_tree, dest)
            added.append(slug)
        elif _tree_has(src, rev, flat_tree):
            dest = studies_dir(ws_root) / slug
            _extract_subtree(src, rev, flat_tree, dest)
            added.append(slug)
        else:
            missing_upstream.append(slug)
    return added, missing_upstream


def _apply_deps(ws_root: Path, missing: list[DepFinding], src: Path, rev: str) -> tuple[list[str], list[str]]:
    """Additively write missing package deps into workspace.yaml (comment-preserving).

    For each missing package, mirror the SOURCE workspace's canonical ``imports:`` block
    at ``rev`` into the downstream ``imports:`` and add the package to
    ``dashboard.registry.include``. A package the source workspace does not itself
    declare is left unresolved (never fabricated). Returns ``(added_keys, unresolved)``.
    """
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    src_imports = _source_workspace_yaml(src, rev).get("imports") or {}
    by_stem: dict[str, tuple[str, object]] = {
        _pkg_stem(str(k)): (str(k), v) for k, v in src_imports.items()
    }

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    wf = ws_root / "workspace.yaml"
    doc = yaml_rt.load(wf.read_text(encoding="utf-8")) or CommentedMap()

    imports = doc.get("imports")
    if not isinstance(imports, dict):
        imports = CommentedMap()
        doc["imports"] = imports

    dashboard = doc.get("dashboard")
    if not isinstance(dashboard, dict):
        dashboard = CommentedMap()
        doc["dashboard"] = dashboard
    registry = dashboard.get("registry")
    if not isinstance(registry, dict):
        registry = CommentedMap()
        dashboard["registry"] = registry
    include = registry.get("include")
    if not isinstance(include, list):
        include = CommentedSeq()
        registry["include"] = include

    added: list[str] = []
    unresolved: list[str] = []
    for dep in missing:
        if dep.stem not in by_stem:
            unresolved.append(dep.package)
            continue
        key, block = by_stem[dep.stem]
        if _pkg_stem(key) not in {_pkg_stem(str(k)) for k in imports}:
            imports[key] = copy.deepcopy(block)
        if _pkg_stem(key) not in {_pkg_stem(str(k)) for k in include}:
            include.append(key)
        added.append(key)

    if added:
        yaml_rt.dump(doc, wf)
    return added, unresolved


def sync(
    ws_root: Path,
    *,
    dry_run: bool = False,
    upstream_src: Path | None = None,
    rev: str | None = None,
    add_deps: bool = False,
) -> SyncResult:
    """Additively import allowlisted investigations + their dependencies from the pinned rev.

    Brings, for every allowlisted investigation:
      * the investigation dir (if missing locally);
      * its member studies (if missing locally) — flat or nested upstream;
    and detects the package deps its studies reference. Package deps are only
    *reported* unless ``add_deps=True``, which mirrors the source's import blocks into
    this workspace.yaml. Never overwrites a local copy; only allowlisted slugs pulled.
    """
    sel = load_selection(ws_root)
    if sel.source is None:
        raise RuntimeError(
            "workspace.yaml `imported_investigations` is a plain list (guard-only). Add a "
            "`from:`/`git:` source block to enable sync — see investigation_import docstring."
        )
    token = sel.source.rev_token
    rev = rev or (pinned_rev(ws_root, token) if token else None)
    if not rev:
        raise RuntimeError(
            "could not resolve the pinned upstream rev from uv.lock "
            f"(token={token!r}); pass rev= explicitly."
        )

    subtree = sel.source.subtree
    cache_key = token or "upstream"
    src = _ensure_source(rev, git=sel.source.git, src=upstream_src, cache_key=cache_key)
    upstream = _upstream_slugs(src, rev, subtree)
    have = present_slugs(ws_root)

    already_local = sorted(set(sel.allow) & have)
    missing_upstream = [s for s in sel.allow if s not in upstream]
    to_add = [s for s in sel.allow if s not in have and s in upstream]

    studies_added: dict[str, list[str]] = {}
    studies_missing_upstream: dict[str, list[str]] = {}

    if not dry_run:
        inv_dir = investigations_dir(ws_root)
        inv_dir.mkdir(parents=True, exist_ok=True)
        for slug in to_add:
            _copy_investigation(src, rev, slug, subtree, inv_dir)

        # Reconcile member studies for EVERY allowlisted investigation now present —
        # not just freshly-added ones. The motivating bug was a partial sync: the
        # investigation.yaml already local, its member study missing.
        studies_subtree = _source_studies_subtree(src, rev, subtree)
        present_after = present_slugs(ws_root)
        for inv in [s for s in sel.allow if s in present_after]:
            added, miss = _copy_member_studies(
                src, rev, inv, ws_root, subtree, studies_subtree
            )
            if added:
                studies_added[inv] = added
            if miss:
                studies_missing_upstream[inv] = miss

    # Dependency detection (offline, over the now-materialized studies).
    imported_present = sorted(present_slugs(ws_root) & set(sel.allow))
    dep_findings = detect_missing_deps(ws_root, imported_present)
    deps_added: list[str] = []
    deps_unresolved: list[str] = []
    missing_deps = [d for d in dep_findings if not d.satisfied]
    if add_deps and missing_deps and not dry_run:
        deps_added, deps_unresolved = _apply_deps(ws_root, missing_deps, src, rev)

    return SyncResult(
        rev=rev,
        allow=list(sel.allow),
        already_local=already_local,
        added=to_add,
        missing_upstream=missing_upstream,
        dry_run=dry_run,
        studies_added=studies_added,
        studies_missing_upstream=studies_missing_upstream,
        dep_findings=dep_findings,
        deps_added=deps_added,
        deps_unresolved=deps_unresolved,
    )


# ---------------------------------------------------------------------------
# CLI: python -m discovery_engine.investigation_import {check,sync}
# ---------------------------------------------------------------------------


def _resolve_ws(ws_arg: str | None) -> Path:
    if ws_arg:
        root = Path(ws_arg).resolve()
        if not (root / "workspace.yaml").is_file():
            sys.exit(f"no workspace.yaml under {root}")
        return root
    return find_workspace_root(Path.cwd())


def _cmd_check(args) -> int:
    ws_root = _resolve_ws(args.ws)
    problems = check(ws_root)
    sel = load_selection(ws_root)
    print(f"imported (allow): {len(sel.allow)}   native: {len(sel.native)}   "
          f"present: {len(present_slugs(ws_root))}")
    errors = [p for p in problems if p.severity == "error"]
    warnings = [p for p in problems if p.severity == "warning"]
    for p in warnings:
        print(f"[warning] {p.message}")
    if not errors:
        if warnings:
            print("OK (errors) — every investigation declared + complete; "
                  f"{len(warnings)} dep warning(s) above.")
        else:
            print("OK — every investigation is declared + complete; lists are disjoint.")
        return 0
    for p in errors:
        print(f"[error] {p.message}")
    return 1


def _cmd_sync(args) -> int:
    ws_root = _resolve_ws(args.ws)
    res = sync(
        ws_root,
        dry_run=args.dry_run,
        upstream_src=args.upstream_src,
        rev=args.rev,
        add_deps=args.add_deps,
    )
    print(f"upstream rev:   {res.rev[:12]}")
    print(f"allowlist:      {len(res.allow)} slug(s)")
    print(f"already local:  {res.already_local}")
    if res.missing_upstream:
        print(f"WARN: allowlisted but NOT in upstream@{res.rev[:12]}: {res.missing_upstream}")

    if res.added:
        print(f"investigations added: {res.added}")
        if not res.dry_run:
            for slug in res.added:
                print(f"  + {slug}")
    else:
        print("investigations: nothing to add (all allowlisted already present).")

    if res.studies_added:
        for inv, studies in res.studies_added.items():
            print(f"member studies added to '{inv}': {studies}")
            for s in studies:
                print(f"  + {s}")
    if res.studies_missing_upstream:
        for inv, studies in res.studies_missing_upstream.items():
            print(f"WARN: '{inv}' member study(ies) NOT found upstream@{res.rev[:12]}: {studies}")

    missing_deps = [d for d in res.dep_findings if not d.satisfied]
    if missing_deps:
        print("package dependencies NOT satisfied here:")
        for d in missing_deps:
            print(f"  ! {d.package}  (referenced by {d.referenced_by})")
        if res.deps_added:
            print(f"added to imports + registry.include: {res.deps_added}")
        if res.deps_unresolved:
            print(f"WARN: unresolved (source workspace does not declare): {res.deps_unresolved}")
        if not args.add_deps:
            print("  (run `sync --add-deps` to mirror the source's import blocks)")
    elif res.dep_findings:
        print("package dependencies: all satisfied.")

    if res.dry_run:
        print("(dry-run — nothing copied)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m discovery_engine.investigation_import",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--ws", "--workspace", dest="ws", default=None,
                   help="workspace root (default: walk up from CWD).")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("check", help="guard: every present investigation declared + complete.")
    pc.set_defaults(func=_cmd_check)

    ps = sub.add_parser("sync", help="additively import missing allowlisted investigations + deps.")
    ps.add_argument("--dry-run", action="store_true", help="print what would be added; copy nothing.")
    ps.add_argument("--upstream-src", type=Path, default=None,
                    help="use an existing local upstream checkout instead of a cache clone.")
    ps.add_argument("--rev", default=None, help="override the rev (default: pinned in uv.lock).")
    ps.add_argument("--add-deps", "--fix", dest="add_deps", action="store_true",
                    help="additively write missing package deps into workspace.yaml "
                         "(mirroring the source's import blocks). Default: report only.")
    ps.set_defaults(func=_cmd_sync)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
