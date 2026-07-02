"""Tests SQLite : ports, anti-duplication, historique."""

from usine import db
from usine.state import RunState


def test_allocate_port_monotone_et_idempotent():
    p1 = db.allocate_port("run-a")
    p2 = db.allocate_port("run-b")
    assert p2 == p1 + 1
    assert db.allocate_port("run-a") == p1  # idempotent par run_id


def test_upsert_et_list_runs():
    state = RunState.create(category="finance", stack="api")
    state.label = "testapp"
    db.upsert_run(state)
    state.step_start("discover")
    state.step_done("discover", cost_usd=0.1, input_tokens=10, cache_read_tokens=999)
    db.upsert_run(state)
    runs = db.list_runs()
    assert len(runs) == 1
    row = runs[0]
    assert row["run_id"] == state.run_id
    assert row["label"] == "testapp"
    assert row["tokens_cache_read"] == 999
    assert row["prompts_version"] == state.prompts_version


def test_past_ideas_filtre_et_borne():
    db.save_ideas("r1", [
        {"name": "Alpha", "one_liner": "a", "category": "finance", "score": 80},
        {"name": "Beta", "one_liner": "b", "category": "pets", "score": 70},
    ], selected_name="Alpha")
    db.save_ideas("r2", [
        {"name": "Gamma", "one_liner": "c", "category": "finance", "score": 60},
    ], selected_name="Gamma")
    # sélectionnées (Alpha, Gamma) + même catégorie que le run courant (finance)
    past = db.past_ideas("finance")
    names = " ".join(past)
    assert "Alpha" in names and "Gamma" in names
    assert "Beta" not in names  # non retenue et autre catégorie
    past_all = db.past_ideas("pets")
    assert any("Beta" in p for p in past_all)


def test_known_slugs():
    db.save_ideas("r1", [{"name": "Scope Log!", "one_liner": "x", "category": "c", "score": 1}],
                  selected_name="Scope Log!")
    assert "scope-log" in db.known_slugs()


def test_delete_run():
    state = RunState.create()
    db.upsert_run(state)
    db.allocate_port(state.run_id)
    db.save_ideas(state.run_id, [{"name": "X", "one_liner": "y", "category": "c", "score": 1}],
                  selected_name="X")
    db.delete_run(state.run_id)
    assert all(r["run_id"] != state.run_id for r in db.list_runs())
    assert db.past_ideas() == []
