"""Tests for the L0-L5 study-reproducibility audit (discovery_engine.study_audit)."""
import json
from types import SimpleNamespace

import discovery_engine.study_audit as study_audit

from discovery_engine.study_audit import (
    CheckResult,
    StudyAudit,
    AuditReport,
    audit_workspace,
    render_report,
    main,
    _composite_resolvable,
)
from discovery_engine.workspace_paths import WorkspacePaths


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _ws(tmp_path, name="t"):
    (tmp_path / "workspace.yaml").write_text(f"name: {name}\n", encoding="utf-8")
    (tmp_path / "studies").mkdir(exist_ok=True)
    return tmp_path


def _study(tmp_path, slug, text):
    d = tmp_path / "studies" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "study.yaml").write_text(text, encoding="utf-8")
    return d


def _find(audit, name):
    return next((c for c in audit.checks if c.name == name), None)


def _all_checks(report):
    out = []
    for a in list(report.studies) + list(report.investigations):
        out.extend(a.checks)
    return out


def _find_any(report, name):
    return next((c for c in _all_checks(report) if c.name == name), None)


# ---------------------------------------------------------------------------
# Task 1 — data model + enumeration + empty report
# ---------------------------------------------------------------------------

def test_empty_workspace_returns_empty_report(tmp_path):
    _ws(tmp_path)
    report = audit_workspace(tmp_path)
    assert report.studies == []
    assert report.investigations == []
    assert report.hard_failures() == []
    d = report.as_dict()
    assert d["studies"] == []
    assert d["investigations"] == []
    # fully JSON-serializable
    assert json.loads(json.dumps(d)) == d


def test_worst_semantics():
    a = StudyAudit(slug="s", checks=[])
    assert a.worst() == "pass"
    a.checks.append(CheckResult("L0", "x", "pass", "hard"))
    assert a.worst() == "pass"
    a.checks.append(CheckResult("L2", "y", "warn", "soft"))
    assert a.worst() == "warn"
    a.checks.append(CheckResult("L0", "z", "fail", "hard"))
    assert a.worst() == "fail"


def test_enumeration_produces_one_studyaudit_per_study(tmp_path):
    _ws(tmp_path)
    _study(tmp_path, "s1", "name: s1\n")
    _study(tmp_path, "s2", "name: s2\n")
    report = audit_workspace(tmp_path)
    slugs = {a.slug for a in report.studies}
    assert {"s1", "s2"} <= slugs


# ---------------------------------------------------------------------------
# Task 2 — L0 Structure + L1 Resolvability (HARD tier)
# ---------------------------------------------------------------------------

_KNOWN = {"pkg.good"}
_GENPARAMS = {"pkg.good": {"rate"}}

_GOOD_STUDY = """\
name: s1
conditions:
  baseline:
    name: base
    composite: pkg.good
  variants:
    - name: v1
      composite: pkg.good
      params:
        rate: 2
  model_settings: []
"""


def _audit(tmp_path):
    return audit_workspace(tmp_path, known_composites=_KNOWN, generator_params=_GENPARAMS)


def test_good_study_passes_all_l0_l1(tmp_path):
    _ws(tmp_path)
    _study(tmp_path, "s1", _GOOD_STUDY)
    report = _audit(tmp_path)
    (a,) = [x for x in report.studies if x.slug == "s1"]
    for name in ("slug-matches-dir", "canonical-model-schema",
                 "composite-resolves", "params-are-generator-accepted",
                 "inputs-from-resolves"):
        c = _find(a, name)
        assert c is not None, f"{name} missing"
        assert c.status == "pass", f"{name} -> {c.status}: {c.detail}"
    assert report.hard_failures() == []


def test_nested_study_fails_no_nested(tmp_path):
    _ws(tmp_path)
    nested = tmp_path / "investigations" / "inv" / "study.yaml"
    nested.parent.mkdir(parents=True)
    nested.write_text("name: nested\n", encoding="utf-8")
    report = _audit(tmp_path)
    c = _find_any(report, "no-nested-study")
    assert c is not None and c.status == "fail" and c.tier == "hard"


