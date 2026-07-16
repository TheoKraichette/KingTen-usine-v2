"""Diagnostic de l'environnement : `doctor` (complet) et `preflight()` (avant run/resume).

Chaque check retourne un niveau (ok/warn/fail), un détail et un remède actionnable.
`doctor` ne modifie rien : diagnostic pur. `preflight()` lève UsineNotReady sur les
échecs bloquants pour échouer AVANT de dépenser des tokens — un disque C: saturé
fait crasher containerd (SIGBUS) en plein build et coûte un run entier.
"""

import asyncio
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from usine import db
from usine.config import settings
from usine.validation import _docker

OK, WARN, FAIL = "ok", "warn", "fail"
_ICONS = {OK: "✅", WARN: "⚠️", FAIL: "❌"}

_REMEDY_HOST_DISK = ("libérer le disque hôte puis compacter le vhdx Docker "
                     "(procédure dans le README, section « Exploitation »)")
_REMEDY_VM_DISK = "docker builder prune / docker image prune (espace interne de la VM Docker)"
_REMEDY_CREDS = ("se reconnecter sur l'hôte (claude login), puis supprimer "
                 "data/claude/.credentials.json pour re-seeder au prochain lancement")


@dataclass
class Check:
    name: str
    level: str
    detail: str = ""
    remedy: str = ""


class UsineNotReady(RuntimeError):
    """Préflight bloquant : l'environnement ne permet pas de lancer des runs."""


# ── checks purs (testables sans Docker) ─────────────────────────────


def check_disk(path: str, label: str, warn_gb: float, fail_gb: float, remedy: str) -> Check:
    try:
        usage = shutil.disk_usage(path)
    except OSError as e:
        return Check(label, FAIL, f"illisible ({e})", remedy)
    free_gb = usage.free / 1e9
    detail = f"{free_gb:.1f} Go libres / {usage.total / 1e9:.0f} Go"
    if free_gb < fail_gb:
        return Check(label, FAIL, f"{detail} — seuil bloquant {fail_gb:g} Go", remedy)
    if free_gb < warn_gb:
        return Check(label, WARN, f"{detail} — seuil d'alerte {warn_gb:g} Go", remedy)
    return Check(label, OK, detail)


