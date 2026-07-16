"""Commandes CLI : run, resume, status, report, list, clean, delete, open, stop."""

import asyncio
import json
import logging
import random
import shutil
import time
from pathlib import Path

from usine import db
from usine.config import settings
from usine.doctor import UsineNotReady, preflight
from usine.logging_setup import run_logger
from usine.pipeline import execute_run
from usine.prompts import CATEGORIES
from usine.report import generate_report, write_report
from usine.state import RunState
from usine.validation import _docker, container_name, sweep_orphans

logger = logging.getLogger(__name__)


def _is_stale(run_id: str, threshold: int = 300) -> bool:
    """Un run est présumé mort si son run.log n'a pas bougé depuis `threshold` secondes."""
    log = Path(settings.output_dir) / f"run_{run_id}" / "run.log"
    return not log.exists() or (time.time() - log.stat().st_mtime) > threshold


# ── run ──────────────────────────────────────────────────────
async def _run_many(count: int, parallel: int, category: str | None, stack: str | None,
                    budget: float) -> list[RunState]:
    await preflight(logger)
    await sweep_orphans(logger)
    sem = asyncio.Semaphore(parallel)
    spent = {"usd": 0.0}
    # Catégories distinctes par run concurrent : limite les idées dupliquées
    # entre discovers simultanés
    cats = random.sample(CATEGORIES, k=min(count, len(CATEGORIES)))

    async def one(idx: int) -> RunState | None:
        if idx and idx < parallel:
            await asyncio.sleep(idx * 3)  # décale les discovers de la première vague
        async with sem:
            if budget and spent["usd"] >= budget:
                logger.warning("⛔ run %d/%d non lancé : budget batch atteint ($%.2f ≥ $%.2f)",
                               idx + 1, count, spent["usd"], budget)
                return None
            state = RunState.create(
                category=category or cats[idx % len(cats)], stack=stack
            )
            rlog = run_logger(state.run_id, state.run_dir)
            rlog.info("🚀 run %d/%d démarré (catégorie: %s, stack: %s)",
                      idx + 1, count, state.category, state.stack or "au choix de product")
            result = await execute_run(state, rlog)
            spent["usd"] += result.total_cost_usd
            return result

    results = await asyncio.gather(*(one(i) for i in range(count)), return_exceptions=True)
    states: list[RunState] = []
    for i, res in enumerate(results):
        if isinstance(res, BaseException):
            logger.error("💥 run %d: erreur inattendue de l'usine: %r", i + 1, res)
        elif res is not None:
            states.append(res)
    return states


def cmd_run(count: int, parallel: int | None, category: str | None, stack: str | None,
            budget: float = 0.0, dry_run: bool = False) -> int:
    parallel = parallel or settings.parallel
    if dry_run:
        cats = category or f"tirées de {len(CATEGORIES)} catégories (distinctes par run)"
        models = (f"discover={settings.model_discover} product={settings.model_product} "
                  f"dev={settings.model_dev} fix={settings.model_fix} "
                  f"business={settings.model_business}")
        timeouts = (f"discover={settings.timeout_discover} product={settings.timeout_product} "
                    f"dev={settings.timeout_dev} fix={settings.timeout_fix} "
                    f"validate={settings.timeout_validate}")
        cap = f"${settings.max_cost_per_run:.2f}" if settings.max_cost_per_run else "aucun"
        print(f"""Plan d'exécution (--dry-run, rien n'est lancé) :
  runs          : {count} (parallélisme {parallel})
  catégorie     : {cats}
  stack         : {stack or 'au choix de l’agent product'}
  modèles       : {models}
  timeouts (s)  : {timeouts}
  budget batch  : {f'${budget:.2f}' if budget else 'aucun'} · plafond/run : {cap}
  estimation    : ~$2.5-5.5 équiv. API et ~15-40 min par run (constaté sur les runs passés)""")
        return 0
    logger.info("═══ Usine v2 — %d run(s), parallélisme %d%s ═══",
                count, parallel, f", budget ${budget:.2f}" if budget else "")
    try:
        states = asyncio.run(_run_many(count, parallel, category, stack, budget))
    except UsineNotReady as e:
        logger.error("⛔ préflight échoué — aucun run lancé : %s", e)
        return 2

    logger.info("")
    logger.info("═══ Bilan ═══")
    ok = 0
    for s in states:
        if s.status == "completed":
            ok += 1
            logger.info("✅ %s (%s) — port %s — output/run_%s/",
                        s.run_id, s.label, s.host_port, s.run_id)
        else:
            failed_step = next((n for n, i in s.steps.items() if i.status == "failed"), "?")
            logger.info("❌ %s (%s) — échec à l'étape %s — voir output/run_%s/RAPPORT_ECHEC.md",
                        s.run_id, s.label or "sans nom", failed_step, s.run_id)
    path = write_report()
    logger.info("📊 rapport : %s", path)
    return 0 if ok == len(states) and states else 1


