"""Point d'entrée CLI : run / resume / status / report / list / clean / delete / open / stop
/ doctor / sdk-check."""

import argparse
import sys

from usine import __version__
from usine.logging_setup import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(prog="usine", description="AI App Factory v2")
    parser.add_argument("--version", action="version", version=f"usine {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Lance N runs de génération d'apps")
    p_run.add_argument("--count", type=int, default=1, help="Nombre d'apps à générer")
    p_run.add_argument("--parallel", type=int, default=None, help="Runs concurrents max")
    p_run.add_argument("--category", default=None, help="Catégorie d'idées imposée")
    p_run.add_argument("--stack", choices=["webapp", "api", "static"], default=None,
                       help="Template de stack imposé")
    p_run.add_argument("--budget", type=float, default=0.0,
                       help="Plafond de coût équiv. API du batch en $ (0 = aucun)")
    p_run.add_argument("--dry-run", action="store_true",
                       help="Affiche le plan d'exécution sans rien lancer")

    p_resume = sub.add_parser("resume", help="Reprend un run interrompu (ou tous : --all)")
    p_resume.add_argument("run_id", nargs="?", default=None)
    p_resume.add_argument("--all", action="store_true", dest="resume_all",
                          help="Reprend tous les runs interrompus/échoués")
    p_resume.add_argument("--force", action="store_true",
                          help="Reprend même si le run semble encore actif")

    p_report = sub.add_parser("report", help="Rapport markdown comparant les runs")
    p_report.add_argument("--json", action="store_true", dest="as_json",
                          help="Sortie JSON machine plutôt que markdown")

    sub.add_parser("list", help="Historique des runs")
    sub.add_parser("status", help="Runs en cours : étape, durée, coût, dernière activité")
    sub.add_parser("clean", help="Supprime les conteneurs/images usine-app-* orphelins")
    sub.add_parser("doctor", help="Diagnostic complet : Docker, disques, credentials, "
                                  "templates, CA, Chromium")

    p_delete = sub.add_parser("delete", help="Supprime un run (dossier + base)")
    p_delete.add_argument("run_id")
    p_delete.add_argument("--keep-ideas", action="store_true",
                          help="Conserve les idées du run dans l'anti-duplication")

    p_open = sub.add_parser("open", help="Builde et lance une app packagée sur son port hôte")
    p_open.add_argument("run_id")

    p_stop = sub.add_parser("stop", help="Arrête une app lancée via open")
    p_stop.add_argument("run_id")

    sub.add_parser("sdk-check", help="Valide l'auth SDK et le structured output")

    args = parser.parse_args()
    setup_logging()

    if args.command == "sdk-check":
        from usine.sdk_check import sdk_check
        return sdk_check()
    if args.command == "doctor":
        import asyncio

        from usine.doctor import run_doctor
        return asyncio.run(run_doctor())

    from usine import cli

    if args.command == "run":
        return cli.cmd_run(count=args.count, parallel=args.parallel, category=args.category,
                           stack=args.stack, budget=args.budget, dry_run=args.dry_run)
    if args.command == "resume":
        return cli.cmd_resume(args.run_id, resume_all=args.resume_all, force=args.force)
    if args.command == "report":
        return cli.cmd_report(as_json=args.as_json)
    if args.command == "list":
        return cli.cmd_list()
    if args.command == "status":
        return cli.cmd_status()
    if args.command == "clean":
        return cli.cmd_clean()
    if args.command == "delete":
        return cli.cmd_delete(args.run_id, keep_ideas=args.keep_ideas)
    if args.command == "open":
        return cli.cmd_open(args.run_id)
    if args.command == "stop":
        return cli.cmd_stop(args.run_id)

    parser.error(f"commande inconnue: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
