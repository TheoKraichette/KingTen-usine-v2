# CLAUDE.md

Guide pour Claude Code lors du travail sur ce repo.

## Le projet

Usine v2 — AI App Factory : génère des micro-SaaS complets via le Claude Agent SDK Python, avec **validation mécanique réelle** (docker build/run + healthcheck + smoke HTTP + Playwright). Réécriture from scratch de `../usine_v1` (à ne pas modifier).

## Commandes

```powershell
docker compose build                                   # rebuild l'image usine
docker compose run --rm usine doctor                   # diagnostic complet de l'environnement
docker compose run --rm usine sdk-check                # test auth SDK + structured output
docker compose run --rm usine run --count 2 --parallel 2
docker compose run --rm usine status                   # runs en cours
docker compose run --rm usine resume <run_id>
docker compose run --rm usine report
docker compose run --rm usine list
.\check.ps1                                            # qualité : ruff + pytest dans Docker
```

Tout tourne dans Docker — il n'y a **pas de Python sur l'hôte** (bloqué par une politique Windows). Pour tester du code Python : `docker compose run --rm --entrypoint python usine -c "..."`.

Le source (`usine/`, `templates/`, `tests/`) est **monté en volume** dans le conteneur : les modifs de code sont actives sans rebuild. Rebuild uniquement si `requirements.txt`, le `Dockerfile` ou les versions épinglées changent. Lancer `.\check.ps1` (ruff + pytest) avant tout commit.

## Architecture

- `usine/__main__.py` → `cli.py` — CLI et runner parallèle (asyncio + sémaphore, catégories distinctes par run concurrent, gather avec isolation des échecs).
- `usine/pipeline.py` — `execute_run()` : boucle sur `state.next_step()`, chaque étape persiste `state.json` + upsert SQLite. La boucle validate⇄fix vit dans `step_validate()` (max `USINE_MAX_FIX_CYCLES`).
- `usine/agents.py` + `usine/prompts.py` — les 5 agents. discover/product/business = `run_structured()` (structured output, schémas dans `schemas.py`) ; dev/fix = `run_agentic()` (bypassPermissions, cwd = `output/run_<id>/code`).
- `usine/sdk.py` — wrappers SDK : heartbeat, timeout par étape (non retryable), retry/backoff sur erreurs transitoires, extraction de la conso (`StepUsage`).
- `usine/validation.py` — le cœur v2 : build → run (conteneur frère sur `usine-net`, joint par nom) → healthcheck → smoke génériques → `tests/smoke.py` de l'app → cleanup en `finally`. Aucun jugement de Claude.
- `usine/doctor.py` — `doctor` (diagnostic complet, checks purs testables) + `preflight()` appelé avant run/resume (disques, daemon, credentials) : échec AVANT de dépenser des tokens.
- `usine/state.py` — `RunState` pydantic, persisté à chaque étape ; `next_step()` pilote le resume.
- `usine/db.py` — SQLite usine : historique des runs, idées (anti-duplication du discover), allocation de ports hôte uniques.
- `templates/{webapp,api,static}/` — squelettes copiés au premier dev SEULEMENT (jamais recopiés au resume : le code de l'agent est préservé). **Contrat template** : port 8000, `/health` → strictement 200 (webapp/api), Dockerfile fonctionnel tel quel, `tests/smoke.py` (argv[1]=base_url, exit 0/1, une ligne OK/FAIL par check, minimum 2 + nb de parcours critiques — toute baisse du compte est un échec).
- `tests/` (racine) — tests unitaires de l'usine elle-même : `docker compose run --rm --entrypoint python usine -m pytest tests -q`. À faire tourner après toute modif de state/db/report.

## Points d'attention

- **Lire la doc officielle du Agent SDK avant de changer les appels** (`code.claude.com/docs/en/agent-sdk`) — ne pas coder les signatures de mémoire.
- Les CA d'entreprise (`certs/*.crt`) sont indispensables dans l'image : la machine est derrière un proxy d'inspection TLS (WatchGuard/Cloudflare Gateway). Sans eux, téléchargements Node/Playwright et appels API échouent en `SELF_SIGNED_CERT_IN_CHAIN`.
- `entrypoint.sh` tourne en root (chmod du socket Docker, copie des credentials) puis `gosu usine` — le mode `bypassPermissions` du SDK refuse root.
- Les apps générées doivent rester **offline** (pas de CDN, pas d'API externe) — c'est ce qui rend la validation hermétique.
- Un run échoué écrit `RAPPORT_ECHEC.md` et ne doit jamais faire tomber les autres (tout est catché dans `execute_run`).
- Logs : UTF-8 partout, un `run.log` par run + console globale préfixée `[run_id]`.

## Langue

Logs, commentaires, docs et UI des apps générées : français. Le code (identifiants) : anglais.
