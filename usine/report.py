"""Rapport markdown comparant tous les runs (historique SQLite)."""

import time
from collections import defaultdict
from pathlib import Path

from usine import db
from usine.config import settings


def _fmt_duration(seconds: float) -> str:
    mins, secs = divmod(int(seconds or 0), 60)
    return f"{mins}m{secs:02d}s"


def _fmt_tokens(n: int) -> str:
    n = n or 0
    return f"{n / 1_000_000:.1f}M" if n >= 1_000_000 else f"{n // 1000}k"


def _status_label(r) -> str:
    if r["status"] == "running":
        log = Path(settings.output_dir) / f"run_{r['run_id']}" / "run.log"
        if not log.exists() or (time.time() - log.stat().st_mtime) > 300:
            return "💀 interrompu"
        return "⏳ en cours"
    return {"completed": "✅ terminé", "failed": "❌ échoué"}.get(r["status"], r["status"])


def generate_report() -> str:
    runs = db.list_runs()
    if not runs:
        return "Aucun run enregistré.\n"

    lines = [
        "# Usine v2 — Rapport des runs",
        "",
        "*Le coût est l'équivalent tarif API : rien n'est facturé, la conso est "
        "décomptée de l'abonnement.*",
        "",
        "| Run | App | Statut | Stack | Catégorie | Durée | Coût équiv. API "
        "| Tokens in/cache-r/out | Fix | Val. | App |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    total_cost = ok = 0
    by_stack: dict[str, list] = defaultdict(list)
    by_cat: dict[str, list] = defaultdict(list)
    for r in runs:
        validation = {None: "—", 1: "✅", 0: "❌"}[r["validation_passed"]]
        port = r["host_port"] if "host_port" in r.keys() else None
        cache_read = r["tokens_cache_read"] if "tokens_cache_read" in r.keys() else 0
        link = (f"[landing](run_{r['run_id']}/landing_page.html) · :{port}"
                if r["status"] == "completed" and port else "—")
        lines.append(
            f"| {r['run_id']} | {r['label'] or '—'} | {_status_label(r)} | {r['stack'] or '—'} "
            f"| {r['category'] or '—'} | {_fmt_duration(r['duration_s'])} "
            f"| ${r['cost_usd']:.2f} | {_fmt_tokens(r['tokens_in'])}/"
            f"{_fmt_tokens(cache_read)}/{_fmt_tokens(r['tokens_out'])} "
            f"| {r['fix_cycles']} | {validation} | {link} |"
        )
        total_cost += r["cost_usd"] or 0
        ok += int(r["status"] == "completed")
        if r["stack"]:
            by_stack[r["stack"]].append(r)
        if r["category"]:
            by_cat[r["category"]].append(r)

    lines += [
        "",
        f"**{len(runs)} runs** — {ok} réussis, {len(runs) - ok} autres · "
        f"coût total ${total_cost:.2f} équiv. API",
        "",
    ]

    def _agg_section(title: str, groups: dict[str, list]) -> None:
        if not groups:
            return
        lines.append(f"## Par {title}")
        lines.append("")
        lines.append(f"| {title.capitalize()} | Runs | Réussite | Coût moyen "
                     "| Durée moyenne | Fix moyens |")
        lines.append("|---|---|---|---|---|---|")
        for key, rows in sorted(groups.items()):
            done = [r for r in rows if r["status"] == "completed"]
            rate = f"{100 * len(done) // len(rows)}%"
            avg_cost = sum(r["cost_usd"] or 0 for r in rows) / len(rows)
            avg_dur = sum(r["duration_s"] or 0 for r in rows) / len(rows)
            avg_fix = sum(r["fix_cycles"] or 0 for r in rows) / len(rows)
            lines.append(f"| {key} | {len(rows)} | {rate} | ${avg_cost:.2f} "
                         f"| {_fmt_duration(avg_dur)} | {avg_fix:.1f} |")
        lines.append("")

    _agg_section("stack", by_stack)
    _agg_section("catégorie", by_cat)

    failed = [r for r in runs if r["status"] == "failed" and r["error"]]
    if failed:
        lines.append("## Échecs")
        for r in failed:
            lines.append(f"- **{r['run_id']}** ({r['label'] or '—'}) : {r['error'][:300]} — "
                         f"`resume {r['run_id']}` pour reprendre")
        lines.append("")
    return "\n".join(lines)


def write_report() -> Path:
    path = Path(settings.output_dir) / "report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(generate_report(), encoding="utf-8")
    return path
