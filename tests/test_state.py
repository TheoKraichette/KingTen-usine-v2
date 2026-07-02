"""Tests de la logique resume/état — le cœur que le pipeline exerce peu souvent."""

from usine.state import STEP_ORDER, RunState


def test_next_step_suit_l_ordre():
    state = RunState.create()
    assert state.next_step() == "discover"
    for step in STEP_ORDER:
        assert state.next_step() == step
        state.step_start(step)
        assert state.next_step() == step  # running n'est pas done → resume la ré-exécute
        state.step_done(step)
    assert state.next_step() is None


def test_kill_pendant_dev_rearme_dev():
    state = RunState.create()
    for step in ("discover", "product"):
        state.step_start(step)
        state.step_done(step)
    state.step_start("dev")  # kill ici : le statut reste running
    reloaded = RunState.load(state.run_id)
    assert reloaded.next_step() == "dev"


def test_echec_puis_rearmement():
    state = RunState.create()
    state.step_start("discover")
    state.step_failed("discover", "boom")
    assert state.status == "failed"
    assert state.next_step() == "discover"
    # ré-armement façon cmd_resume
    state.steps["discover"].status = "pending"
    state.steps["discover"].error = None
    state.status = "running"
    state.ended_at = None
    state.save()
    reloaded = RunState.load(state.run_id)
    assert reloaded.next_step() == "discover"
    assert reloaded.steps["discover"].error is None


def test_round_trip_state_json():
    state = RunState.create(category="finance", stack="webapp")
    state.idea = {"name": "Test", "one_liner": "pitch"}
    state.session_ids["dev"] = "sess-123"
    state.agent_summaries["dev"] = "résumé"
    state.smoke_hash = "abc"
    state.smoke_checks = 7
    state.save()
    reloaded = RunState.load(state.run_id)
    assert reloaded.category == "finance"
    assert reloaded.stack == "webapp"
    assert reloaded.session_ids["dev"] == "sess-123"
    assert reloaded.smoke_checks == 7
    assert reloaded.prompts_version  # empreinte calculée à la création
    assert reloaded.models["dev"]


def test_conso_cumulee_et_separee():
    state = RunState.create()
    state.step_start("dev")
    state.step_done("dev", cost_usd=1.5, input_tokens=100, cache_creation_tokens=200,
                    cache_read_tokens=5000, output_tokens=50)
    state.add_usage("dev", cost_usd=0.5, input_tokens=10, cache_read_tokens=1000)
    usage = state.total_usage
    assert state.total_cost_usd == 2.0
    assert usage["input"] == 110
    assert usage["cache_creation"] == 200
    assert usage["cache_read"] == 6000
    assert usage["output"] == 50


def test_vieux_state_json_sans_nouveaux_champs(tmp_path):
    """Un state.json d'avant les nouveaux champs doit encore se charger (defaults pydantic)."""
    state = RunState.create()
    data = state.model_dump()
    for legacy_missing in ("agent_summaries", "smoke_hash", "smoke_checks",
                           "prompts_version", "models"):
        data.pop(legacy_missing, None)
    for info in data["steps"].values():
        info.pop("cache_creation_tokens", None)
        info.pop("cache_read_tokens", None)
    import json
    (state.run_dir / "state.json").write_text(json.dumps(data), encoding="utf-8")
    reloaded = RunState.load(state.run_id)
    assert reloaded.smoke_checks == 0
    assert reloaded.total_usage["cache_read"] == 0