# ── resume ───────────────────────────────────────────────────
def _resume_one(run_id: str, force: bool = False) -> RunState | None:
    try:
        state = RunState.load(run_id)
    except FileNotFoundError:
        logger.error("run %s introuvable (pas de output/run_%s/state.json)", run_id, run_id)
        return None
    if state.status == "completed":
        logger.info("run %s déjà terminé — rien à reprendre", run_id)
        return state
    if state.status == "running" and not _is_stale(run_id) and not force:
        logger.error("run %s semble ENCORE EN COURS (run.log actif il y a < 5 min) — "
                     "un double resume détruirait ses conteneurs de test. Forcer : --force", run_id)
        return None
    step = state.next_step()
    if step:
        state.steps[step].status = "pending"
        state.steps[step].error = None
        state.status = "running"
        state.ended_at = None
        state.save()
    rlog = run_logger(state.run_id, state.run_dir)
    rlog.info("🔄 reprise à l'étape «%s»", step)
    try:
        return asyncio.run(_resume_async(state, rlog))
    except UsineNotReady as e:
        logger.error("⛔ préflight échoué — reprise annulée : %s", e)
        return None


async def _resume_async(state: RunState, rlog) -> RunState:
    await preflight(logger)
    await sweep_orphans(logger)
    return await execute_run(state, rlog)


def cmd_resume(run_id: str | None, resume_all: bool = False, force: bool = False,
               parallel: int | None = None) -> int:
    if resume_all:
        candidates = [
            r["run_id"] for r in db.list_runs()
            if r["status"] in ("running", "failed") and _is_stale(r["run_id"])
            and (Path(settings.output_dir) / f"run_{r['run_id']}" / "state.json").exists()
        ]
        if not candidates:
            logger.info("aucun run interrompu à reprendre")
            return 0
        logger.info("🔄 reprise de %d run(s) : %s", len(candidates), ", ".join(candidates))
        ok = 0
        for rid in candidates:  # séquentiel : chaque resume gère déjà son propre pipeline
            final = _resume_one(rid, force=force)
            ok += int(final is not None and final.status == "completed")
        write_report()
        return 0 if ok == len(candidates) else 1
    if not run_id:
        logger.error("usage: resume <run_id> | resume --all")
        return 2
    final = _resume_one(run_id, force=force)
    write_report()
    return 0 if final is not None and final.status == "completed" else 1


# ── report / list ────────────────────────────────────────────
def cmd_report(as_json: bool = False) -> int:
    if as_json:
        rows = [dict(r) for r in db.list_runs()]
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return 0
    print(generate_report())
    path = write_report()
    logger.info("📊 rapport écrit : %s", path)
    return 0


def cmd_list() -> int:
    runs = db.list_runs()
    if not runs:
        print("Aucun run.")
        return 0
    for r in runs:
        status = r["status"]
        if status == "running" and _is_stale(r["run_id"]):
            status = "💀 interrompu (resumable)"
        print(f"{r['run_id']}  {r['created_at']}  {status:<26} "
              f"{(r['label'] or '—'):<32} {r['stack'] or '—':<7} ${r['cost_usd'] or 0:.2f}")
    return 0


# ── status ───────────────────────────────────────────────────
def _last_log_line(log: Path) -> str:
    if not log.exists():
        return ""
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return ""
    return lines[-1][:150] if lines else ""


