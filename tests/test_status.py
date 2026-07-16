"""Tests de la commande status — sans Docker : tout passe par la DB et state.json."""

from usine import db
from usine.cli import cmd_status
from usine.state import RunState


def test_status_sans_run(capsys):
    assert cmd_status() == 0
    assert "Aucun run en cours" in capsys.readouterr().out


def test_status_run_actif_montre_etape_et_derniere_ligne(capsys):
    state = RunState.create(category="test")
    state.label = "mon-app"
    state.step_start("discover")
    db.upsert_run(state)
    (state.run_dir / "run.log").write_text(
        "10:00:00 INFO démarrage\n10:00:01 INFO discover en cours", encoding="utf-8"
    )
    assert cmd_status() == 0
    out = capsys.readouterr().out
    assert state.run_id in out
    assert "discover" in out
    assert "discover en cours" in out  # dernière ligne du run.log
    assert "⏳" in out  # log frais → run considéré actif


def test_status_ignore_les_runs_termines(capsys):
    state = RunState.create()
    for step in ("discover", "product", "dev", "validate", "business", "package"):
        state.step_start(step)
        state.step_done(step)
    state.status = "completed"
    state.save()
    db.upsert_run(state)
    assert cmd_status() == 0
    assert "Aucun run en cours" in capsys.readouterr().out