def test_slug_mismatch_fails(tmp_path):
    _ws(tmp_path)
    _study(tmp_path, "s1", "name: other\n")
    report = _audit(tmp_path)
    (a,) = [x for x in report.studies if x.slug == "s1"]
    c = _find(a, "slug-matches-dir")
    assert c.status == "fail" and c.tier == "hard"


def test_missing_composite_fails_resolution(tmp_path):
    _ws(tmp_path)
    _study(tmp_path, "s1",
           "name: s1\nconditions:\n  baseline:\n    composite: pkg.missing\n  model_settings: []\n")
    report = _audit(tmp_path)
    (a,) = [x for x in report.studies if x.slug == "s1"]
    c = _find(a, "composite-resolves")
    assert c.status == "fail" and c.tier == "hard"


def test_package_file_composite_resolves(tmp_path):
    # A study may reference a composite shipped as a *.composite.json FILE in the
    # workspace package's composites dir (ws_root/<pkg>/composites) — the same
    # place the workbench's find_composite_path resolves it at run time. The
    # audit must discover it too, not only the layout composites dir + registry.
    _ws(tmp_path)
    cdir = tmp_path / "mypkg" / "composites"
    cdir.mkdir(parents=True)
    (cdir / "myfig.composite.json").write_text('{"name": "myfig", "state": {}}',
                                                encoding="utf-8")
    _study(tmp_path, "s1",
           "name: s1\nconditions:\n  baseline:\n    composite: mypkg.composites.myfig\n"
           "  model_settings: []\n")
    report = _audit(tmp_path)
    (a,) = [x for x in report.studies if x.slug == "s1"]
    c = _find(a, "composite-resolves")
    assert c.status == "pass", f"{c.status}: {c.detail}"


def test_package_file_composite_name_match_resolves(tmp_path):
    # The id tail may be the doc's `name` rather than the filename stem.
    _ws(tmp_path)
    cdir = tmp_path / "mypkg" / "composites"
    cdir.mkdir(parents=True)
    (cdir / "fig04a-interaction.composite.json").write_text(
        '{"name": "interaction-modalities", "state": {}}', encoding="utf-8")
    _study(tmp_path, "s1",
           "name: s1\nconditions:\n  baseline:\n    composite: mypkg.composites.interaction-modalities\n"
           "  model_settings: []\n")
    report = _audit(tmp_path)
    (a,) = [x for x in report.studies if x.slug == "s1"]
    assert _find(a, "composite-resolves").status == "pass"


_PLANNED_TEMPLATE = """name: s1
contract:
  face:
    inputs:
      temperature: {_type: float}
    outputs:
      y: {_type: float}
conditions:
  baseline:
    composite: pkg.not_built_yet
    planned: true
  model_settings: []
"""


def test_planned_template_with_contract_is_soft_not_fail(tmp_path):
    # A study that declares a contract.face and marks its composite planned:true
    # is a template -- an interface committed before the mechanism (holes/
    # templates). Its unresolved composite must NOT hard-fail; it is graded soft.
    _ws(tmp_path)
    _study(tmp_path, "s1", _PLANNED_TEMPLATE)
    report = _audit(tmp_path)
    (a,) = [x for x in report.studies if x.slug == "s1"]
    c = _find(a, "composite-resolves")
    assert c.status == "warn" and c.tier == "soft", f"{c.status}/{c.tier}: {c.detail}"
    assert "template" in c.detail.lower()
    assert a.slug not in [h.split(":")[0] for h in report.hard_failures()]


def test_planned_without_contract_face_still_fails(tmp_path):
    # planned:true but NO contract.face is an unbuilt stub, not a template:
    # it commits to no interface, so the unresolved composite still hard-fails.
    _ws(tmp_path)
    _study(tmp_path, "s1",
           "name: s1\nconditions:\n  baseline:\n    composite: pkg.not_built_yet\n"
           "    planned: true\n  model_settings: []\n")
    report = _audit(tmp_path)
    (a,) = [x for x in report.studies if x.slug == "s1"]
    c = _find(a, "composite-resolves")
    assert c.status == "fail" and c.tier == "hard"


