"""Pipeline d'un run : discover → product → dev → validate ⇄ fix → business → package.

business ne dépend que de la spec : il est lancé en tâche parallèle dès le début du dev
et rejoint avant package (≈ 1-2 min de wall-clock gagnées par run).
"""

import asyncio
import contextlib
import shutil

from usine import db
from usine.agents import (
    agent_business, agent_dev, agent_discover, agent_fix, agent_product, smoke_file_hash,
)
from usine.config import settings
from usine.sdk import StepUsage
from usine.state import RunState, dump_json, utcnow
from usine.validation import ValidationFailed, cleanup, ensure_infra, validate_app


def _usage_kwargs(usage: StepUsage) -> dict:
    return {
        "cost_usd": usage.cost_usd,
        "input_tokens": usage.input_tokens,
        "cache_creation_tokens": usage.cache_creation_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "output_tokens": usage.output_tokens,
    }


async def step_validate(state: RunState, logger) -> None:
    """Boucle validate ⇄ fix : erreurs concrètes, max N cycles, nettoyage garanti.

    Les échecs d'INFRASTRUCTURE (daemon, réseau, timeout de build) court-circuitent
    la boucle fix : l'agent ne peut pas les corriger, le run échoue avec un rapport clair.
    """
    await ensure_infra(logger)
    cycle = state.fix_cycles  # reprise : on repart du cycle où on s'était arrêté
    try:
        while True:
            report = await validate_app(state, cycle, logger)
            state.validation = report
            dump_json(state.run_dir / "validation_report.json", report)
            state.save()
            if report["passed"]:
                logger.info("🎉 validation réussie (cycle %d, %.0fs)", cycle, report["duration_s"])
                return
            if report.get("infra_error"):
                failed = [s for s in report["stages"] if not s["passed"]]
                detail = failed[-1]["detail"][-400:] if failed else "?"
                raise RuntimeError(f"échec d'infrastructure (pas un bug de l'app) : {detail}")
            if cycle >= settings.max_fix_cycles:
                raise ValidationFailed(
                    f"validation toujours en échec après {cycle} cycle(s) de fix — "
                    f"voir validation_report.json"
                )
            cycle += 1
            usage = await agent_fix(state, report, cycle, logger)
            state.fix_cycles = cycle
            state.add_usage("validate", **_usage_kwargs(usage))
            new_hash = smoke_file_hash(state.code_dir)
            if new_hash != state.smoke_hash:
                logger.warning("⚠️ fix#%d a modifié tests/smoke.py — toute baisse du nombre "
                               "de checks fera échouer la validation", cycle)
                state.smoke_hash = new_hash
                state.save()
    finally:
        await cleanup(state.run_id, remove_image=True)


async def step_package(state: RunState, logger) -> None:
    """Assemble output/run_<id>/ : compose de l'app, landing page, assets, README."""
    port = db.allocate_port(state.run_id)
    state.host_port = port
    state.save()

    # Artefacts de dev à ne pas livrer
    for pycache in state.code_dir.rglob("__pycache__"):
        shutil.rmtree(pycache, ignore_errors=True)
    (state.code_dir / "app.db").unlink(missing_ok=True)

    (state.code_dir / "docker-compose.yml").write_text(
        f'services:\n  app:\n    build: .\n    ports:\n      - "{port}:{settings.app_port}"\n',
        encoding="utf-8",
    )

    biz = state.business or {}
    if biz.get("landing_page_html"):
        (state.run_dir / "landing_page.html").write_text(
            biz["landing_page_html"], encoding="utf-8"
        )
    dump_json(state.run_dir / "business.json",
              {k: v for k, v in biz.items() if k != "landing_page_html"})

    idea = state.idea or {}
    validation = state.validation or {}
    readme = f"""# {idea.get('name', state.label)} — {biz.get('tagline', '')}

{idea.get('one_liner', '')}

**Stack** : `{state.stack}` · **Validation** : {'✅ passée' if validation.get('passed') else '❌ échouée'} (cycle {validation.get('cycle', 0)}) · **Généré le** : {state.created_at}

## Lancer l'app

```bash
cd code
docker compose up --build
# → http://localhost:{port}
```

(ou depuis l'usine : `docker compose run --rm usine open {state.run_id}`)

## Contenu du dossier

- `code/` — l'application complète (Dockerfile + docker-compose inclus)
- `landing_page.html` — page marketing standalone
- `business.json` — tagline, pricing, SEO
- `validation_report.json` — résultats détaillés des tests réels
- `ideas.json` — les 5 idées du discover
- `state.json` — état complet du pipeline
- `run.log` — journal du run
"""
    (state.run_dir / "README.md").write_text(readme, encoding="utf-8")
    logger.info("📦 packagé — app sur le port hôte %d", port)