def check_credentials(path: Path, now_ms: int | None = None) -> Check:
    name = "credentials Claude"
    if not path.is_file():
        return Check(name, FAIL, f"{path} absent", _REMEDY_CREDS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return Check(name, FAIL, f"illisibles ({e})", _REMEDY_CREDS)
    oauth = data.get("claudeAiOauth") or {}
    expires_at = oauth.get("expiresAt") or 0
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if expires_at and expires_at < now_ms:
        if oauth.get("refreshToken"):
            return Check(name, WARN,
                         "access token expiré — refresh automatique au prochain appel SDK")
        return Check(name, FAIL, "token expiré sans refresh token", _REMEDY_CREDS)
    if expires_at:
        return Check(name, OK, f"token valide encore {(expires_at - now_ms) / 3_600_000:.0f} h")
    return Check(name, OK, "présents")


# Contrat minimal de chaque template (voir CLAUDE.md) — vérifié fichier par fichier
_TEMPLATE_CONTRACT = {
    "webapp": ("Dockerfile", "main.py", "requirements.txt"),
    "api": ("Dockerfile", "main.py", "requirements.txt", "tests/smoke.py"),
    "static": ("Dockerfile", "site/index.html", "tests/smoke.py"),
}


def check_templates(templates_dir: Path) -> Check:
    missing = [f"{stack}/{rel}" for stack, files in _TEMPLATE_CONTRACT.items()
               for rel in files if not (templates_dir / stack / rel).is_file()]
    if missing:
        return Check("templates", FAIL, "fichiers manquants : " + ", ".join(missing),
                     "restaurer depuis git (git checkout -- templates/)")
    return Check("templates", OK, f"{len(_TEMPLATE_CONTRACT)} stacks complets")


def check_db_access() -> Check:
    try:
        runs = db.list_runs()
    except Exception as e:  # noqa: BLE001 — tout échec DB doit remonter tel quel
        return Check("base SQLite", FAIL, f"{type(e).__name__}: {e}",
                     "vérifier data/usine.db (verrou, corruption)")
    return Check("base SQLite", OK, f"{len(runs)} run(s) en historique")


def check_playwright(browsers_path: Path | None = None) -> Check:
    path = browsers_path or Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright"))
    chromium = sorted(path.glob("chromium*")) if path.is_dir() else []
    if not chromium:
        return Check("Chromium Playwright", FAIL, f"absent de {path}",
                     "rebuild de l'image (playwright install chromium)")
    return Check("Chromium Playwright", OK, chromium[-1].name)


def check_ca_certs(certs_dir: Path = Path("/usr/local/share/ca-certificates")) -> Check:
    certs = sorted(p.name for p in certs_dir.glob("*.crt")) if certs_dir.is_dir() else []
    if not certs:
        return Check("CA d'entreprise", FAIL, "aucun .crt embarqué — le proxy TLS bloquera "
                     "les appels API", "réexporter les CA (README « Exploitation ») puis rebuild")
    return Check("CA d'entreprise", OK, ", ".join(certs))


def check_cli_config(config_dir: Path | None = None) -> Check:
    """Sans .claude.json le CLI refuse de démarrer dès lors que des backups existent."""
    cfg_dir = config_dir or Path(os.environ.get("CLAUDE_CONFIG_DIR",
                                                str(Path.home() / ".claude")))
    name = "config CLI (.claude.json)"
    if (cfg_dir / ".claude.json").is_file():
        return Check(name, OK, "présente (persistée dans le volume)")
    if list(cfg_dir.glob("backups/.claude.json.backup.*")):
        return Check(name, FAIL, "absente alors que des backups existent — le CLI "
                     "refusera de démarrer",
                     "passer par l'entrypoint (restaure le dernier backup) ou copier "
                     "un backup de data/claude/backups/ vers data/claude/.claude.json")
    return Check(name, WARN, "absente — sera créée au premier appel du CLI")


def check_sdk() -> Check:
    try:
        import claude_agent_sdk
    except ImportError as e:  # image incomplète
        return Check("SDK claude-agent", FAIL, str(e), "rebuild de l'image (requirements.txt)")
    return Check("SDK claude-agent", OK, f"v{getattr(claude_agent_sdk, '__version__', '?')}")


# ── checks Docker (async) ───────────────────────────────────────────


async def check_daemon() -> Check:
    code, out = await _docker("info", "--format", "{{.ServerVersion}}", timeout=30)
    if code != 0:
        return Check("daemon Docker", FAIL, out.strip()[:200] or "injoignable",
                     "démarrer Docker Desktop")
    return Check("daemon Docker", OK, f"v{out.strip()}")


async def check_network() -> Check:
    code, _ = await _docker("network", "inspect", settings.docker_network, timeout=30)
    detail = "présent" if code == 0 else "absent — créé automatiquement au prochain run"
    return Check(f"réseau {settings.docker_network}", OK, detail)


async def check_docker_space() -> Check:
    code, out = await _docker("system", "df", "--format", "{{json .}}", timeout=60)
    if code != 0:
        return Check("espace Docker", WARN, out.strip()[:200])
    parts = []
    for line in out.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("Type") in ("Images", "Build Cache"):
            parts.append(f"{row['Type'].lower()} {row.get('Size', '?')} "
                         f"(réclamable {row.get('Reclaimable', '?')})")
    return Check("espace Docker", OK, " · ".join(parts) or "?")


async def check_claude_cli() -> Check:
    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "--version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 30)
    except (OSError, TimeoutError) as e:
        return Check("CLI claude", FAIL, f"introuvable ou muet ({e})",
                     "rebuild de l'image (npm install -g @anthropic-ai/claude-code)")
    version = out.decode(errors="replace").strip()
    if proc.returncode != 0:
        return Check("CLI claude", FAIL, f"exit {proc.returncode} : {version[:200]}",
                     "rebuild de l'image")
    return Check("CLI claude", OK, version[:80])


# ── commandes ───────────────────────────────────────────────────────


def _credentials_path() -> Path:
    return Path.home() / ".claude" / ".credentials.json"


async def run_doctor() -> int:
    """Diagnostic complet, affiché check par check avec remèdes. Exit 1 si bloquant."""
    checks = [
        await check_daemon(),
        await check_network(),
        check_disk("/", "disque VM Docker", settings.vm_disk_warn_gb,
                   settings.vm_disk_min_gb, _REMEDY_VM_DISK),
        check_disk(settings.output_dir, "disque hôte (output/)", settings.host_disk_warn_gb,
                   settings.host_disk_min_gb, _REMEDY_HOST_DISK),
        await check_docker_space(),
        check_credentials(_credentials_path()),
        check_cli_config(),
        await check_claude_cli(),
        check_sdk(),
        check_templates(Path(settings.templates_dir)),
        check_db_access(),
        check_playwright(),
        check_ca_certs(),
    ]
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"{_ICONS[c.level]} {c.name:<{width}}  {c.detail}")
        if c.remedy and c.level != OK:
            print(f"{'':<{width + 3}}└ remède : {c.remedy}")
    fails = sum(c.level == FAIL for c in checks)
    warns = sum(c.level == WARN for c in checks)
    print()
    if fails:
        print(f"❌ {fails} problème(s) bloquant(s), {warns} avertissement(s) — "
              "l'usine ne doit pas lancer de runs en l'état.")
        return 1
    if warns:
        print(f"⚠️ opérationnelle, avec {warns} avertissement(s) à surveiller.")
        return 0
    print("✅ tout est opérationnel.")
    return 0


async def preflight(logger) -> None:
    """Garde-fous minimaux avant de dépenser des tokens. Lève UsineNotReady si bloquant."""
    checks = [
        await check_daemon(),
        check_disk(settings.output_dir, "disque hôte (output/)", settings.host_disk_warn_gb,
                   settings.host_disk_min_gb, _REMEDY_HOST_DISK),
        check_disk("/", "disque VM Docker", settings.vm_disk_warn_gb,
                   settings.vm_disk_min_gb, _REMEDY_VM_DISK),
        check_credentials(_credentials_path()),
        check_cli_config(),
    ]
    fails = []
    for c in checks:
        if c.level == FAIL:
            fails.append(f"{c.name} : {c.detail}" + (f" → {c.remedy}" if c.remedy else ""))
        elif c.level == WARN:
            logger.warning("⚠️ préflight — %s : %s", c.name, c.detail)
    if fails:
        raise UsineNotReady("; ".join(fails) + " — diagnostic complet : commande `doctor`")