def test_bogus_params_fail(tmp_path):
    _ws(tmp_path)
    _study(tmp_path, "s1",
           "name: s1\nconditions:\n  baseline:\n    composite: pkg.good\n    params:\n      bogus: 1\n  model_settings: []\n")
    report = _audit(tmp_path)
    (a,) = [x for x in report.studies if x.slug == "s1"]
    c = _find(a, "params-are-generator-accepted")
    assert c.status == "fail" and c.tier == "hard"


def test_n_steps_param_is_accepted(tmp_path):
    _ws(tmp_path)
    _study(tmp_path, "s1",
           "name: s1\nconditions:\n  baseline:\n    composite: pkg.good\n    params:\n      rate: 1\n      n_steps: 10\n  model_settings: []\n")
    report = _audit(tmp_path)
    (a,) = [x for x in report.studies if x.slug == "s1"]
    assert _find(a, "params-are-generator-accepted").status == "pass"


def test_dangling_input_from_fails(tmp_path):
    _ws(tmp_path)
    _study(tmp_path, "s1",
           "name: s1\nconditions:\n  baseline:\n    composite: pkg.good\n  model_settings: []\n"
           "inputs:\n  - artifact: x\n    from: nope\n")
    report = _audit(tmp_path)
    (a,) = [x for x in report.studies if x.slug == "s1"]
    c = _find(a, "inputs-from-resolves")
    assert c.status == "fail" and c.tier == "hard"


def test_malformed_study_is_single_l0_fail(tmp_path):
    _ws(tmp_path)
    d = tmp_path / "studies" / "bad"
    d.mkdir(parents=True)
    (d / "study.yaml").write_text("name: [unterminated\n", encoding="utf-8")
    report = _audit(tmp_path)  # must not raise
    (a,) = [x for x in report.studies if x.slug == "bad"]
    assert a.worst() == "fail"
    assert any(c.level == "L0" and c.status == "fail" for c in a.checks)


def test_legacy_studies_key_warns(tmp_path):
    _ws(tmp_path)
    inv = tmp_path / "investigations" / "inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text("name: inv\nstudies:\n  - s1\n", encoding="utf-8")
    report = _audit(tmp_path)
    (a,) = [x for x in report.investigations if x.slug == "inv"]
    c = _find(a, "investigation-members-only")
    assert c.status == "warn"


# ---------------------------------------------------------------------------
# Task 3 — L2/L3/L4 (SOFT) + L5 Ordering (per investigation)
# ---------------------------------------------------------------------------

def _inv(tmp_path, slug, text):
    d = tmp_path / "investigations" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "investigation.yaml").write_text(text, encoding="utf-8")
    return d


def test_l2_node_keyable_pass_and_warn(tmp_path):
    _ws(tmp_path)
    # resolvable composite, no inputs -> keyable
    _study(tmp_path, "s1", _GOOD_STUDY)
    # resolvable composite but a dangling input -> not keyable
    _study(tmp_path, "s2",
           "name: s2\nconditions:\n  baseline:\n    composite: pkg.good\n  model_settings: []\n"
           "inputs:\n  - artifact: x\n    from: nope\n")
    report = _audit(tmp_path)
    a1 = next(x for x in report.studies if x.slug == "s1")
    a2 = next(x for x in report.studies if x.slug == "s2")
    c1 = _find(a1, "node-keyable")
    c2 = _find(a2, "node-keyable")
    assert c1.status == "pass" and c1.tier == "soft"
    assert c2.status == "warn" and c2.tier == "soft"


def test_l3_outputs_present_warn_when_declared_but_absent(tmp_path):
    _ws(tmp_path)
    _study(tmp_path, "s1", _GOOD_STUDY + "visualizations:\n  - name: fig1\n")
    report = _audit(tmp_path)
    a = next(x for x in report.studies if x.slug == "s1")
    c = _find(a, "outputs-present")
    assert c.status == "warn" and c.tier == "soft"


def test_l3_l4_pass_with_card_and_verdict(tmp_path):
    _ws(tmp_path)
    sdir = _study(tmp_path, "s1", _GOOD_STUDY + "visualizations:\n  - name: fig1\n")
    rc = sdir / "viz" / "report_card"
    rc.mkdir(parents=True)
    (rc / "x.html").write_text("<html></html>", encoding="utf-8")
    (rc / "x.verdict.json").write_text(json.dumps({"overall": "pass"}), encoding="utf-8")
    report = _audit(tmp_path)
    a = next(x for x in report.studies if x.slug == "s1")
    assert _find(a, "outputs-present").status == "pass"
    assert _find(a, "report-card-verdict").status == "pass"


