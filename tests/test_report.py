"""Tests du rapport markdown."""

from usine import db
from usine.report import generate_report
from usine.state import RunState


def test_rapport_vide():
    assert "Aucun run" in generate_report()


def test_rapport_avec_runs():
    state = RunState.create(category="finance", stack="webapp")
    state.label = "demo"
    state.status = "completed"
    state.host_port = 8100
    state.validation = {"passed": True}
    db.upsert_run(state)
    report = generate_report()
    assert state.run_id in report
    assert "Coût équiv. API" in report
    assert "Par stack" in report
    assert "webapp" in report