_STEPS = {
    "discover": agent_discover,
    "product": agent_product,
    "dev": agent_dev,
    "validate": step_validate,
    "business": agent_business,
    "package": step_package,
}


def _write_failure_report(state: RunState, step: str, error: str) -> None:
    lines = [
        f"# Run {state.run_id} ({state.label or 'sans nom'}) — ÉCHEC",
        "",
        f"- **Étape échouée** : {step}",
        f"- **Erreur** : {error}",
        f"- **Cycles de fix** : {state.fix_cycles}",
        "",
        "## Pour diagnostiquer",
        "- `run.log` — journal complet du run",
    ]
    if state.validation:
        lines.append("- `validation_report.json` — dernier rapport de validation (erreurs concrètes)")
        for s in state.validation.get("stages", []):
            if not s.get("passed"):
                lines.append(f"  - ❌ {s['name']}: {s.get('detail', '')[:200]}")
    lines.append(f"\nReprendre : `docker compose run --rm usine resume {state.run_id}`")
    (state.run_dir / "RAPPORT_ECHEC.md").write_text("\n".join(lines), encoding="utf-8")


async def execute_run(state: RunState, logger) -> RunState:
    """Exécute (ou reprend) un run étape par étape. N'élève jamais : retourne l'état final."""
    db.upsert_run(state)
    biz_task: asyncio.Task | None = None

    async def _business_bg():
        state.step_start("business")
        try:
            usage = await agent_business(state, logger)
            state.step_done("business", **_usage_kwargs(usage))
            db.upsert_run(state)
            return None
        except Exception as e:  # noqa: BLE001 — rejoint et traité dans la boucle principale
            state.step_failed("business", str(e))
            return e

    async def _fail(step: str, error: Exception) -> RunState:
        logger.error("💥 étape %s échouée : %s", step, error)
        usage = getattr(error, "usage", None)
        if isinstance(usage, StepUsage):
            state.add_usage(step, **_usage_kwargs(usage))
            if usage.session_id:
                state.session_ids[step] = usage.session_id
        state.step_failed(step, str(error))
        _write_failure_report(state, step, str(error))
        db.upsert_run(state)
        if biz_task is not None and not biz_task.done():
            with contextlib.suppress(Exception):
                await biz_task  # déjà payé — on le laisse finir proprement
        return state

    while (step := state.next_step()) is not None:
        if settings.max_cost_per_run and state.total_cost_usd >= settings.max_cost_per_run:
            return await _fail(step, RuntimeError(
                f"budget du run dépassé (${state.total_cost_usd:.2f} ≥ "
                f"${settings.max_cost_per_run:.2f}) — reprendre avec `resume` après arbitrage"
            ))

        if step == "business" and biz_task is not None:
            err = await biz_task
            biz_task = None
            if err:
                return await _fail("business", err)
            continue

        logger.info("── étape %s ──", step)

        # business ne dépend que de la spec : lancé en parallèle dès le début du dev
        if step == "dev" and state.steps["business"].status == "pending":
            biz_task = asyncio.create_task(_business_bg())

        state.step_start(step)
        db.upsert_run(state)
        try:
            usage = await _STEPS[step](state, logger)
        except Exception as e:  # noqa: BLE001 — un run échoué ne doit pas bloquer les autres
            return await _fail(step, e)
        if isinstance(usage, StepUsage):
            state.step_done(step, **_usage_kwargs(usage))
        else:
            state.step_done(step)
        db.upsert_run(state)

    state.status = "completed"
    state.ended_at = utcnow()
    state.save()
    db.upsert_run(state)
    usage = state.total_usage
    mins, secs = divmod(int(state.duration_s), 60)
    logger.info("🏁 TERMINÉ — %s en %dm%02ds, %d fix, $%.2f équiv. API, "
                "%dk in / %dk cache-r / %dk out — port %s",
                state.label, mins, secs, state.fix_cycles, state.total_cost_usd,
                usage["input"] // 1000, usage["cache_read"] // 1000,
                usage["output"] // 1000, state.host_port)
    return state
