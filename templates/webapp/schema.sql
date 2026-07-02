-- Schéma SQLite — complété par l'agent dev selon les entities de spec.json.
-- Tout en CREATE TABLE IF NOT EXISTS : le script est rejoué à chaque démarrage.

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
