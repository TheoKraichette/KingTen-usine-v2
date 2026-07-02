"""Tests smoke de l'API — contrat usine v2.

Usage : python tests/smoke.py <base_url>
Exit 0 si tout passe, 1 sinon, en imprimant clairement chaque échec.
`httpx` est disponible dans l'environnement d'exécution.

L'AGENT DEV DOIT :
- conserver les checks génériques ci-dessous
- remplacer critical_paths() par un test réel de CHAQUE parcours critique de spec.json
  (séquences d'appels HTTP avec assertions sur statuts et contenus)
"""

import sys

import httpx

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("OK   " if ok else "FAIL ") + name + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def generic_checks(base: str) -> None:
    r = httpx.get(base + "/health", timeout=10)
    check("GET /health == 200", r.status_code == 200, f"reçu {r.status_code}")
    r = httpx.get(base + "/", timeout=10)
    check("GET / == 200", r.status_code == 200, f"reçu {r.status_code}")


def critical_paths(base: str) -> None:
    """Un test par parcours critique de spec.json. Exemple de structure :

    with httpx.Client(base_url=base, timeout=10) as client:
        r = client.post("/items", json={"name": "test"})
        check("POST /items == 201", r.status_code == 201, f"reçu {r.status_code}")
        item_id = r.json()["id"]
        r = client.get(f"/items/{item_id}")
        check("GET /items/{id} retourne l'item", r.json().get("name") == "test")
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