def test_l4_warn_when_card_lacks_verdict(tmp_path):
    _ws(tmp_path)
    sdir = _study(tmp_path, "s1", _GOOD_STUDY)
    rc = sdir / "viz" / "report_card"
    rc.mkdir(parents=True)
    (rc / "x.html").write_text("<html></html>", encoding="utf-8")
    report = _audit(tmp_path)
    a = next(x for x in report.studies if x.slug == "s1")
    c = _find(a, "report-card-verdict")
    assert c.status == "warn" and c.tier == "soft"


def test_l4_pass_when_no_cards(tmp_path):
    _ws(tmp_path)
    _study(tmp_path, "s1", _GOOD_STUDY)
    report = _audit(tmp_path)
    a = next(x for x in report.studies if x.slug == "s1")
    assert _find(a, "report-card-verdict").status == "pass"


def _study_with_input(tmp_path, slug, producer=None):
    text = f"name: {slug}\nconditions:\n  baseline:\n    composite: pkg.good\n  model_settings: []\n"
    if producer is not None:
        text += f"inputs:\n  - artifact: {producer}\n    from: {producer}\n"
    _study(tmp_path, slug, text)


def test_l5_dag_acyclic_and_order(tmp_path):
    _ws(tmp_path)
    _study_with_input(tmp_path, "a")
    _study_with_input(tmp_path, "b", producer="a")
    _inv(tmp_path, "inv", "name: inv\nmembers:\n  - a\n  - b\n")
    report = _audit(tmp_path)
    a = next(x for x in report.investigations if x.slug == "inv")
    assert _find(a, "dag-acyclic").status == "pass"
    assert _find(a, "no-dangling-edges").status == "pass"
    topo = _find(a, "topological-executable")
    assert topo.status == "pass" and topo.tier == "soft"
    assert topo.detail.index("a") < topo.detail.index("b")


def test_l5_cycle_fails_hard(tmp_path):
    _ws(tmp_path)
    _study_with_input(tmp_path, "a", producer="b")
    _study_with_input(tmp_path, "b", producer="a")
    _inv(tmp_path, "inv", "name: inv\nmembers:\n  - a\n  - b\n")
    report = _audit(tmp_path)
    a = next(x for x in report.investigations if x.slug == "inv")
    c = _find(a, "dag-acyclic")
    assert c.status == "fail" and c.tier == "hard"
    assert (a.slug, c) in report.hard_failures()


def test_l5_dangling_member_edge_fails_hard(tmp_path):
    _ws(tmp_path)
    _study_with_input(tmp_path, "a")
    _study_with_input(tmp_path, "b", producer="ghost")
    _inv(tmp_path, "inv", "name: inv\nmembers:\n  - a\n  - b\n")
    report = _audit(tmp_path)
    a = next(x for x in report.investigations if x.slug == "inv")
    c = _find(a, "no-dangling-edges")
    assert c.status == "fail" and c.tier == "hard"


# ---------------------------------------------------------------------------
# reproducible-rerun-spine Task 7 — public investigation_execution_order()
# ---------------------------------------------------------------------------

def test_investigation_execution_order_a_b_c(tmp_path):
    from discovery_engine.study_audit import investigation_execution_order

    _ws(tmp_path)
    _study_with_input(tmp_path, "A")
    _study_with_input(tmp_path, "B", producer="A")
    _study_with_input(tmp_path, "C", producer="B")
    _inv(tmp_path, "inv", "name: inv\nmembers:\n  - A\n  - B\n  - C\n")
    wp = WorkspacePaths.load(tmp_path)
    inv_spec = {"name": "inv", "members": ["A", "B", "C"]}
    assert investigation_execution_order(wp, inv_spec) == ["A", "B", "C"]


