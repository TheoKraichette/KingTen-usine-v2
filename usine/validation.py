"""Validation mécanique d'une app générée — aucun jugement de Claude.

Étapes : docker build → docker run (limité, labellisé) → healthcheck strict
→ smoke HTTP génériques (toutes les routes déclarées) → parcours critiques
(tests/smoke.py de l'app, avec comptage des checks) → restart + re-smoke
(persistance) → nettoyage systématique.
"""

import asyncio
import os
import re
import signal
import sys
import time
from pathlib import Path

import httpx

from usine.config import settings
from usine.state import RunState

_DETAIL_MAX = 4000  # taille max d'un détail d'échec injecté dans le prompt de fix

# Motifs d'échec d'INFRASTRUCTURE (pas un bug de l'app → ne pas lancer la boucle fix)
_INFRA_PATTERNS = (
    "Cannot connect to the Docker daemon",
    "error during connect",
    "no space left on device",
    "network usine-net not found",
    "could not find an available",
    "toomanyrequests",
)


class ValidationFailed(RuntimeError):
    """Levée quand la validation échoue encore après le dernier cycle de fix."""


_CHECK_LINE = re.compile(r"^(?:OK|FAIL)\b", re.MULTILINE)


def count_checks(output: str) -> int:
    """Nombre de lignes OK/FAIL imprimées par tests/smoke.py (contrat : une par check)."""
    return len(_CHECK_LINE.findall(output))


