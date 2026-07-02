"""Historique des runs et anti-duplication d'idées — SQLite locale de l'usine."""

import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from usine.config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    label TEXT,
    status TEXT,
    stack TEXT,
    category TEXT,
    created_at TEXT,
    ended_at TEXT,
    duration_s REAL DEFAULT 0,
    cost_usd REAL DEFAULT 0,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    fix_cycles INTEGER DEFAULT 0,
    validation_passed INTEGER,
    error TEXT
);
CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    name TEXT,
    one_liner TEXT,
    category TEXT,
    score INTEGER,
    selected INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ports (
    port INTEGER PRIMARY KEY,
    run_id TEXT
);
"""

# Colonnes ajoutées après la v2.0 — appliquées en douceur sur les bases existantes
_MIGRATIONS = [
    "ALTER TABLE runs ADD COLUMN tokens_cache_creation INTEGER DEFAULT 0",
    "ALTER TABLE runs ADD COLUMN tokens_cache_read INTEGER DEFAULT 0",
    "ALTER TABLE runs ADD COLUMN host_port INTEGER",
    "ALTER TABLE runs ADD COLUMN prompts_version TEXT",
    "ALTER TABLE runs ADD COLUMN models TEXT",
]

PORT_BASE = 8100


@contextmanager
def _conn():
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(Path(settings.data_dir) / "usine.db", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    for migration in _MIGRATIONS:
        try:
            conn.execute(migration)
        except sqlite3.OperationalError:
            pass  # colonne déjà présente
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_run(state) -> None:
    """Synchronise l'état d'un run dans la table runs."""
    usage = state.total_usage
    error = None
    for info in state.steps.values():
        if info.error:
            error = info.error
    with _conn() as c:
        c.execute(
            """INSERT INTO runs (run_id, label, status, stack, category, created_at,
                                 ended_at, duration_s, cost_usd, tokens_in,
                                 tokens_cache_creation, tokens_cache_read, tokens_out,
                                 fix_cycles, validation_passed, error, host_port,
                                 prompts_version, models)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(run_id) DO UPDATE SET
                 label=excluded.label, status=excluded.status, stack=excluded.stack,
                 category=excluded.category, ended_at=excluded.ended_at,
                 duration_s=excluded.duration_s, cost_usd=excluded.cost_usd,
                 tokens_in=excluded.tokens_in,
                 tokens_cache_creation=excluded.tokens_cache_creation,
                 tokens_cache_read=excluded.tokens_cache_read,
                 tokens_out=excluded.tokens_out,
                 fix_cycles=excluded.fix_cycles,
                 validation_passed=excluded.validation_passed, error=excluded.error,
                 host_port=excluded.host_port,
                 prompts_version=excluded.prompts_version, models=excluded.models""",
            (state.run_id, state.label, state.status, state.stack, state.category,
             state.created_at, state.ended_at, state.duration_s, state.total_cost_usd,
             usage["input"], usage["cache_creation"], usage["cache_read"], usage["output"],
             state.fix_cycles,
             None if state.validation is None else int(bool(state.validation.get("passed"))),
             error, state.host_port, state.prompts_version,
             json.dumps(state.models) if state.models else None),
        )


def save_ideas(run_id: str, ideas: list[dict], selected_name: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM ideas WHERE run_id = ?", (run_id,))
        for idea in ideas:
            c.execute(
                "INSERT INTO ideas (run_id, name, one_liner, category, score, selected) "
                "VALUES (?,?,?,?,?,?)",
                (run_id, idea.get("name"), idea.get("one_liner"), idea.get("category"),
                 idea.get("score"), int(idea.get("name") == selected_name)),
            )


def past_ideas(category: str | None = None, limit: int = 60) -> list[str]:
    """Idées à exclure du discover : les sélectionnées + celles de la même catégorie, bornées."""
    query = "SELECT name, one_liner FROM ideas WHERE selected = 1"
    args: list = []
    if category:
        query += " OR category = ?"
        args.append(category)
    query += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        rows = c.execute(query, args).fetchall()
    return [f"{r['name']} — {r['one_liner']}" for r in rows]


def known_slugs() -> set[str]:
    """Slugs de toutes les idées déjà en base (vérification mécanique anti-duplication)."""
    with _conn() as c:
        rows = c.execute("SELECT name FROM ideas").fetchall()
    return {re.sub(r"[^a-z0-9]+", "-", (r["name"] or "").lower()).strip("-")[:30] for r in rows}


def allocate_port(run_id: str) -> int:
    """Réserve un port hôte unique (atomique, sûr entre plusieurs processus usine)."""
    with _conn() as c:
        row = c.execute("SELECT port FROM ports WHERE run_id = ?", (run_id,)).fetchone()
        if row:
            return row["port"]
        for _ in range(5):
            try:
                row = c.execute(
                    "INSERT INTO ports (port, run_id) "
                    "SELECT COALESCE(MAX(port), ?) + 1, ? FROM ports RETURNING port",
                    (PORT_BASE - 1, run_id),
                ).fetchone()
                return row["port"]
            except sqlite3.IntegrityError:
                continue  # course avec un autre processus — on retente
    raise RuntimeError("allocation de port impossible après 5 tentatives")


def delete_run(run_id: str, *, keep_ideas: bool = False) -> None:
    with _conn() as c:
        c.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
        c.execute("DELETE FROM ports WHERE run_id = ?", (run_id,))
        if not keep_ideas:
            c.execute("DELETE FROM ideas WHERE run_id = ?", (run_id,))


def list_runs() -> list[sqlite3.Row]:
    with _conn() as c:
        return c.execute("SELECT * FROM runs ORDER BY created_at").fetchall()