def test_investigation_execution_order_ignores_non_member_producer(tmp_path):
    from discovery_engine.study_audit import investigation_execution_order

    _ws(tmp_path)
    _study_with_input(tmp_path, "upstream")
    _study_with_input(tmp_path, "A", producer="upstream")
    _study_with_input(tmp_path, "B", producer="A")
    _inv(tmp_path, "inv", "name: inv\nmembers:\n  - A\n  - B\n")
    wp = WorkspacePaths.load(tmp_path)
    inv_spec = {"name": "inv", "members": ["A", "B"]}
    order = investigation_execution_order(wp, inv_spec)
    # "upstream" is not a declared member -> never appears, but still shaped
    # the ordering of the members that do come back.
    assert order == ["A", "B"]


def test_investigation_execution_order_cycle_falls_back_to_declared_order(tmp_path):
    from discovery_engine.study_audit import investigation_execution_order

    _ws(tmp_path)
    _study_with_input(tmp_path, "A", producer="B")
    _study_with_input(tmp_path, "B", producer="A")
    _inv(tmp_path, "inv", "name: inv\nmembers:\n  - A\n  - B\n")
    wp = WorkspacePaths.load(tmp_path)
    inv_spec = {"name": "inv", "members": ["A", "B"]}
    # graphlib.CycleError -> best-effort degrade to declared member order.
    assert investigation_execution_order(wp, inv_spec) == ["A", "B"]


# ---------------------------------------------------------------------------
# Task 4 — CLI + --gate + --json
# ---------------------------------------------------------------------------

def test_gate_zero_on_clean_workspace(tmp_path):
    # main() does not inject fakes, so it audits against the real generator
    # registry; an empty workspace has no hard failures -> gate exits 0.
    _ws(tmp_path)
    assert main(["--workspace", str(tmp_path), "--gate"]) == 0


def test_gate_nonzero_on_hard_fail(tmp_path):
    _ws(tmp_path)
    nested = tmp_path / "investigations" / "inv" / "study.yaml"
    nested.parent.mkdir(parents=True)
    nested.write_text("name: nested\n", encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "--gate"]) == 1


def test_no_gate_always_zero(tmp_path):
    _ws(tmp_path)
    nested = tmp_path / "investigations" / "inv" / "study.yaml"
    nested.parent.mkdir(parents=True)
    nested.write_text("name: nested\n", encoding="utf-8")
    # hard fail present, but no --gate -> exit 0
    assert main(["--workspace", str(tmp_path)]) == 0


