from discovery_engine.discover._policy import AUTONOMOUS, AutonomyPolicy
from discovery_engine.discover.actions import record_model_acquisition_proposal
from discovery_engine.discover.cycle import execute_action
from discovery_engine.investigation.import_ import investigations_dir
from discovery_engine.study.io import load_yaml_mapping, save_yaml_atomic

SLUG = "inv"
MECH = "feedback_inhibition"


def _decider(verb, why="because"):
    return lambda t, d: {"choice": verb, "rationale": why}


def _setup(tmp_path):
    inv_dir = investigations_dir(tmp_path) / SLUG
    inv_dir.mkdir(parents=True)
    path = inv_dir / "investigation.yaml"
    save_yaml_atomic(path, {"slug": SLUG})
    p = record_model_acquisition_proposal(path, {
        "mechanism": MECH,
        "expected_behavioral_test": {"behavior_test": "t", "pass_if": {"min": 1}},
        "provenance": {"trigger": "trig"},
    })
    return path, p["id"]


def _run(tmp_path, ma_id, **kw):
    kw.setdefault("policy", AutonomyPolicy(mode=AUTONOMOUS))
    return execute_action(tmp_path, SLUG, target=ma_id, action="accept_model_acquisition", **kw)


def _prop(path, ma_id):
    return next(p for p in load_yaml_mapping(path)["model_acquisition_proposals"] if p["id"] == ma_id)


def test_autonomous_decider_accepts_and_feeds_mechanism(tmp_path):
    path, i = _setup(tmp_path)
    rec = _run(tmp_path, i, autonomous=True, decider=_decider("approve"))
    assert rec["status"] == "executed"
    assert _prop(path, i)["status"] == "accepted"
    assert MECH in load_yaml_mapping(path).get("required_mechanisms", [])


def test_reject_declines(tmp_path):
    path, i = _setup(tmp_path)
    rec = _run(tmp_path, i, autonomous=True, decider=_decider("reject"))
    assert rec["status"] == "executed"
    assert _prop(path, i)["status"] == "declined"
    assert MECH not in load_yaml_mapping(path).get("required_mechanisms", [])


def test_no_decider_is_gated(tmp_path):
    path, i = _setup(tmp_path)
    rec = _run(tmp_path, i, autonomous=True, decider=None)
    assert rec["status"] == "gated-human" and _prop(path, i)["status"] == "proposed"


def test_not_autonomous_is_gated(tmp_path):
    path, i = _setup(tmp_path)
    rec = _run(tmp_path, i, autonomous=False, decider=_decider("approve"), policy=AutonomyPolicy())
    assert rec["status"] == "gated-human" and _prop(path, i)["status"] == "proposed"


def test_policy_denies_is_gated(tmp_path):
    path, i = _setup(tmp_path)
    pol = AutonomyPolicy(mode=AUTONOMOUS, allowlist=frozenset({"render"}))
    rec = _run(tmp_path, i, autonomous=True, decider=_decider("approve"), policy=pol)
    assert rec["status"] == "gated-human" and _prop(path, i)["status"] == "proposed"


def test_malformed_decider_reply_is_gated(tmp_path):
    path, i = _setup(tmp_path)
    rec = _run(tmp_path, i, autonomous=True, decider=lambda t, d: {"choice": "maybe", "rationale": ""})
    assert rec["status"] == "gated-human" and _prop(path, i)["status"] == "proposed"