def cmd_status() -> int:
    """Vue temps réel des runs en cours : étape, durée, coût, dernière activité du log."""
    rows = [r for r in db.list_runs() if r["status"] == "running"]
    if not rows:
        print("Aucun run en cours.")
        return 0
    now = time.time()
    for r in rows:
        run_id = r["run_id"]
        log = Path(settings.output_dir) / f"run_{run_id}" / "run.log"
        try:
            state = RunState.load(run_id)
            step = next((n for n, i in state.steps.items() if i.status == "running"),
                        state.next_step() or "?")
            elapsed, cost = state.duration_s, state.total_cost_usd
        except FileNotFoundError:
            step, elapsed, cost = "?", r["duration_s"] or 0.0, r["cost_usd"] or 0.0
        if not log.exists():
            icon, live = "⏳", "pas encore de log"
        elif (age := int(now - log.stat().st_mtime)) > 300:
            icon, live = "💀", f"log muet depuis {age // 60} min — interrompu ? (resume {run_id})"
        else:
            icon, live = "⏳", f"log actif il y a {age}s"
        print(f"{icon} {run_id}  {(r['label'] or '—'):<32} étape {step:<9} "
              f"{int(elapsed) // 60:>3}m  ${cost:.2f}  {live}")
        if last := _last_log_line(log):
            print(f"   └ {last}")
    return 0


# ── clean / delete ───────────────────────────────────────────
def cmd_clean() -> int:
    asyncio.run(sweep_orphans(logger))
    logger.info("🧹 nettoyage terminé (conteneurs et images usine-app-* orphelins)")
    return 0


def cmd_delete(run_id: str, keep_ideas: bool = False) -> int:
    run_dir = Path(settings.output_dir) / f"run_{run_id}"
    if not run_dir.exists():
        logger.error("run %s introuvable", run_id)
        return 2
    asyncio.run(_delete_docker(run_id))
    shutil.rmtree(run_dir, ignore_errors=True)
    db.delete_run(run_id, keep_ideas=keep_ideas)
    logger.info("🗑️ run %s supprimé (dossier + DB%s)", run_id,
                ", idées conservées" if keep_ideas else "")
    return 0


async def _delete_docker(run_id: str) -> None:
    await _docker("rm", "-f", container_name(run_id), f"usine-live-{run_id}", timeout=60)
    await _docker("rmi", "-f", f"usine-app-{run_id}:latest", f"usine-live-{run_id}:latest",
                  timeout=60)


# ── open / stop ──────────────────────────────────────────────
def cmd_open(run_id: str) -> int:
    try:
        state = RunState.load(run_id)
    except FileNotFoundError:
        logger.error("run %s introuvable", run_id)
        return 2
    port = state.host_port or db.allocate_port(run_id)
    return asyncio.run(_open_async(state, port))


async def _open_async(state: RunState, port: int) -> int:
    name = f"usine-live-{state.run_id}"
    tag = f"usine-live-{state.run_id}:latest"
    logger.info("🔨 build de %s...", state.label or state.run_id)
    code, out = await _docker("build", "-t", tag, str(state.code_dir), timeout=600)
    if code != 0:
        logger.error("build échoué : %s", out[-500:])
        return 1
    await _docker("rm", "-f", name, timeout=60)
    code, out = await _docker(
        "run", "-d", "--name", name, "--label", "usine.live=1",
        "-p", f"{port}:{settings.app_port}", tag, timeout=120,
    )
    if code != 0:
        logger.error("run échoué : %s", out[-500:])
        return 1
    logger.info("🌐 %s en ligne → http://localhost:%d   (arrêter : usine stop %s)",
                state.label or state.run_id, port, state.run_id)
    return 0


def cmd_stop(run_id: str) -> int:
    async def _stop():
        await _docker("rm", "-f", f"usine-live-{run_id}", timeout=60)
        await _docker("rmi", "-f", f"usine-live-{run_id}:latest", timeout=60)
    asyncio.run(_stop())
    logger.info("⏹️ usine-live-%s arrêté", run_id)
    return 0