async def _docker(*args: str, timeout: int = 300) -> tuple[int, str]:
    """Exécute une commande docker, retourne (code, sortie combinée). 124 = timeout."""
    proc = await asyncio.create_subprocess_exec(
        "docker", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, f"timeout après {timeout}s: docker {' '.join(args)}"
    return proc.returncode or 0, out.decode("utf-8", errors="replace")


def _tail(text: str, limit: int = _DETAIL_MAX) -> str:
    return text[-limit:] if len(text) > limit else text


def _is_infra_failure(code: int, detail: str) -> bool:
    return code == 124 or any(p in detail for p in _INFRA_PATTERNS)


def container_name(run_id: str) -> str:
    return f"usine-app-{run_id}"


def image_tag(run_id: str) -> str:
    return f"usine-app-{run_id}:latest"


async def cleanup(run_id: str, *, remove_image: bool = False) -> None:
    await _docker("rm", "-f", container_name(run_id), timeout=60)
    if remove_image:
        await _docker("rmi", "-f", image_tag(run_id), timeout=60)


async def ensure_infra(logger) -> None:
    """Pré-flight : daemon joignable + réseau usine-net présent. Échec = erreur d'usine."""
    code, out = await _docker("info", "--format", "{{.ServerVersion}}", timeout=30)
    if code != 0:
        raise RuntimeError(f"infra: daemon Docker injoignable — {out.strip()[:300]}")
    code, _ = await _docker("network", "inspect", settings.docker_network, timeout=30)
    if code != 0:
        code, out = await _docker("network", "create", settings.docker_network, timeout=30)
        if code != 0 and "already exists" not in out:
            raise RuntimeError(
                f"infra: réseau {settings.docker_network} indisponible — {out.strip()[:300]}"
            )
        logger.info("🔌 réseau %s créé", settings.docker_network)


def _run_is_active(run_id: str, threshold: int = 600) -> bool:
    """Un run est actif si son run.log a bougé récemment (heartbeat toutes les 30-120 s)."""
    log = Path(settings.output_dir) / f"run_{run_id}" / "run.log"
    return log.exists() and (time.time() - log.stat().st_mtime) < threshold


async def sweep_orphans(logger) -> None:
    """Supprime les conteneurs/images usine-app-* dont le run n'est plus actif (kill dur passé)."""
    code, out = await _docker("ps", "-a", "--filter", "name=usine-app-",
                              "--format", "{{.Names}}", timeout=60)
    if code == 0:
        for name in out.split():
            run_id = name.removeprefix("usine-app-")
            if not _run_is_active(run_id):
                logger.info("🧹 orphelin balayé : conteneur %s", name)
                await _docker("rm", "-f", name, timeout=60)
    code, out = await _docker("images", "usine-app-*", "--format", "{{.Repository}}", timeout=60)
    if code == 0:
        for repo in set(out.split()):
            run_id = repo.removeprefix("usine-app-")
            if not _run_is_active(run_id):
                logger.info("🧹 orphelin balayé : image %s", repo)
                await _docker("rmi", "-f", f"{repo}:latest", timeout=60)


async def validate_app(state: RunState, cycle: int, logger) -> dict:
    """Un cycle complet de validation. Retourne le rapport (dict sérialisable)."""
    t0 = time.time()
    stages: list[dict] = []
    infra_error = False
    name = container_name(state.run_id)
    base_url = f"http://{name}:{settings.app_port}"

    def stage(stage_name: str, passed: bool, detail: str = "") -> bool:
        stages.append({"name": stage_name, "passed": passed, "detail": _tail(detail)})
        logger.info("%s %s%s", "✅" if passed else "❌", stage_name,
                    "" if passed else f" — {detail[:160]}")
        return passed

    async def _fail_report() -> dict:
        _, logs = await _docker("logs", "--tail", "120", name, timeout=60)
        return _report(False, cycle, stages, logs, t0, infra_error)

    try:
        # 1. docker build
        logger.info("🐳 validation #%d — docker build...", cycle)
        code, out = await _docker(
            "build", "-t", image_tag(state.run_id), str(state.code_dir), timeout=600
        )
        if code != 0:
            infra_error = _is_infra_failure(code, out)
            stage("docker build", False, out)
            return _report(False, cycle, stages, "", t0, infra_error)
        stage("docker build", True)

        # 2. docker run (labellisé + limité) + healthcheck strict
        await _docker("rm", "-f", name, timeout=60)
        code, out = await _docker(
            "run", "-d", "--name", name,
            "--network", settings.docker_network,
            "--label", "usine.app=1", "--label", f"usine.run={state.run_id}",
            "--memory", "512m", "--cpus", "1", "--pids-limit", "256",
            image_tag(state.run_id),
            timeout=120,
        )
        if code != 0:
            infra_error = _is_infra_failure(code, out)
            stage("docker run", False, out)
            return _report(False, cycle, stages, "", t0, infra_error)
        stage("docker run", True)

        healthy, detail = await _wait_healthy(name, base_url, state.stack)
        if not stage("healthcheck", healthy, detail):
            return await _fail_report()

        # 3. smoke HTTP génériques (toutes les routes déclarées dans la spec)
        ok, detail = await _generic_smoke(state, base_url)
        if not stage("smoke HTTP génériques", ok, detail):
            return await _fail_report()

        # 4. parcours critiques (tests/smoke.py écrit par l'agent dev, checks comptés)
        ok, detail = await _app_smoke(state, base_url, logger)
        if not stage("parcours critiques (tests/smoke.py)", ok, detail):
            return await _fail_report()

        # 5. persistance : restart du conteneur puis re-smoke
        code, out = await _docker("restart", name, timeout=90)
        if code != 0:
            stage("persistance (restart conteneur)", False, out)
            return await _fail_report()
        healthy, detail = await _wait_healthy(name, base_url, state.stack)
        if healthy:
            ok, detail = await _generic_smoke(state, base_url)
        else:
            ok = False
        if not stage("persistance (restart conteneur)", ok and healthy, detail):
            return await _fail_report()

        return _report(True, cycle, stages, "", t0, False)

    finally:
        # 6. nettoyage systématique du conteneur de test
        await _docker("rm", "-f", name, timeout=60)


def _report(passed: bool, cycle: int, stages: list[dict], docker_logs_tail: str,
            t0: float, infra_error: bool) -> dict:
    return {
        "passed": passed,
        "cycle": cycle,
        "stages": stages,
        "docker_logs_tail": _tail(docker_logs_tail, 3000),
        "duration_s": round(time.time() - t0, 1),
        "infra_error": infra_error,
    }


async def _wait_healthy(name: str, base_url: str, stack: str | None) -> tuple[bool, str]:
    """Attend un conteneur sain. Contrat STRICT : /health → 200 (webapp/api), / → 200 (static)."""
    probe_path = "/" if stack == "static" else "/health"
    deadline = time.time() + settings.healthcheck_timeout
    last = "inconnu"
    while time.time() < deadline:
        code, out = await _docker(
            "inspect", "-f",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} {{.State.Status}}",
            name, timeout=30,
        )
        if code != 0:
            return False, f"conteneur disparu: {out.strip()}"
        health, status = (out.strip().split() + ["?"])[:2]
        last = f"health={health} status={status}"
        if status == "exited":
            return False, f"le conteneur s'est arrêté ({last})"
        if status == "running" and health in ("healthy", "none"):
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    r = await client.get(base_url + probe_path)
                    if r.status_code == 200:
                        return True, ""
                    last = f"GET {probe_path} → {r.status_code} (200 exigé)"
            except httpx.HTTPError as e:
                last = f"GET {probe_path} inaccessible: {e}"
        await asyncio.sleep(2)
    return False, f"pas sain après {settings.healthcheck_timeout}s ({last})"