def test_json_output_round_trips(tmp_path, capsys):
    _ws(tmp_path)
    _study(tmp_path, "s1", _GOOD_STUDY)
    rc = main(["--workspace", str(tmp_path), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert isinstance(parsed, dict) and "studies" in parsed


def test_render_report_is_a_string(tmp_path):
    _ws(tmp_path)
    _study(tmp_path, "s1", _GOOD_STUDY)
    report = _audit(tmp_path)
    text = render_report(report)
    assert isinstance(text, str)
    assert "s1" in text


# ---------------------------------------------------------------------------
# Review fixes — totality, relative workspace, extra_packages, --json hygiene
# ---------------------------------------------------------------------------

def test_scalar_variants_never_crashes_whole_audit(tmp_path):
    _ws(tmp_path)
    # malformed: variants is a scalar, not a list
    _study(tmp_path, "bad",
           "name: bad\nconditions:\n  baseline:\n    composite: pkg.good\n  variants: 5\n  model_settings: []\n")
    _study(tmp_path, "s1", _GOOD_STUDY)
    report = _audit(tmp_path)  # must NOT raise
    slugs = {a.slug for a in report.studies}
    assert {"bad", "s1"} <= slugs
    # the good study is still fully audited
    a1 = next(x for x in report.studies if x.slug == "s1")
    assert _find(a1, "composite-resolves").status == "pass"
    # the malformed one is a single L0 fail, not a crash
    abad = next(x for x in report.studies if x.slug == "bad")
    assert abad.worst() == "fail"
    assert any(c.level == "L0" and c.status == "fail" for c in abad.checks)


def test_scalar_inputs_never_crashes(tmp_path):
    _ws(tmp_path)
    _study(tmp_path, "s1",
           "name: s1\nconditions:\n  baseline:\n    composite: pkg.good\n  model_settings: []\n"
           "inputs: true\n")
    report = _audit(tmp_path)  # must NOT raise
    assert any(a.slug == "s1" for a in report.studies)


def test_scalar_members_never_crashes(tmp_path):
    _ws(tmp_path)
    _inv(tmp_path, "inv", "name: inv\nmembers: 7\n")
    report = _audit(tmp_path)  # must NOT raise
    assert any(a.slug == "inv" for a in report.investigations)


def test_relative_workspace_with_nested_study(tmp_path, monkeypatch):
    _ws(tmp_path)
    nested = tmp_path / "investigations" / "inv" / "study.yaml"
    nested.parent.mkdir(parents=True)
    nested.write_text("name: n\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    report = audit_workspace(".", known_composites=_KNOWN, generator_params=_GENPARAMS)  # no ValueError
    c = _find_any(report, "no-nested-study")
    assert c is not None and c.status == "fail"


def test_extra_packages_forwarded_to_discovery(tmp_path, monkeypatch):
    _ws(tmp_path)
    captured = {}

    def fake_discover(extra_packages=None):
        captured["extra"] = list(extra_packages or [])
        return {}

    monkeypatch.setattr(study_audit, "_discover_generators", fake_discover)
    # no injection -> defaults derived -> discovery is called with our packages
    audit_workspace(tmp_path, extra_packages=["foo.composites"])
    assert "foo.composites" in captured["extra"]


def test_package_flag_forwarded(tmp_path, monkeypatch):
    _ws(tmp_path)
    captured = {}

    def fake_discover(extra_packages=None):
        captured["extra"] = list(extra_packages or [])
        return {}

    monkeypatch.setattr(study_audit, "_discover_generators", fake_discover)
    main(["--workspace", str(tmp_path), "--package", "v2ecoli.composites"])
    assert "v2ecoli.composites" in captured["extra"]


def test_json_clean_despite_discovery_stdout(tmp_path, capsys, monkeypatch):
    _ws(tmp_path)

    def noisy_discover(extra_packages=None):
        print("Setting POLARS_MAX_THREADS=1")
        print("skipping `foo`: ImportError")
        return {}

    monkeypatch.setattr(study_audit, "_discover_generators", noisy_discover)
    rc = main(["--workspace", str(tmp_path), "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)  # must parse despite the discovery prints
    assert "studies" in parsed


def test_legacy_parameter_overrides_are_validated(tmp_path):
    _ws(tmp_path)
    _study(tmp_path, "s1",
           "name: s1\nconditions:\n  baseline:\n    composite: pkg.good\n"
           "    parameter_overrides:\n      bogus: 1\n  model_settings: []\n")
    report = _audit(tmp_path)
    a = next(x for x in report.studies if x.slug == "s1")
    assert _find(a, "params-are-generator-accepted").status == "fail"


def test_composite_resolvable_alias_forms(tmp_path):
    _ws(tmp_path)
    wp = WorkspacePaths.load(tmp_path)
    # registry ids carry a duplicated trailing module segment (v2ecoli style)
    known = {"v2ecoli.composites.reactor_bird_coupled.reactor_bird_coupled",
             "v2ecoli.composites.parca.parca"}
    # full id resolves
    assert _composite_resolvable("v2ecoli.composites.parca.parca", known, wp) is True
    # collapsed duplicated-tail (module) form resolves
    assert _composite_resolvable(
        "v2ecoli.composites.reactor_bird_coupled", known, wp) is True
    # bare composite name resolves
    assert _composite_resolvable("parca", known, wp) is True
    # a genuinely-absent composite (different, uninstalled package) does NOT
    assert _composite_resolvable("v2ecoli_pdmp.composites.x.x", known, wp) is False
    # a non-duplicated id does NOT leak its parent module as resolvable
    assert _composite_resolvable("pkg", {"pkg.good"}, wp) is False


def test_composite_resolvable_no_bare_suffix_match(tmp_path):
    _ws(tmp_path)
    wp = WorkspacePaths.load(tmp_path)
    assert _composite_resolvable("pkg.millard2017_metabolism", set(), wp) is True
    assert _composite_resolvable("millard2017_metabolism", set(), wp) is True
    # bare-suffix false positive must NOT resolve
    assert _composite_resolvable("foo_millard2017_metabolism", set(), wp) is False


_INHERIT_STUDY = """\
name: s1
conditions:
  baseline:
    name: base
    composite: pkg.good
  variants:
    - name: v1
      params:
        rate: 2
  model_settings: []
"""


def test_variant_inherits_baseline_composite(tmp_path):
    # A variant that omits `composite` inherits the baseline's — legitimate
    # canonical authoring. Both L1 resolution and L0 schema must pass.
    _ws(tmp_path)
    _study(tmp_path, "s1", _INHERIT_STUDY)
    report = _audit(tmp_path)
    a = next(x for x in report.studies if x.slug == "s1")
    assert _find(a, "composite-resolves").status == "pass"
    assert _find(a, "canonical-model-schema").status == "pass"


def test_gate_zero_on_populated_clean_workspace(tmp_path, monkeypatch):
    _ws(tmp_path)
    _study(tmp_path, "s1", _GOOD_STUDY)
    entry = SimpleNamespace(id="pkg.good", parameters={"rate": {"type": "integer"}})
    monkeypatch.setattr(study_audit, "_discover_generators",
                        lambda extra_packages=None: {"pkg.good": entry})
    # main() does not inject fakes; with pkg.good registered the study is clean
    assert main(["--workspace", str(tmp_path), "--gate"]) == 0


# ---------------------------------------------------------------------------
# B1 — allowlist / ratchet mechanism

def test_load_allowlist_parses_and_skips_comments(tmp_path):
    from discovery_engine.study_audit import load_allowlist
    f = tmp_path / "known.txt"
    f.write_text(
        "# accepted debt\n"
        "s1:composite-resolves\n"
        "\n"
        "s2:params-are-generator-accepted  # inline comment\n",
        encoding="utf-8")
    assert load_allowlist(f) == {"s1:composite-resolves",
                                 "s2:params-are-generator-accepted"}
    assert load_allowlist(tmp_path / "nope.txt") == set()   # missing -> empty


def test_hard_failures_excludes_allowlisted(tmp_path):
    _ws(tmp_path)
    _study(tmp_path, "s1",
           "name: s1\nconditions:\n  baseline:\n    composite: pkg.missing\n  model_settings: []\n")
    report = _audit(tmp_path)
    assert report.hard_failures()                              # fails without allowlist
    allow = {"s1:composite-resolves"}
    assert report.hard_failures(allowlist=allow) == []         # suppressed
    assert report.stale_allowlist_entries(allow) == set()      # it DOES occur -> not stale


def test_stale_allowlist_entry_detected(tmp_path):
    _ws(tmp_path)
    _study(tmp_path, "s1", _GOOD_STUDY)                        # clean study, no hard fails
    report = _audit(tmp_path)
    allow = {"s1:composite-resolves"}                          # parked but no longer fails
    assert report.stale_allowlist_entries(allow) == {"s1:composite-resolves"}


def test_gate_with_allowlist(tmp_path, capsys):
    from discovery_engine.study_audit import main
    _ws(tmp_path)
    _study(tmp_path, "s1",
           "name: s1\nconditions:\n  baseline:\n    composite: pkg.missing\n  model_settings: []\n")
    al = tmp_path / "known.txt"
    # not allowlisted -> gate fails
    al.write_text("# empty\n", encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "--gate", "--allowlist", str(al)]) == 1
    # allowlisted -> gate passes
    al.write_text("s1:composite-resolves\n", encoding="utf-8")
    assert main(["--workspace", str(tmp_path), "--gate", "--allowlist", str(al)]) == 0


def test_gate_fails_on_stale_allowlist_entry(tmp_path):
    from discovery_engine.study_audit import main
    _ws(tmp_path)   # empty workspace: no studies, so NO hard failures at all
    al = tmp_path / "known.txt"
    al.write_text("ghost:composite-resolves\n", encoding="utf-8")  # matches nothing -> stale
    # ratchet: a stale allowlist entry must FAIL the gate so it gets removed
    # (registry-independent — main() uses the real registry, but an empty
    # workspace has zero hard failures regardless).
    assert main(["--workspace", str(tmp_path), "--gate", "--allowlist", str(al)]) == 1
