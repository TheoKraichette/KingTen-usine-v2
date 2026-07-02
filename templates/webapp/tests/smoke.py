"""Tests smoke de l'app — contrat usine v2.

Usage : python tests/smoke.py <base_url>
Exit 0 si tout passe, 1 sinon, en imprimant clairement chaque échec.
`httpx` et `playwright` (chromium) sont disponibles dans l'environnement d'exécution.

L'AGENT DEV DOIT :
- conserver les checks génériques ci-dessous
- remplacer critical_paths() par un test réel de CHAQUE parcours critique de spec.json
"""

import sys

import httpx

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("OK   " if ok else "FAIL ") + name + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def launch_browser(p):
    """Lance chromium avec les flags requis pour tourner en conteneur."""
    return p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])


def generic_checks(base: str) -> None:
    r = httpx.get(base + "/health", timeout=10)
    check("GET /health == 200", r.status_code == 200, f"reçu {r.status_code}")
    r = httpx.get(base + "/", timeout=10, follow_redirects=True)
    check("GET / == 200", r.status_code == 200, f"reçu {r.status_code}")

    # La page d'accueil doit charger sans erreur JS (htmx, scripts de l'app...)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = launch_browser(p)
        page = browser.new_page()
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/", wait_until="networkidle")
        check("la page d'accueil charge sans erreur JS", not errors, "; ".join(errors)[:300])
        browser.close()


def critical_paths(base: str) -> None:
    """Un test par parcours critique de spec.json. Exemple de structure Playwright :

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = launch_browser(p)
        page = browser.new_page()
        page.goto(base + "/signup")
        page.fill("#email", "smoke@test.fr")
        page.fill("#password", "Secret123!")
        page.click("#btn-signup")
        page.wait_for_url(base + "/dashboard")
        check("signup aboutit au dashboard", "/dashboard" in page.url)
        browser.close()
    """
    raise NotImplementedError(
        "l'agent dev doit implémenter ici les parcours critiques de spec.json"
    )


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python tests/smoke.py <base_url>")
        return 2
    base = sys.argv[1].rstrip("/")
    try:
        generic_checks(base)
        critical_paths(base)
    except Exception as e:  # noqa: BLE001
        check(f"exception {type(e).__name__}", False, str(e)[:300])
    if FAILURES:
        print(f"\n{len(FAILURES)} échec(s) : {', '.join(FAILURES)}")
        return 1
    print("\nTous les tests passent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