async def _generic_smoke(state: RunState, base_url: str) -> tuple[bool, str]:
    """Sonde TOUTES les routes déclarées dans la spec, selon leur nature.

    - GET publique sans paramètre : < 500 et ≠ 404 ; « / » doit répondre 2xx.
    - GET auth : 200/3xx/401/403 acceptés, 404/405/5xx = échec.
    - non-GET (corps vide) : 400/401/403/422/2xx/3xx prouvent que la route existe ;
      404/405/5xx = échec (route déclarée mais absente ou qui crashe).
    - paramètres {x} substitués par « 1 » : seul un 5xx est éliminatoire.
    """
    failures: list[str] = []
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        # « / » doit servir quelque chose
        try:
            r = await client.get(base_url + "/")
            if not (200 <= r.status_code < 300):
                failures.append(f"GET / → {r.status_code} (2xx attendu)\n{r.text[:300]}")
        except httpx.HTTPError as e:
            failures.append(f"GET / → erreur réseau: {e}")

        for route in (state.spec or {}).get("routes", []):
            method = route.get("method", "GET").upper()
            path = route.get("path", "")
            if not path.startswith("/") or path == "/":
                continue
            has_param = "{" in path
            probe_path = re.sub(r"\{[^}]+\}", "1", path)
            auth = bool(route.get("auth_required"))
            try:
                r = await client.request(method, base_url + probe_path)
            except httpx.HTTPError as e:
                failures.append(f"{method} {path} → erreur réseau: {e}")
                continue
            s = r.status_code
            if has_param:
                if s >= 500:
                    failures.append(f"{method} {path} (sonde {probe_path}) → {s}\n{r.text[:300]}")
            elif method == "GET" and not auth:
                if s >= 500 or s == 404:
                    failures.append(f"GET {path} → {s} (route déclarée: 404/5xx interdits)\n"
                                    f"{r.text[:200]}")
            else:
                if s >= 500 or s in (404, 405):
                    failures.append(
                        f"{method} {path} → {s} (la route déclarée doit exister : "
                        f"400/401/403/422 acceptés, pas {s})\n{r.text[:200]}"
                    )
    return (not failures, "\n\n".join(failures))


async def _app_smoke(state: RunState, base_url: str, logger) -> tuple[bool, str]:
    """Lance tests/smoke.py (contrat : argv[1]=base_url, exit 0, une ligne OK/FAIL par check).

    Garde-fous anti-tests-faibles : nombre minimal de checks (2 + parcours critiques),
    et interdiction de diminuer le nombre de checks entre deux validations.
    """
    script = state.code_dir / "tests" / "smoke.py"
    if not script.is_file():
        return False, "tests/smoke.py absent — l'agent dev devait l'écrire (contrat du template)"
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(script), base_url,
        cwd=str(state.code_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,  # groupe de processus dédié : on peut tuer Chromium avec
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), settings.timeout_validate)
    except TimeoutError:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        await proc.wait()
        return False, f"tests/smoke.py: timeout après {settings.timeout_validate}s"
    output = out.decode("utf-8", errors="replace")
    logger.debug("smoke.py sortie:\n%s", output)

    checks = count_checks(output)
    min_checks = 2 + len((state.spec or {}).get("critical_paths", []))
    if proc.returncode != 0:
        return False, f"tests/smoke.py exit {proc.returncode}\n{output}"
    if checks < min_checks:
        return False, (
            f"tests/smoke.py trop faible : {checks} check(s) exécuté(s), minimum {min_checks} "
            f"(2 génériques + {min_checks - 2} parcours critiques). Chaque vérification doit "
            f"imprimer une ligne OK/FAIL via check().\n{output[-1500:]}"
        )
    if state.smoke_checks and checks < state.smoke_checks:
        return False, (
            f"le nombre de checks a DIMINUÉ ({state.smoke_checks} → {checks}) — affaiblir les "
            f"tests est interdit : restaure les vérifications supprimées et corrige l'app."
        )
    state.smoke_checks = checks
    state.save()
    return True, ""
